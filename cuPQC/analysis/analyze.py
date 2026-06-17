#!/usr/bin/env python3
"""
analyze.py -- analysis + visualization for the cuPQC get_entropy probe.

Subcommands mirror the harness modes:
  dump       (Exp 1)  smoke statistical battery on the raw byte stream + plots
  collision  (Exp 3a) birthday-bound context + collision verdict plot
  batchcorr  (Exp 4)  block-to-block correlation heatmap + |r| distribution

IMPORTANT: the 'dump' smoke battery is a NECESSARY-NOT-SUFFICIENT screen. A pass
proves nothing about entropy; it only flags gross failures. The authoritative
output batteries are external: NIST STS (SP 800-22), PractRand, dieharder, ent.
SP 800-90B is deliberately NOT attempted here: it assesses a *raw noise source*,
and get_entropy exposes only conditioned output -- that inapplicability is itself
a finding (see README).

Deps:  numpy, scipy, matplotlib   (WSL/Ubuntu 24.04, PEP 668):
  sudo apt install -y python3-numpy python3-scipy python3-matplotlib
  # or: python3 -m venv .venv && . .venv/bin/activate && pip install numpy scipy matplotlib
"""
import argparse, json, sys, zlib
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

INK = "#10131a"; ACCENT = "#3a7bd5"; WARN = "#d7263d"; GOOD = "#1b998b"; MUTED = "#8a93a6"


def load_meta(basename):
    with open(basename + ".meta.json") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# Experiment 1 -- dump smoke battery
# --------------------------------------------------------------------------
def smoke_battery(data: np.ndarray):
    """data: uint8 array. Returns dict of summary statistics."""
    bits = np.unpackbits(data)
    n_bits = bits.size
    ones = int(bits.sum())
    p_ones = ones / n_bits
    # monobit z (SP 800-22 frequency test, large-sample normal approx)
    z = abs(2 * ones - n_bits) / np.sqrt(n_bits)
    # byte chi-square (uniformity over 256 symbols)
    counts = np.bincount(data, minlength=256).astype(float)
    exp = data.size / 256.0
    chi2 = float(((counts - exp) ** 2 / exp).sum())   # df = 255
    # min-entropy per byte (-log2 max p) and Shannon entropy per byte
    p = counts / counts.sum()
    nz = p[p > 0]
    H_min = float(-np.log2(nz.max()))
    H_sh = float(-(nz * np.log2(nz)).sum())
    # lag-1 byte autocorrelation
    x = data.astype(np.float64)
    x -= x.mean()
    ac1 = float((x[:-1] * x[1:]).sum() / (x * x).sum()) if (x * x).sum() else 0.0
    # compression ratio (any structure => <1.0 ratio of compressed/original)
    comp = len(zlib.compress(data.tobytes(), 9)) / max(1, data.size)
    return dict(n_bytes=int(data.size), p_ones=p_ones, monobit_z=float(z),
                byte_chi2=chi2, byte_chi2_df=255, H_min_per_byte=H_min,
                H_shannon_per_byte=H_sh, lag1_autocorr=ac1, compress_ratio=comp,
                byte_counts=counts, bit_bias=p_ones - 0.5)


def plot_dump(stats, meta, out_png, synthetic=False):
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    fig.suptitle("Exp 1 — raw output smoke battery (necessary, not sufficient)",
                 fontweight="bold", color=INK)
    # byte histogram
    ax[0].bar(np.arange(256), stats["byte_counts"], width=1.0, color=ACCENT)
    exp = stats["n_bytes"] / 256.0
    ax[0].axhline(exp, color=WARN, lw=1, ls="--", label=f"uniform exp={exp:,.0f}")
    ax[0].set_title("byte value distribution"); ax[0].set_xlabel("byte value")
    ax[0].set_ylabel("count"); ax[0].legend(fontsize=8)
    # verdict panel
    ax[1].axis("off")
    chi2_ok = stats["byte_chi2"] < 330.5     # ~chi2(255) upper 1% ≈ 310; 330 generous
    mono_ok = stats["monobit_z"] < 3.0
    ac_ok = abs(stats["lag1_autocorr"]) < 0.01
    rows = [
        ("bytes analysed", f"{stats['n_bytes']:,}", None),
        ("P(bit=1)", f"{stats['p_ones']:.6f}", mono_ok),
        ("monobit z", f"{stats['monobit_z']:.3f}  (want <3)", mono_ok),
        ("byte chi-square", f"{stats['byte_chi2']:.1f} / df 255", chi2_ok),
        ("min-entropy/byte", f"{stats['H_min_per_byte']:.3f} / 8", None),
        ("Shannon/byte", f"{stats['H_shannon_per_byte']:.4f} / 8", None),
        ("lag-1 autocorr", f"{stats['lag1_autocorr']:+.5f}", ac_ok),
        ("compress ratio", f"{stats['compress_ratio']:.4f}  (want ~1)",
         stats["compress_ratio"] > 0.99),
    ]
    y = 0.95
    for k, v, ok in rows:
        c = INK if ok is None else (GOOD if ok else WARN)
        ax[1].text(0.02, y, k, color=MUTED, fontsize=10, va="top")
        ax[1].text(0.55, y, v, color=c, fontsize=10, va="top", fontweight="bold")
        y -= 0.115
    _footer(fig, meta, synthetic)
    fig.tight_layout(rect=[0, 0.04, 1, 0.93]); fig.savefig(out_png, dpi=130)
    print(f"[dump] wrote {out_png}")
    return dict(monobit_ok=mono_ok, chi2_ok=chi2_ok, autocorr_ok=ac_ok)


# --------------------------------------------------------------------------
# Experiment 3a -- collision / birthday
# --------------------------------------------------------------------------
def plot_collision(meta, out_png, synthetic=False):
    M = int(meta["blocks_emitted"]); b = int(meta["block_bits"])
    observed = int(meta["collisions"])
    # P(at least one collision) ~ 1 - exp(-M(M-1)/2 / 2^b)  (birthday)
    Ms = np.logspace(0, np.log10(max(M, 10)) + 6, 400)
    with np.errstate(over="ignore"):
        Pexp = 1 - np.exp(-Ms * (Ms - 1) / 2.0 / (2.0 ** b))
    fig, ax = plt.subplots(figsize=(9, 4.6))
    ax.plot(Ms, Pexp, color=ACCENT, lw=2,
            label=f"birthday P(≥1 collision), {b}-bit blocks")
    ax.axvline(M, color=MUTED, ls="--", lw=1, label=f"this run: M={M:,} blocks")
    half = 2.0 ** (b / 2.0)
    ax.axvline(half, color=GOOD, ls=":", lw=1, label=f"50% point ≈ 2^{b//2}")
    ax.set_xscale("log"); ax.set_xlabel("blocks drawn M (log)")
    ax.set_ylabel("P(≥1 collision)"); ax.set_ylim(-0.03, 1.03)
    verdict = (f"OBSERVED COLLISIONS: {observed}  ⇒ SEED REUSE / WEAK RNG"
               if observed > 0 else
               f"observed collisions: 0  (expected ≈ {M*(M-1)/2/2**b:.2e})")
    ax.set_title("Exp 3a — cross-call exact-block collision\n" + verdict,
                 fontweight="bold",
                 color=(WARN if observed > 0 else INK))
    ax.legend(fontsize=8, loc="center left")
    _footer(fig, meta, synthetic)
    fig.tight_layout(rect=[0, 0.05, 1, 1]); fig.savefig(out_png, dpi=130)
    print(f"[collision] wrote {out_png}  (observed={observed})")
    return dict(observed=observed, expected=float(M * (M - 1) / 2 / 2 ** b))


# --------------------------------------------------------------------------
# Experiment 4 -- batch correlation
# --------------------------------------------------------------------------
def batchcorr(matrix: np.ndarray):
    """matrix: (N, block_size) uint8.

    Correlation is computed POSITION-to-POSITION across the N blocks (each byte
    position is a column of N samples), NOT block-to-block: a single 32-byte
    block has only 32 samples, so block-to-block Pearson is dominated by noise
    (|r| up to ~0.8 is normal). With N large the L x L position-correlation has
    off-diagonal SD ~ 1/sqrt(N), giving a tight, meaningful test.

    Returns (LxL corr matrix, off-diagonal |r| array, per-bit-position monobit z).
    """
    N, L = matrix.shape
    cols = matrix.astype(np.float64)                 # (N, L); columns = positions
    cols -= cols.mean(axis=0, keepdims=True)
    sd = cols.std(axis=0, keepdims=True)
    sd[sd == 0] = 1
    cols /= sd
    corr = (cols.T @ cols) / N                        # (L, L) position correlation
    iu = np.triu_indices(L, k=1)
    rvals = corr[iu]
    # transposed-stream monobit: P(bit=1) per bit position across the N blocks
    bits = np.unpackbits(matrix, axis=1)              # (N, L*8)
    colmeans = bits.mean(axis=0)
    z = (colmeans - 0.5) * 2 * np.sqrt(N)             # ~N(0,1) per position under H0
    return corr, rvals, z, N


def plot_batchcorr(matrix, meta, out_png, synthetic=False):
    corr, rvals, z, N = batchcorr(matrix)
    L = matrix.shape[1]
    fig, ax = plt.subplots(1, 3, figsize=(14, 4.3))
    fig.suptitle("Exp 4 — batch / cross-instance correlation "
                 "(position-to-position across blocks)",
                 fontweight="bold", color=INK)
    # LxL position-correlation heatmap
    im = ax[0].imshow(corr, cmap="coolwarm", vmin=-0.05, vmax=0.05)
    ax[0].set_title(f"byte-position corr ({L}×{L}), N={N:,}")
    ax[0].set_xlabel("byte position"); ax[0].set_ylabel("byte position")
    fig.colorbar(im, ax=ax[0], fraction=0.046)
    # off-diagonal |r| vs theory: under H0, r ~ N(0, 1/sqrt(N))
    sd = 1 / np.sqrt(max(N, 1))
    ax[1].hist(rvals, bins=60, density=True, color=ACCENT, alpha=0.8, label="off-diagonal r")
    xs = np.linspace(min(rvals.min(), -4*sd), max(rvals.max(), 4*sd), 200)
    ax[1].plot(xs, np.exp(-xs**2/(2*sd*sd))/(sd*np.sqrt(2*np.pi)),
               color=WARN, lw=1.5, label=f"H0: N(0,{sd:.4f}²)")
    ax[1].set_title("position-pair r vs H0"); ax[1].set_xlabel("r"); ax[1].legend(fontsize=8)
    maxr = float(np.abs(rvals).max())
    ax[1].text(0.02, 0.95, f"max|r|={maxr:.4f}\n(~{maxr/sd:.1f}σ)",
               transform=ax[1].transAxes, va="top", fontsize=9,
               color=(WARN if maxr > 5*sd else GOOD))
    # transposed per-position monobit z
    ax[2].plot(z, color=GOOD, lw=0.6)
    ax[2].axhline(3, color=WARN, ls="--", lw=1); ax[2].axhline(-3, color=WARN, ls="--", lw=1)
    ax[2].set_title("per-bit-position monobit z\n(across blocks)")
    ax[2].set_xlabel("bit position"); ax[2].set_ylabel("z")
    flagged = int((np.abs(z) > 3).sum())
    ax[2].text(0.02, 0.95, f"|z|>3: {flagged}/{z.size}", transform=ax[2].transAxes,
               color=(WARN if flagged > 0.02*z.size else GOOD), fontsize=9, va="top")
    _footer(fig, meta, synthetic)
    fig.tight_layout(rect=[0, 0.04, 1, 0.93]); fig.savefig(out_png, dpi=130)
    print(f"[batchcorr] wrote {out_png}  max|r|={maxr:.4f} flagged_bits={flagged}")
    return dict(max_abs_r=maxr, mean_abs_r=float(np.abs(rvals).mean()),
                flagged_bits=flagged, total_bits=int(z.size), N=int(N))


# --------------------------------------------------------------------------
def _footer(fig, meta, synthetic):
    tag = (f"subject={meta.get('subject','?')}  variant={meta.get('variant','?')}  "
           f"gpu={meta.get('gpu','?')}  block={meta.get('block_bits','?')}b")
    fig.text(0.01, 0.005, tag, fontsize=7, color=MUTED)
    if synthetic:
        fig.text(0.5, 0.5, "SYNTHETIC — ILLUSTRATIVE ONLY", fontsize=34,
                 color="#ff000022", ha="center", va="center", rotation=24,
                 fontweight="bold", zorder=10)


def main():
    ap = argparse.ArgumentParser(description="analyze cuPQC get_entropy probe output")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("dump", "collision", "batchcorr"):
        s = sub.add_parser(name)
        s.add_argument("basename", help="output basename used by the harness (no extension)")
        s.add_argument("--png", default=None)
    a = ap.parse_args()
    meta = load_meta(a.basename)
    png = a.png or (a.basename + f".{a.cmd}.png")

    if a.cmd == "dump":
        data = np.fromfile(a.basename + ".bin", dtype=np.uint8)
        st = smoke_battery(data)
        plot_dump(st, meta, png)
    elif a.cmd == "collision":
        plot_collision(meta, png)
    elif a.cmd == "batchcorr":
        L = int(meta["block_size_bytes"]); N = int(meta["n_blocks"])
        mat = np.fromfile(a.basename + ".matrix.bin", dtype=np.uint8)[:N*L].reshape(N, L)
        plot_batchcorr(mat, meta, png)


if __name__ == "__main__":
    main()
