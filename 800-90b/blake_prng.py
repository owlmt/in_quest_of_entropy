#!/usr/bin/env python3
"""
blake_prng.py - Deterministic pseudorandom source from BLAKE2b in counter mode.

Generates a sample stream that is byte-for-byte reproducible from a
printed key, yet should pass the SP 800-90B IID battery and yield
near-maximum min-entropy estimates from the predictor estimators.

This is the analog of the AES-256-CTR demonstration in
github.com/owlmt/ais31-full-evaluation but with BLAKE2b, which is
particularly pointed because BLAKE2s is the Linux kernel's `input_pool`
compression primitive (see drivers/char/random.c). Demonstrating that
*the very function the kernel uses to condition entropy* can be turned
into a backdoored generator indistinguishable from a real RNG under
black-box testing is the AIS 31 §4.6.2 point in operational form:
statistical tests can falsify a stochastic model but never verify it.

Construction:
    seed       = published key (32 bytes, printed in the output header)
    block_i    = BLAKE2b(key=seed, msg=i.to_bytes(8,'big'), digest_size=64)
    output     = concatenation of block_0, block_1, ...
    sample[j]  = output[j] & mask   (low `bits` bits of byte j)

This is "PRG-as-RNG" -- the same anti-pattern as Dual_EC_DRBG output
fed through a 90B pipeline, except here the backdoor is the seed itself
being public knowledge.
"""

import hashlib
import argparse
import sys
import time
import math
import collections


PRINTED_KEY_HEX = (
    "000102030405060708090a0b0c0d0e0f"
    "101112131415161718191a1b1c1d1e1f"
)


def generate(key: bytes, n_samples: int, bits_per_sample: int = 8) -> bytes:
    """
    Produce n_samples bytes using BLAKE2b in counter mode keyed by `key`.
    Each output byte holds the low `bits_per_sample` bits of one stream byte.
    """
    if not (1 <= bits_per_sample <= 8):
        raise ValueError("bits_per_sample must be in [1,8]")
    mask = (1 << bits_per_sample) - 1
    out = bytearray(n_samples)
    counter = 0
    pos = 0

    while pos < n_samples:
        h = hashlib.blake2b(key=key, digest_size=64)
        h.update(counter.to_bytes(8, "big"))
        block = h.digest()
        for b in block:
            if pos >= n_samples:
                break
            out[pos] = b & mask
            pos += 1
        counter += 1

    return bytes(out)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("-n", "--samples", type=int, default=1_000_000)
    p.add_argument("-b", "--bits", type=int, default=8)
    p.add_argument("-k", "--key", default=PRINTED_KEY_HEX,
                   help="32-byte hex key (default: published demonstration key)")
    p.add_argument("-o", "--output", default="blake_samples.bin")
    args = p.parse_args()

    key = bytes.fromhex(args.key)
    if len(key) > 64:
        print(f"WARNING: BLAKE2b key truncated to 64 bytes", file=sys.stderr)
        key = key[:64]

    print(f"# Source: deterministic BLAKE2b counter mode")
    print(f"# Printed key (hex, {len(key)} bytes):")
    print(f"#   {key.hex()}")
    print(f"# Counter starts at 0, big-endian 8 bytes")
    print(f"# Output = (block_i bytes) & 0x{(1 << args.bits) - 1:02x}  (low {args.bits} bits)")
    print(f"# Target: {args.samples:,} samples -> {args.output}")

    t0 = time.perf_counter()
    data = generate(key, args.samples, args.bits)
    elapsed = time.perf_counter() - t0

    with open(args.output, "wb") as f:
        f.write(data)

    print(f"# Done: {len(data):,} bytes in {elapsed:.3f}s "
          f"({args.samples/elapsed:,.0f} samples/s)")

    counts = collections.Counter(data)
    p_max = max(counts.values()) / len(data)
    print(f"#")
    print(f"# Distinct values used:  {len(counts)} / {2**args.bits}")
    print(f"# Most common freq:      {p_max:.4f}")
    print(f"# Naive MCV min-entropy: {-math.log2(p_max):.4f} bits/sample")
    print(f"#")
    print(f"# Verify reproducibility:")
    print(f"#   python3 predict.py {args.output} --key {args.key} --bits {args.bits}")


if __name__ == "__main__":
    main()
