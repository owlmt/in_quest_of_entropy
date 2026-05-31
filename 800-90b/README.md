# SP 800-90B entropy toolkit + predictability demonstrations

A small toolkit that (a) collects raw entropy in user space the way the Linux
kernel's jitter noise source does, (b) runs the NIST SP 800-90B IID battery and
min-entropy estimators on it, and (c) demonstrates that black-box statistical
testing cannot distinguish genuine entropy from a deterministic, fully
predictable source once that source is run through cryptographic conditioning.

The estimators 6.3.9 (MultiMMC) and 6.3.10 (LZ78Y) are the same predictors as
AIS 31 v3.0 Tirn T3 and T4 -- the harmonization is deliberate.

## Files

Collection and testing
- collect_entropy.py  -- jitter timing noise source (clock_gettime deltas), the
  user-space analogue of crypto/jitterentropy.c
- nist_90b_tests.py   -- 5.1 permutation battery (19 statistics) + min-entropy
  estimators 6.3.1, .5, .6, .7, .8, .9, .10

Deterministic sources (the "backdoor" demonstrations)
- blake_prng.py       -- BLAKE2b in counter mode keyed by a PRINTED key; output
  is byte-for-byte reproducible yet passes 90B
- linux_rng.py        -- faithful model of the Linux output path:
  noise -> BLAKE2s input_pool -> extract_entropy -> ChaCha20 crng
  (fast-key-erasure). ChaCha20 verified against RFC 8439 2.3.2.
  Seed from real jitter (--from-jitter) or a fixed published value (--fixed-seed).
- predict.py          -- reproduces a BLAKE2b-CTR stream from its printed key,
  proving predictability

Comparisons
- compare.py          -- two-way: real jitter vs BLAKE2b-CTR printed key
- compare_linux.py    -- four-way: raw jitter / Linux<-jitter / Linux<-fixed-seed
  / BLAKE2b-CTR

Results and interpretation
- RESULTS.txt         -- captured output of the two-way comparison
- FINDINGS.md         -- interpretation of the two-way comparison
- RESULTS_linux.txt   -- captured output of the four-way comparison
- FINDINGS_linux.md   -- interpretation of the four-way comparison, with the
  data-processing-inequality and HILL-pseudoentropy argument and citations

## Quick start

```bash
pip install numpy --break-system-packages 2>/dev/null || pip install numpy

# Your laptop's jitter, full SP 800-90B analysis
python3 collect_entropy.py -n 1000000 -b 8 -o samples.bin
python3 nist_90b_tests.py samples.bin

# Two-way: real jitter vs deterministic BLAKE2b-CTR
python3 compare.py -n 1000000 -b 8 | tee RESULTS.txt

# Four-way: raw jitter vs Linux pipeline (real seed, fixed seed) vs BLAKE
python3 compare_linux.py -n 1000000 -b 8 | tee RESULTS_linux.txt
```

## The headline result

On the test laptop, raw jitter scored 2.67 bits/byte and failed 8 of 19 IID
tests. The same jitter conditioned through the Linux pipeline jumped to 7.52
bits and passed all 19. A copy of that pipeline seeded from a fixed, published
value -- fully predictable -- also scored ~7.4 bits and passed all 19, as did
the printed-key BLAKE PRG. The three conditioned columns are statistically
indistinguishable; only one used real entropy.

By the data-processing inequality, deterministic conditioning cannot increase
min-entropy (H_inf(f(K)) <= H_inf(K)), so the high scores are not real entropy
-- they are HILL pseudoentropy, which is what every polynomial-time statistical
test measures. A passing battery is necessary, never sufficient, for
unpredictability. See FINDINGS_linux.md for the full argument and citations.

## What this does NOT model

- add_interrupt_randomness() (user space can't observe interrupt timing)
- the 3.1.4 restart tests and section 4 continuous health tests (RCT/APT)
- a hardware RNG (RDRAND/RDSEED) contribution

Pre-conditioning min-entropy figures from nist_90b_tests.py are a lower bound
on what the full kernel RNG would see at the same point in the pipeline. For the
canonical 90B numbers, cross-check with the NIST reference implementation at
github.com/usnistgov/SP800-90B_EntropyAssessment.
