# Conditioning, not entropy: a four-way SP 800-90B comparison

**One line:** running raw entropy through the Linux RNG's own BLAKE2s + ChaCha20
conditioning makes a weak 2.67-bit/byte source, a fully predictable fixed-seed
source, and a printed-key PRG all score ~7.4-7.9 bits/byte and pass the entire
IID battery. The black-box tests measure the conditioner, not the noise source.

## Setup

Four sources, 1,000,000 samples each, 8 bits/sample, analysed with the SP 800-90B
IID permutation battery (19 statistics) and the non-IID min-entropy estimators
(MCV, t-Tuple, LRS, MultiMCW, Lag, MultiMMC=Tirn T3, LZ78Y=Tirn T4):

- **A. raw jitter** -- user-space jitter timing, no conditioning
- **B. Linux <- jitter** -- the SAME jitter pushed through BLAKE2s input_pool + ChaCha20 crng (fast-key-erasure), i.e. the real Linux output path
- **C. Linux <- fixed seed** -- the IDENTICAL pipeline seeded from a fixed published value (deadbeefcafebabe...); byte-for-byte reproducible, zero real entropy
- **D. BLAKE2b-CTR** -- deterministic PRG keyed by a printed key

## Results (this run, on the test laptop)

```
  Estimator           A raw jitter   B Linux<-jitter    C Linux<-fixed     D BLAKE2b-CTR
                    real, no cond.         real seed       PREDICTABLE       PREDICTABLE
  ------------------------------------------------------------------------------------
  MCV                        3.736             7.887             7.880             7.873
  t-Tuple                    2.673             7.887             7.354             7.873
  LRS                        3.060             7.522             7.522             7.939
  MultiMCW                   3.056             7.950             7.976             7.965
  Lag                        2.828             7.937             7.998             7.928
  MultiMMC (=Tirn T3)        2.739             7.929             7.956             7.940
  LZ78Y    (=Tirn T4)        2.791             7.930             7.956             7.939
  ------------------------------------------------------------------------------------
  FINAL H                    2.673             7.522             7.354             7.873
  IID pass                   11/19             19/19             19/19             19/19
```

(Min-entropy estimators ran on the full 1M samples; the IID battery used a
10,000-sample subset with 2,000 shuffles -- the quick-pass configuration.)

## What it shows

1. **The tests have real power against low entropy.** Raw jitter (A) scored 2.67
   bits and was rejected by 8 of the 19 IID statistics. The predictors did their
   job on a genuinely weak source.

2. **Conditioning manufactures the score.** The same jitter, conditioned the Linux
   way (B), jumps to 7.52 bits and passes all 19. The +4.85-bit gain cannot be real
   entropy -- by the data-processing inequality a deterministic function f satisfies
   H_inf(f(K)) <= H_inf(K). The conditioner did not add entropy; it hid the deficit.

3. **Real vs. fake seed is invisible.** Column C runs the identical pipeline on a
   FIXED, published seed -- it is fully predictable (regenerable byte-for-byte) yet
   scores 7.35 bits and passes 19/19, statistically indistinguishable from B (real
   entropy) and D (printed-key PRG). Only B used real entropy. The black box cannot
   tell them apart.

## Why, formally

The Linux output path is:

    R = ChaCha20(K, nu, 0) || ChaCha20(K, nu, 1) || ...
    where K = BLAKE2s(N_1 || N_2 || ... || N_t),
          K <- ChaCha20(K, nu, c)[0:256]  after each call (fast-key-erasure)

Once K is fixed, R is a deterministic function of K. Two theorems bracket the gap:

- **Data-processing inequality (min-entropy form).** H_inf(f(K)) <= H_inf(K).
  So the TRUE min-entropy of R is at most H_inf(K) -- exactly 0 in column C.
  Anchors: Cachin, PhD thesis, ETH Zurich 1997 (the citation NIST SP 800-90B
  uses for min-entropy); Dodis, Reyzin & Smith, "Fuzzy Extractors", SIAM J.
  Comput. 38(1):97-139, 2008; for the smooth version, Renner & Wolf, ISIT 2004,
  and Beaudry & Renner, arXiv:1107.0740, 2011. Operationalised inside 90B at
  Section 3.1.5 and Appendix D.

- **PRG indistinguishability (Yao 1982) + HILL pseudoentropy (Hastad-Impagliazzo-
  Levin-Luby 1999).** Modelling ChaCha20 as a PRF, every polynomial-time
  distinguisher -- and all 90B estimators ARE polynomial-time predictors -- fails
  to separate the keystream from uniform when K is secret. The output has HILL
  pseudo-min-entropy ~ |R| while its real min-entropy is <= H_inf(K). Every
  efficient statistical test measures the former and silently reports it as the
  latter. The reported-minus-real gap can be the entire output length.

The load-bearing condition is "K secret". The tests never see K, so they cannot
distinguish B from C. The backdoor holder DOES know K, and predicts column C with
probability 1.

## Consequence for RNG certification

Swapping a real-but-mediocre noise source for a backdoored generator keyed by a
value the attacker knows makes the 90B / AIS 31 numbers go UP. An evaluator
running only the black-box battery would read the improvement as the RNG getting
better, at the exact moment it became fully predictable. This is the operational
form of AIS 31 v3.0 Section 4.6.2: statistical tests can falsify a stochastic
model but never confirm one. A passing battery is necessary, never sufficient.
The only instrument that separates column B from column C is a physics-grounded
stochastic model establishing H_inf(K) > 0 -- precisely what a malicious build
defeats by leaving the conditioning untouched while zeroing the seed.

## Reproduce

    python3 compare_linux.py -n 1000000 -b 8

ChaCha20 verified against the RFC 8439 Section 2.3.2 test vector; the fixed-seed
stream (C) and the BLAKE stream (D) are byte-for-byte reproducible via predict.py.
