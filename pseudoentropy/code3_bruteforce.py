#!/usr/bin/env python3
"""
code3_bruteforce.py  --  recover the seed from the output, in reasonable time.

The pseudoentropy stream (code1) passes SP 800-90B at ~7.14 bits/byte, yet its
whole content unfolds from one small seed. An outside adversary who never saw
the seed recovers it by parallel exhaustion against the first 16 output bytes,
then reconstructs the entire stream. Recovery cost = the REAL min-entropy.

Usage:  python3 code3_bruteforce.py [target_file] [seed_bits]
Default: pseudo_sample.bin 32
"""

import hashlib
import multiprocessing as mp
import sys
import time

CRIB_LEN = 16


def scan(args):
    lo, hi, nbytes, crib = args
    sh = hashlib.shake_256
    clen = len(crib)
    for s in range(lo, hi):
        if sh(s.to_bytes(nbytes, "big")).digest(clen) == crib:
            return s
    return None


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "pseudo_sample.bin"
    seed_bits = int(sys.argv[2]) if len(sys.argv) > 2 else 32
    nbytes = (seed_bits + 7) // 8
    data = open(target, "rb").read()
    crib = data[:CRIB_LEN]
    space = 1 << seed_bits
    ncpu = mp.cpu_count()
    chunk = max(1 << 16, space // (ncpu * 64))
    chunks = [(lo, min(lo + chunk, space), nbytes, crib)
              for lo in range(0, space, chunk)]

    print(f"target {target}: recover <= {seed_bits}-bit SHAKE-256 seed "
          f"from {CRIB_LEN} observed bytes")
    print(f"search 2^{seed_bits} = {space:,}   workers {ncpu}")

    t0 = time.time()
    found = None
    with mp.Pool(ncpu) as pool:
        for r in pool.imap_unordered(scan, chunks):
            if r is not None:
                found = r
                pool.terminate()
                break
    dt = time.time() - t0

    if found is None:
        print(f"seed NOT found within 2^{seed_bits} (wall {dt:.1f}s)")
        return
    full = hashlib.shake_256(found.to_bytes(nbytes, "big")).digest(len(data))
    print(f"recovered seed       : {found}")
    print(f"wall time            : {dt:.1f} s")
    print(f"stream reconstructed : {full == data}  ({len(data):,} bytes)")
    # leave an evidence file for the post
    with open("bruteforce_evidence.txt", "w") as f:
        f.write(f"target={target} seed_bits={seed_bits}\n")
        f.write(f"recovered_seed={found}\nwall_seconds={dt:.1f}\n")
        f.write(f"stream_reconstructed={full == data}\n")
    print("wrote bruteforce_evidence.txt")


if __name__ == "__main__":
    main()
