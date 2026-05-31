#!/usr/bin/env python3
"""
compare.py - Run jitter source and BLAKE2b-CTR source through SP 800-90B
and produce a side-by-side comparison.

This is the "key chart" for demonstrating that black-box 90B testing
cannot distinguish a genuine jitter entropy source from a deterministic
PRG keyed by a publicly-printed key.
"""

import subprocess
import sys
import os
import argparse
import importlib.util
import math


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("-n", "--samples", type=int, default=200_000,
                   help="Sample count for each source (default 200k)")
    p.add_argument("-b", "--bits", type=int, default=8)
    p.add_argument("--no-perm", action="store_true",
                   help="Skip permutation battery on both sources")
    p.add_argument("--perm-subset", type=int, default=10000)
    p.add_argument("--perm-shuffles", type=int, default=2000)
    args = p.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    collector = os.path.join(here, "collect_entropy.py")
    blake_gen = os.path.join(here, "blake_prng.py")
    tester    = os.path.join(here, "nist_90b_tests.py")
    predictor = os.path.join(here, "predict.py")

    jitter_file = "/tmp/cmp_jitter.bin"
    blake_file  = "/tmp/cmp_blake.bin"

    # ---- Collect ----
    print("=" * 70)
    print("STEP 1: Collect jitter entropy (real, non-reproducible)")
    print("=" * 70)
    subprocess.check_call(
        [sys.executable, collector, "-n", str(args.samples),
         "-b", str(args.bits), "-o", jitter_file, "--quiet"]
    )

    print()
    print("=" * 70)
    print("STEP 2: Generate BLAKE2b-CTR stream (deterministic, reproducible)")
    print("=" * 70)
    subprocess.check_call(
        [sys.executable, blake_gen, "-n", str(args.samples),
         "-b", str(args.bits), "-o", blake_file]
    )

    print()
    print("=" * 70)
    print("STEP 3: Verify BLAKE stream is byte-for-byte reproducible")
    print("=" * 70)
    # Use the same default key
    from blake_prng import PRINTED_KEY_HEX
    rc = subprocess.call(
        [sys.executable, predictor, blake_file, "--key", PRINTED_KEY_HEX,
         "--bits", str(args.bits)]
    )
    if rc != 0:
        print("FATAL: BLAKE reproducibility check failed", file=sys.stderr)
        sys.exit(1)

    # ---- Run tests programmatically so we can format the table ----
    sys.path.insert(0, here)
    tests = load_module(tester, "tests")

    def analyze(path, label):
        with open(path, "rb") as f:
            samples = list(f.read())
        k = len(set(samples))
        result = {"label": label, "k": k, "log2k": math.log2(k)}
        result["MCV"]      = tests.mcv_estimate(samples)
        result["t-Tuple"]  = tests.t_tuple_estimate(samples)
        result["LRS"]      = tests.lrs_estimate(samples)

        print(f"\n  computing predictors for {label}...", file=sys.stderr)
        r = tests.multimcw(samples);     result["MultiMCW"] = r[0] if r else None
        r = tests.lag_predictor(samples); result["Lag"]     = r[0] if r else None
        r = tests.multimmc(samples);     result["MultiMMC"] = r[0] if r else None
        r = tests.lz78y(samples);        result["LZ78Y"]    = r[0] if r else None

        # Permutation pass/fail summary
        if not args.no_perm:
            perm = tests.run_permutation_battery(
                samples,
                n_perm=args.perm_shuffles,
                subset=args.perm_subset,
            )
            n_fail = sum(1 for v in perm.values() if v["verdict"] == "REJECT")
            result["perm_pass"] = len(perm) - n_fail
            result["perm_total"] = len(perm)
            result["iid_track"] = (n_fail == 0)
        else:
            result["perm_pass"] = result["perm_total"] = None
            result["iid_track"] = None

        vals = [v for k_, v in result.items()
                if k_ in ("MCV", "t-Tuple", "LRS", "MultiMCW",
                          "Lag", "MultiMMC", "LZ78Y") and v is not None]
        result["final"] = min(vals)
        return result

    print()
    print("=" * 70)
    print("STEP 4: SP 800-90B analysis on both sources")
    print("=" * 70)
    jitter = analyze(jitter_file, "jitter (real)")
    blake  = analyze(blake_file,  "BLAKE2b-CTR (printed key)")

    # ---- Side-by-side table ----
    print()
    print("=" * 70)
    print("  COMPARISON: real jitter entropy  vs.  PRG with printed key")
    print("=" * 70)
    print(f"  samples per source : {args.samples:,}")
    print(f"  bits per sample    : {args.bits}")
    print(f"  alphabet (k)       : jitter={jitter['k']}, blake={blake['k']}")
    print(f"  theoretical max H  : {jitter['log2k']:.3f} bits/sample")
    print()
    print(f"  {'Estimator':<22} {'jitter (real)':>16} {'BLAKE (printed key)':>22}")
    print(f"  {'-'*22} {'-'*16} {'-'*22}")
    for name in ["MCV", "t-Tuple", "LRS", "MultiMCW",
                 "Lag", "MultiMMC", "LZ78Y"]:
        a = jitter.get(name)
        b = blake.get(name)
        a_s = f"{a:.4f}" if a is not None else "-"
        b_s = f"{b:.4f}" if b is not None else "-"
        suffix = ""
        if name == "MultiMMC":
            suffix = "  (= Tirn T3)"
        elif name == "LZ78Y":
            suffix = "  (= Tirn T4)"
        print(f"  {name:<22} {a_s:>16} {b_s:>22}{suffix}")

    print(f"  {'-'*22} {'-'*16} {'-'*22}")
    print(f"  {'FINAL min-entropy':<22} {jitter['final']:>16.4f} {blake['final']:>22.4f}")

    if not args.no_perm:
        print()
        print(f"  §5.1 permutation battery (subset={args.perm_subset}, shuffles={args.perm_shuffles}):")
        print(f"    jitter: {jitter['perm_pass']}/{jitter['perm_total']} tests pass "
              f"-> {'IID' if jitter['iid_track'] else 'non-IID'} track")
        print(f"    BLAKE : {blake ['perm_pass']}/{blake ['perm_total']} tests pass "
              f"-> {'IID' if blake ['iid_track'] else 'non-IID'} track")

    print()
    print("=" * 70)
    print("  INTERPRETATION")
    print("=" * 70)
    delta = abs(jitter['final'] - blake['final'])
    print(f"  Final min-entropy delta: {delta:.4f} bits/sample")
    if delta < 0.1:
        print(f"  -> The two sources are statistically indistinguishable at 90B's")
        print(f"     black-box resolution. The BLAKE stream's full predictability")
        print(f"     (proven in STEP 3) is INVISIBLE to the test suite.")
    print(f"  This is the operational form of AIS 31 §4.6.2:")
    print(f"     statistical tests can falsify a stochastic model but never verify it.")
    print(f"     A passing 90B result is necessary, not sufficient, for unpredictability.")
    print("=" * 70)


if __name__ == "__main__":
    main()
