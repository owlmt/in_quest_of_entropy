#!/usr/bin/env python3
"""
visualize.py - Render the four-way entropy comparison as a single figure.

For each source it draws the actual output bytes as a grayscale texture, the
SP 800-90B score as a bar, and a plain-language verdict. The point the picture
makes: B, C and D are visually and statistically identical, all score ~8/8, yet
only B is unpredictable.

Scores default to the published 1,000,000-sample run (RESULTS_linux.txt). Pass
--compute to recompute the Most-Common-Value estimate live on the rendered bytes.
"""

import os
import sys
import argparse
import importlib.util
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", type=int, default=256, help="bitmap side in px")
    ap.add_argument("--compute", action="store_true",
                    help="recompute MCV min-entropy live on the rendered bytes")
    ap.add_argument("-o", "--output", default="entropy_four_ways.png")
    # canonical scores from the 1M run (RESULTS_linux.txt)
    ap.add_argument("--scores", default="2.67,7.52,7.35,7.87")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    coll  = load(os.path.join(here, "collect_entropy.py"), "coll")
    lrng  = load(os.path.join(here, "linux_rng.py"), "lrng")
    blake = load(os.path.join(here, "blake_prng.py"), "blake")

    N = args.side * args.side

    # ---- generate the four streams ----
    print("Generating A: raw jitter ...", file=sys.stderr)
    A = coll.collect(N, 8, verbose=False)
    jit = "/tmp/vis_jitter.bin"; open(jit, "wb").write(A)

    print("Generating B: Linux <- jitter ...", file=sys.stderr)
    B = lrng.build_from_jitter(jit).get_random_bytes(N)

    print("Generating C: Linux <- fixed seed ...", file=sys.stderr)
    C = lrng.build_fixed(lrng.FIXED_SEED_HEX).get_random_bytes(N)

    print("Generating D: BLAKE2b-CTR printed key ...", file=sys.stderr)
    D = blake.generate(bytes.fromhex(blake.PRINTED_KEY_HEX), N, 8)

    streams = [A, B, C, D]

    scores = [float(x) for x in args.scores.split(",")]
    if args.compute:
        tests = load(os.path.join(here, "nist_90b_tests.py"), "tests")
        scores = [tests.mcv_estimate(list(s)) for s in streams]
        print("live MCV scores:", [f"{x:.2f}" for x in scores], file=sys.stderr)

    GREEN = "#2E9E5B"; RED = "#D1495B"; AMBER = "#E8A33D"; INK = "#1b1b1b"
    panels = [
        dict(tag="A", title="Raw jitter",
             sub="real noise, no conditioning",
             verdict="WEAK\nentropy is low — and the test says so",
             color=AMBER),
        dict(tag="B", title="Linux pipeline\n(real jitter seed)",
             sub="BLAKE2s pool -> ChaCha20",
             verdict="SECURE\nreal 256-bit secret seed",
             color=GREEN),
        dict(tag="C", title="Linux pipeline\n(FIXED seed)",
             sub="identical pipeline, public seed",
             verdict="PREDICTABLE\nseed is published",
             color=RED),
        dict(tag="D", title="BLAKE2b-CTR\n(printed key)",
             sub="deterministic PRG",
             verdict="PREDICTABLE\nkey is printed",
             color=RED),
    ]

    fig = plt.figure(figsize=(15, 8.2), facecolor="white")
    gs = fig.add_gridspec(3, 4, height_ratios=[0.62, 1.0, 0.5],
                          hspace=0.18, wspace=0.16,
                          left=0.045, right=0.965, top=0.84, bottom=0.11)

    fig.suptitle("Four random streams. A black-box test rates three of them ~8/8.\nOnly one is actually unpredictable.",
                 fontsize=20, fontweight="bold", color=INK, x=0.5, y=0.965,
                 ha="center")

    for i, (s, p) in enumerate(zip(streams, panels)):
        img = np.frombuffer(s, dtype=np.uint8).reshape(args.side, args.side)

        # header
        axh = fig.add_subplot(gs[0, i]); axh.axis("off")
        axh.text(0.5, 0.78, f"{p['tag']}", fontsize=30, fontweight="bold",
                 ha="center", va="center", color=p["color"], transform=axh.transAxes)
        axh.text(0.5, 0.30, p["title"], fontsize=13.5, fontweight="bold",
                 ha="center", va="center", color=INK, transform=axh.transAxes)
        axh.text(0.5, -0.02, p["sub"], fontsize=10, style="italic",
                 ha="center", va="center", color="#666", transform=axh.transAxes)

        # bitmap
        axi = fig.add_subplot(gs[1, i])
        axi.imshow(img, cmap="gray", vmin=0, vmax=255, interpolation="nearest")
        axi.set_xticks([]); axi.set_yticks([])
        for sp in axi.spines.values():
            sp.set_edgecolor(p["color"]); sp.set_linewidth(2.5)

        # score bar + verdict
        axb = fig.add_subplot(gs[2, i]); axb.axis("off")
        axb.set_xlim(0, 1); axb.set_ylim(0, 1)
        # bar track
        axb.add_patch(plt.Rectangle((0.08, 0.66), 0.84, 0.16,
                                    color="#e9e9e9", zorder=1))
        frac = scores[i] / 8.0
        axb.add_patch(plt.Rectangle((0.08, 0.66), 0.84 * frac, 0.16,
                                    color=p["color"], zorder=2))
        axb.text(0.5, 0.90, f"90B score: {scores[i]:.2f} / 8 bits",
                 fontsize=11, fontweight="bold", ha="center", va="bottom",
                 color=INK, transform=axb.transAxes)
        # verdict badge
        bb = FancyBboxPatch((0.08, 0.04), 0.84, 0.48,
                            boxstyle="round,pad=0.02,rounding_size=0.04",
                            linewidth=0, facecolor=p["color"], alpha=0.14,
                            transform=axb.transAxes, zorder=1)
        axb.add_patch(bb)
        vt = p["verdict"].split("\n")
        axb.text(0.5, 0.40, vt[0], fontsize=12.5, fontweight="bold",
                 ha="center", va="center", color=p["color"], transform=axb.transAxes)
        axb.text(0.5, 0.16, vt[1], fontsize=9.2, ha="center", va="center",
                 color="#555", transform=axb.transAxes)

    cap = ("By the data-processing inequality, conditioning cannot create entropy: H_inf(f(K)) <= H_inf(K).  "
           "Streams B, C and D are statistically indistinguishable and all score near 8/8 — yet only B used real entropy.  "
           "No black-box test can separate them; only the seed's provenance can.")
    fig.text(0.5, 0.045, cap, fontsize=10.3, ha="center", va="center",
             color="#333", wrap=True)
    fig.text(0.5, 0.012, "github.com/owlmt/in_quest_of_entropy/tree/main/800-90b",
             fontsize=9.5, ha="center", color="#888", style="italic")

    fig.savefig(args.output, dpi=150, facecolor="white")
    print(f"saved {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
