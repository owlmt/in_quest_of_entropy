#!/usr/bin/env python3
"""
predict.py - Reproduce a BLAKE2b-CTR sample stream from its printed key.

Analog of predict_streamB.py in the AIS 31 self-assessment repo. Given
the printed key and stream parameters, regenerate the sample bytes and
verify they match the file produced by blake_prng.py byte-for-byte.

Success of this script is the operational proof that the stream is
fully predictable -- yet the same stream passes the 90B black-box
battery with near-maximum min-entropy estimates.
"""

import hashlib
import argparse
import sys


def reproduce(key: bytes, n_samples: int, bits_per_sample: int) -> bytes:
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
    p.add_argument("input", help="Sample file from blake_prng.py")
    p.add_argument("-k", "--key", required=True, help="Hex key (the printed key)")
    p.add_argument("-b", "--bits", type=int, default=8)
    args = p.parse_args()

    with open(args.input, "rb") as f:
        actual = f.read()

    key = bytes.fromhex(args.key)
    if len(key) > 64:
        key = key[:64]

    predicted = reproduce(key, len(actual), args.bits)

    if predicted == actual:
        print(f"OK: predicted {len(actual):,} bytes match {args.input} exactly.")
        print(f"    First 32 bytes: {actual[:32].hex()}")
        print(f"    This stream is byte-for-byte reproducible from the printed key.")
        sys.exit(0)
    else:
        # Show first mismatch
        for i, (a, b) in enumerate(zip(actual, predicted)):
            if a != b:
                print(f"MISMATCH at byte {i}: file=0x{a:02x} predicted=0x{b:02x}")
                break
        sys.exit(1)


if __name__ == "__main__":
    main()
