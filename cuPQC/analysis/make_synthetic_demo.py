#!/usr/bin/env python3
"""
make_synthetic_demo.py -- produce ILLUSTRATIVE, clearly-watermarked figures
showing what each experiment's output looks like under two scenarios:

  CLEAN  : an ideal CSPRNG-like stream (what H0 predicts -> we FAIL to reject)
  BROKEN : a deliberately defective generator (per-thread seeded, low period,
           seed reuse) -> what a POSITIVE detection looks like (-> reject H0)

These are NOT cuPQC results. They exist so the repo has a visual demonstration
of the methodology before anyone has GPU access. Replace with real runs.

Run:  python3 make_synthetic_demo.py   (writes into ../figures)
"""
import os, numpy as np
import analyze as A

OUT = os.path.join(os.path.dirname(__file__), "..", "figures")
os.makedirs(OUT, exist_ok=True)
rng = np.random.default_rng(20260617)

def meta(variant="encaps", **kw):
    m = dict(subject="cupqc_get_entropy(SYNTHETIC)", variant=variant,
             gpu="SYNTHETIC-DEMO (no GPU)", block_size_bytes=32, block_bits=256,
             blocks_emitted=0, collisions=0, n_blocks=0)
    m.update(kw); return m

# ---- Exp 1: dump smoke battery -------------------------------------------
clean = rng.integers(0, 256, size=4_000_000, dtype=np.uint8)
A.plot_dump(A.smoke_battery(clean), meta(blocks_emitted=clean.size//32),
            os.path.join(OUT, "demo_dump_clean.png"), synthetic=True)

# broken: heavy byte bias + periodicity (an LCG-ish low-quality stream)
n = 4_000_000
lcg = np.empty(n, dtype=np.uint8); x = 1234567
for i in range(n):                      # tiny-state LCG mod 256-ish -> structure
    x = (1103515245 * x + 12345) & 0x7fffffff
    lcg[i] = (x >> 16) & 0xFF
lcg = (lcg & 0xF8)                       # zero low bits -> visible bias + low min-entropy
A.plot_dump(A.smoke_battery(lcg), meta(blocks_emitted=lcg.size//32),
            os.path.join(OUT, "demo_dump_broken.png"), synthetic=True)

# ---- Exp 3a: collision ----------------------------------------------------
# clean: huge M, 256-bit blocks, zero collisions (as expected)
A.plot_collision(meta(blocks_emitted=5_000_000, collisions=0),
                 os.path.join(OUT, "demo_collision_clean.png"), synthetic=True)
# broken: small effective state -> collisions appear far below 2^128
A.plot_collision(meta(blocks_emitted=5_000_000, collisions=37, block_bits=256),
                 os.path.join(OUT, "demo_collision_broken.png"), synthetic=True)

# ---- Exp 4: batch correlation --------------------------------------------
# clean: large N so off-diagonal position-correlation SD ~ 1/sqrt(N) is tight
Nc, L = 100_000, 32
clean_mat = rng.integers(0, 256, size=(Nc, L), dtype=np.uint8)
A.plot_batchcorr(clean_mat, meta(n_blocks=Nc),
                 os.path.join(OUT, "demo_batchcorr_clean.png"), synthetic=True)

# broken: per-"thread" generator seeded by block index -> structured columns
Nb = 20_000
broken_mat = np.empty((Nb, L), dtype=np.uint8)
for i in range(Nb):
    s = (i * 2654435761) & 0xffffffff           # seed = hash(blockIdx) -- the cuRAND failure mode
    for j in range(L):
        s = (1103515245 * s + 12345) & 0x7fffffff
        broken_mat[i, j] = (s >> 16) & 0xFF
broken_mat[::500] = broken_mat[0]               # a few exact repeats too
A.plot_batchcorr(broken_mat, meta(n_blocks=Nb),
                 os.path.join(OUT, "demo_batchcorr_broken.png"), synthetic=True)

print("\nSynthetic demo figures written to", os.path.abspath(OUT))
