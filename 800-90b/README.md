# Linux-kernel-style entropy + SP 800-90B test runner

Two scripts that together (a) collect raw entropy in user space the way
the Linux kernel's jitter entropy noise source does, and (b) run the
SP 800-90B IID battery and min-entropy estimators on the result.

## What's in here

- **`collect_entropy.py`** — measures `clock_gettime(CLOCK_MONOTONIC_RAW)`
  deltas around a tiny LFSR payload. The low N bits of each delta become
  one sample. This is the same idea as the kernel's `jitterentropy.c`
  (Stephan Müller's design), which is the source feeding `add_interrupt_randomness`
  in modern kernels.

- **`nist_90b_tests.py`** — implements:
  - The §5.1 permutation battery (19 statistic instances)
  - Min-entropy estimators §6.3.1, §6.3.5, §6.3.6, §6.3.7, §6.3.8, §6.3.9, §6.3.10
  - Binary-only estimators (§6.3.2, .3, .4) are intentionally omitted
    since the source is multi-bit.

  Note: §6.3.9 (MultiMMC) and §6.3.10 (LZ78Y) are the same predictors
  as AIS 31 v3.0 Tirn T3 and T4. The harmonization is deliberate.

## Quick start on WSL

```bash
# 1. Collect 1M samples at 8 bits each (~20s on a typical laptop)
python3 collect_entropy.py -n 1000000 -b 8 -o samples.bin

# 2. Run the full test suite (will take a few minutes for predictors)
python3 nist_90b_tests.py samples.bin
```

## Sample-budget guidance

- The §5.1 permutation battery is the slowest part by far (10k shuffles
  × 19 statistics). The script subsets to 20k samples by default.
  For a fuller picture, increase `--perm-subset`.
- The predictor estimators (§6.3.7–10) are pure Python and run at ~5k
  samples/s. 1M samples ≈ 3–5 minutes for all four.
- For the canonical 90B numbers, build the official NIST reference:
  `git clone https://github.com/usnistgov/SP800-90B_EntropyAssessment`
  and run `./ea_non_iid samples.bin 8`. This Python tool is for fast
  iteration; cross-check the final number against the C++ implementation.

## Useful flags

```bash
# Skip the permutation battery (fast, runs only the min-entropy estimators)
python3 nist_90b_tests.py samples.bin --no-perm

# Force the non-IID track even if permutation passes (always runs all
# 7 estimators) -- recommended for jitter sources because §5.1 has weak
# power on subtle predictable structure
python3 nist_90b_tests.py samples.bin --no-perm

# Limit estimator input to N samples (for quick exploration)
python3 nist_90b_tests.py samples.bin --max-samples 100000

# Heavier permutation testing
python3 nist_90b_tests.py samples.bin --perm-subset 50000 --perm-shuffles 10000
```

## Interpreting the output

- `MCV` is the per-sample probability bound from §6.3.1. On the IID
  track it's the only number that counts.
- The four predictors (§6.3.7–10) each report `P_global` (raw guessing
  rate), `P_g_up` (its 99% upper bound), `P_local` (the rate implied by
  the longest correct-prediction run), and the final min-entropy
  `−log₂ max(P_g_up, P_loc, 1/k)`.
- `P_local` dominating the final number is the signal that the source
  has subtle local structure (predictable streaks) that `P_global`
  averages over.

## Tuning the collector

The defaults (64 LFSR iters/sample, 8 output bits) give roughly 7+ bits
per byte of min-entropy on a typical x86 CPU in a moderately loaded
system. To trade rate for entropy density:

```bash
# Stronger jitter via longer payload (slower, more entropy per byte)
python3 collect_entropy.py -n 1000000 -w 256

# Lower bits per sample (cleaner samples but less data per call)
python3 collect_entropy.py -n 1000000 -b 4
```

## What this *doesn't* do

- It doesn't replicate `add_interrupt_randomness()` — user space can't
  observe interrupt timing.
- It doesn't include a conditioning component (§3.1.5 of 90B). All
  numbers reported are *pre-conditioning min-entropy*. Linux conditions
  this through BLAKE2s + ChaCha20 before output.
- It doesn't run the §3.1.4 restart tests (requires power-cycling the
  source 1000 times).
- It doesn't run the §4 continuous health tests (RCT, APT) — those are
  for runtime monitoring, not validation.

For the AIS 31 mapping, this is the NPTRNG noise-source measurement
under NTG.1 — the same place the Linux RNG sits per the BSI's
LinuxRNG_EN report.
