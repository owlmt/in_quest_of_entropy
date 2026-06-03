#!/usr/bin/env python3
"""
code1_pseudoentropy.py  --  pseudoentropy and its REAL min-entropy.

A generator seeded with SEED_BITS of genuine randomness, expanded by SHAKE-256
(FIPS 202) -- the extendable-output function inside ML-KEM and ML-DSA
(FIPS 203/204). To any observer the output looks like full entropy. But:

    real min-entropy of the WHOLE stream  =  SEED_BITS   (data-processing bound)
                                          =  log2(seed-recovery cost)
    the maker, who knows the seed, predicts every byte  (real accessible entropy 0)

This is HILL pseudoentropy: computationally indistinguishable from high-entropy
randomness while its real entropy is only SEED_BITS.

Refs: [HILL99] Hastad, Impagliazzo, Levin, Luby, SICOMP 1999.
      [FIPS202] NIST SHA-3 / SHAKE.  [FIPS203] ML-KEM.
"""

import hashlib
import secrets

SEED_BITS = 32          # the real entropy; ENISA Note 72 asks for >= 188
N = 1_000_000


def stream(n, seed):
    return hashlib.shake_256(seed).digest(n)


if __name__ == "__main__":
    seed = secrets.randbits(SEED_BITS).to_bytes((SEED_BITS + 7) // 8, "big")
    y = stream(N, seed)
    open("pseudo_sample.bin", "wb").write(y)

    print(f"real min-entropy of the whole {N//1000} KB stream : {SEED_BITS} bits")
    print(f"  = seed-recovery work factor                    : 2^{SEED_BITS}")
    print(f"  ENISA Note 72 (PQC) asks for                   : >= 188 bits")
    print(f"maker predicts every byte (knows the seed)       : {stream(N, seed) == y}")
    print("wrote pseudo_sample.bin")
