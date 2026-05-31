#!/usr/bin/env python3
"""
linux_rng.py - User-space model of the Linux kernel RNG output path.

Mirrors the architecture documented in the BSI LinuxRNG report and
drivers/char/random.c (kernel 5.18+):

    noise source(s)  ->  input_pool (BLAKE2s)  ->  extract_entropy
                                                        |
                                                   ChaCha20 crng
                                                   (fast-key-erasure)
                                                        |
                                                     output

The point of this script is to show that the OUTPUT of this pipeline is
the output of a cryptographic DRBG (ChaCha20), and therefore looks like
high-quality random data regardless of how much entropy the input had.

Two modes:
  --from-jitter FILE   seed the input_pool from collected jitter samples
                       (the realistic Linux case)
  --fixed-seed         seed the input_pool from a fixed, published value
                       (the backdoor case: byte-for-byte reproducible,
                        zero real entropy, but identical statistics)

Both modes produce output that passes SP 800-90B with near-maximum
min-entropy. Only the second is predictable. 90B cannot tell them apart.
"""

import hashlib
import struct
import argparse
import sys
import time

# ---------------------------------------------------------------
# ChaCha20 block function (RFC 8439), pure Python.
# Linux uses ChaCha20 for the crng with fast-key-erasure.
# ---------------------------------------------------------------

def _rotl32(x, n):
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF

def _qr(s, a, b, c, d):
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF; s[d] ^= s[a]; s[d] = _rotl32(s[d], 16)
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF; s[b] ^= s[c]; s[b] = _rotl32(s[b], 12)
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF; s[d] ^= s[a]; s[d] = _rotl32(s[d], 8)
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF; s[b] ^= s[c]; s[b] = _rotl32(s[b], 7)

def chacha20_block(key32, counter, nonce12):
    const = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574]
    k = list(struct.unpack('<8I', key32))
    n = list(struct.unpack('<3I', nonce12))
    state = const + k + [counter & 0xFFFFFFFF] + n
    w = state[:]
    for _ in range(10):           # 20 rounds = 10 double-rounds
        _qr(w, 0, 4, 8, 12); _qr(w, 1, 5, 9, 13)
        _qr(w, 2, 6, 10, 14); _qr(w, 3, 7, 11, 15)
        _qr(w, 0, 5, 10, 15); _qr(w, 1, 6, 11, 12)
        _qr(w, 2, 7, 8, 13);  _qr(w, 3, 4, 9, 14)
    out = [(w[i] + state[i]) & 0xFFFFFFFF for i in range(16)]
    return struct.pack('<16I', *out)


# ---------------------------------------------------------------
# Linux RNG model
# ---------------------------------------------------------------

class LinuxRNG:
    """
    Models input_pool (BLAKE2s) + ChaCha20 crng (fast-key-erasure).
    Simplified but architecturally faithful: the output is a ChaCha20
    keystream keyed by a BLAKE2s digest of accumulated noise.
    """

    def __init__(self):
        # input_pool is a BLAKE2s state into which noise is mixed.
        self._pool = hashlib.blake2s(digest_size=32)
        self._key = b'\x00' * 32       # crng key
        self._counter = 0
        self._nonce = b'\x00' * 12

    def mix_pool_bytes(self, data: bytes):
        """drivers/char/random.c: mix_pool_bytes() -> BLAKE2s update."""
        self._pool.update(data)

    def extract_entropy(self) -> bytes:
        """
        extract_entropy(): hash the pool to a 32-byte seed and feed it back
        so the pool keeps evolving (Linux re-mixes the hash output).
        """
        seed = self._pool.copy().digest()
        self._pool.update(seed)
        return seed

    def crng_reseed(self):
        """Install a fresh crng key from the input_pool."""
        self._key = self.extract_entropy()
        self._counter = 0

    def get_random_bytes(self, n: int) -> bytes:
        """
        ChaCha20 keystream with fast-key-erasure: after each request, the
        first 32 bytes of fresh keystream become the next key, so prior
        output cannot be recomputed (backtracking resistance).
        """
        out = bytearray()
        while len(out) < n:
            out.extend(chacha20_block(self._key, self._counter, self._nonce))
            self._counter += 1
        result = bytes(out[:n])
        # fast key erasure
        ke = chacha20_block(self._key, self._counter, self._nonce)
        self._key = ke[:32]
        self._counter = 0
        return result


FIXED_SEED_HEX = (
    "deadbeefcafebabe0011223344556677"
    "8899aabbccddeeff0102030405060708"
)


def build_from_jitter(jitter_path: str) -> LinuxRNG:
    with open(jitter_path, 'rb') as f:
        noise = f.read()
    rng = LinuxRNG()
    # Feed all collected noise into the pool, the way add_*_randomness
    # accumulates interrupt/jitter samples over time.
    rng.mix_pool_bytes(noise)
    rng.crng_reseed()
    return rng


def build_fixed(seed_hex: str) -> LinuxRNG:
    rng = LinuxRNG()
    rng.mix_pool_bytes(bytes.fromhex(seed_hex))
    rng.crng_reseed()
    return rng


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument('-n', '--bytes', type=int, default=1_000_000,
                   help='Output bytes to generate')
    p.add_argument('-o', '--output', default='linux_output.bin')
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument('--from-jitter', metavar='FILE',
                     help='Seed input_pool from a jitter samples file')
    src.add_argument('--fixed-seed', action='store_true',
                     help='Seed input_pool from a fixed published value (backdoor demo)')
    p.add_argument('--seed-hex', default=FIXED_SEED_HEX,
                   help='Fixed seed hex (with --fixed-seed)')
    args = p.parse_args()

    if args.from_jitter:
        print(f"# Linux RNG model, input_pool seeded from jitter: {args.from_jitter}")
        rng = build_from_jitter(args.from_jitter)
        reproducible = False
    else:
        print(f"# Linux RNG model, input_pool seeded from FIXED value")
        print(f"# Published seed (hex): {args.seed_hex}")
        rng = build_fixed(args.seed_hex)
        reproducible = True

    t0 = time.perf_counter()
    data = rng.get_random_bytes(args.bytes)
    elapsed = time.perf_counter() - t0

    with open(args.output, 'wb') as f:
        f.write(data)

    print(f"# Output: {len(data):,} bytes via ChaCha20 crng in {elapsed:.2f}s")
    print(f"# Predictable: {'YES (fixed seed, fully reproducible)' if reproducible else 'no (real jitter seed)'}")
    print(f"# Next: python3 nist_90b_tests.py {args.output}")


if __name__ == '__main__':
    main()
