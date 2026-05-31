#!/usr/bin/env python3
"""
compare_linux.py - Four-way SP 800-90B comparison.

  A. raw jitter            (real noise, no conditioning)
  B. Linux <- jitter       (real jitter through BLAKE2s pool + ChaCha20 crng)
  C. Linux <- fixed seed   (KNOWN seed through the same pipeline; predictable)
  D. BLAKE2b-CTR           (printed key, deterministic)

The question: does conditioning the weak jitter "the Linux way" (B) lift it
to BLAKE-quality scores (D)? And does it matter whether the seed was real
(B) or a fixed published value (C)?
"""

import os
import sys
import math
import argparse
import importlib.util
import subprocess


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('-n', '--samples', type=int, default=200_000)
    ap.add_argument('-b', '--bits', type=int, default=8)
    ap.add_argument('--perm-subset', type=int, default=10000)
    ap.add_argument('--perm-shuffles', type=int, default=2000)
    ap.add_argument('--no-perm', action='store_true')
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    tests = load(os.path.join(here, 'nist_90b_tests.py'), 'tests')
    coll  = load(os.path.join(here, 'collect_entropy.py'), 'coll')
    lrng  = load(os.path.join(here, 'linux_rng.py'), 'lrng')
    blake = load(os.path.join(here, 'blake_prng.py'), 'blake')

    jitter_f = '/tmp/cl_jitter.bin'
    linuxj_f = '/tmp/cl_linux_jitter.bin'
    linuxf_f = '/tmp/cl_linux_fixed.bin'
    blake_f  = '/tmp/cl_blake.bin'

    # A. raw jitter
    print("Building A: raw jitter ...", file=sys.stderr)
    data = coll.collect(args.samples, args.bits, verbose=False)
    open(jitter_f, 'wb').write(data)

    # B. Linux conditioned from that jitter
    print("Building B: Linux <- jitter ...", file=sys.stderr)
    rng = lrng.build_from_jitter(jitter_f)
    open(linuxj_f, 'wb').write(rng.get_random_bytes(args.samples))

    # C. Linux conditioned from a fixed published seed (predictable)
    print("Building C: Linux <- fixed seed ...", file=sys.stderr)
    rng = lrng.build_fixed(lrng.FIXED_SEED_HEX)
    open(linuxf_f, 'wb').write(rng.get_random_bytes(args.samples))

    # D. BLAKE2b-CTR printed key
    print("Building D: BLAKE2b-CTR printed key ...", file=sys.stderr)
    key = bytes.fromhex(blake.PRINTED_KEY_HEX)
    open(blake_f, 'wb').write(blake.generate(key, args.samples, args.bits))

    def analyze(path):
        s = list(open(path, 'rb').read())
        k = len(set(s))
        r = {'k': k}
        r['MCV']     = tests.mcv_estimate(s)
        r['t-Tuple'] = tests.t_tuple_estimate(s)
        r['LRS']     = tests.lrs_estimate(s)
        x = tests.multimcw(s);      r['MultiMCW'] = x[0] if x else None
        x = tests.lag_predictor(s); r['Lag']      = x[0] if x else None
        x = tests.multimmc(s);      r['MultiMMC'] = x[0] if x else None
        x = tests.lz78y(s);         r['LZ78Y']    = x[0] if x else None
        vals = [v for kk, v in r.items()
                if kk != 'k' and v is not None]
        r['final'] = min(vals)
        if not args.no_perm:
            perm = tests.run_permutation_battery(
                s, n_perm=args.perm_shuffles, subset=args.perm_subset)
            nf = sum(1 for v in perm.values() if v['verdict'] == 'REJECT')
            r['perm'] = (len(perm) - nf, len(perm))
        else:
            r['perm'] = None
        return r

    print("Analyzing A ...", file=sys.stderr); A = analyze(jitter_f)
    print("Analyzing B ...", file=sys.stderr); B = analyze(linuxj_f)
    print("Analyzing C ...", file=sys.stderr); C = analyze(linuxf_f)
    print("Analyzing D ...", file=sys.stderr); D = analyze(blake_f)

    cols = [
        ("A raw jitter",        A, "real, no cond."),
        ("B Linux<-jitter",     B, "real seed"),
        ("C Linux<-fixed",      C, "PREDICTABLE"),
        ("D BLAKE2b-CTR",       D, "PREDICTABLE"),
    ]

    print()
    print("=" * 86)
    print("  FOUR-WAY SP 800-90B COMPARISON")
    print("=" * 86)
    print(f"  samples/source={args.samples:,}  bits={args.bits}")
    print()
    hdr = f"  {'Estimator':<14}"
    for name, _, _ in cols:
        hdr += f"{name:>18}"
    print(hdr)
    sub = f"  {'':<14}"
    for _, _, note in cols:
        sub += f"{note:>18}"
    print(sub)
    print("  " + "-" * 84)
    for est in ["MCV", "t-Tuple", "LRS", "MultiMCW", "Lag", "MultiMMC", "LZ78Y"]:
        row = f"  {est:<14}"
        for _, R, _ in cols:
            v = R.get(est)
            row += f"{(f'{v:.3f}' if v is not None else '-'):>18}"
        tag = ""
        if est == "MultiMMC": tag = "  T3"
        if est == "LZ78Y":    tag = "  T4"
        print(row + tag)
    print("  " + "-" * 84)
    row = f"  {'FINAL H':<14}"
    for _, R, _ in cols:
        row += f"{R['final']:>18.3f}"
    print(row)
    if not args.no_perm:
        row = f"  {'IID pass':<14}"
        for _, R, _ in cols:
            pp = f"{R['perm'][0]}/{R['perm'][1]}"
            row += f"{pp:>18}"
        print(row)
    print("=" * 86)
    print()
    print("  READING THE TABLE")
    print("  " + "-" * 84)
    print(f"  A (raw jitter):  {A['final']:.2f} bits  -- real entropy, but weak; predictors catch it")
    print(f"  B (Linux<-real): {B['final']:.2f} bits  -- same jitter, now conditioned: looks perfect")
    print(f"  C (Linux<-fixed):{C['final']:.2f} bits  -- FIXED seed, fully predictable: also looks perfect")
    print(f"  D (BLAKE printed):{D['final']:.2f} bits -- printed key, predictable: also looks perfect")
    print()
    print("  B, C, D are statistically indistinguishable. Only B used real entropy.")
    print("  Conditioning -- not entropy -- is what produces the high score.")
    print("=" * 86)


if __name__ == '__main__':
    main()
