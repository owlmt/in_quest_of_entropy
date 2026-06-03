# Pseudoentropy demo

A stream that passes the standard entropy tests but has almost no real entropy.
SHAKE-256 (the XOF inside ML-KEM / ML-DSA) expands a 32-bit seed into 1 MB.
SP 800-90B scores it ~7.14 bits/byte — the same as Linux `/dev/urandom` — yet
the seed is recoverable by brute force in minutes.

## Run

    python3 code1_pseudoentropy.py     # build the stream; print real min-entropy
    python3 code2_tests_vs_linux.py    # 90B estimate + figure vs Linux RNG
    python3 code3_bruteforce.py        # recover the 32-bit seed (~12 min, 8 cores)

Optional full check with the NIST tool:

    ea_non_iid pseudo_sample.bin 8     # ~7.14 bits/byte

## Files

- `code1_pseudoentropy.py` — generator + real min-entropy (= seed-recovery cost)
- `code2_tests_vs_linux.py` — passes SP 800-90B; figure shows it identical to Linux RNG
- `code3_bruteforce.py` — recovers the seed from 16 output bytes; writes `bruteforce_evidence.txt`
- `pseudoentropy_figure.png` — the picture

## Why it works

Pseudoentropy is computationally indistinguishable from real entropy, so no
statistical test can detect it. Real entropy must be argued at the source
(BSI AIS 31 stochastic model), not measured from output bits.

## Papers / standards

- Håstad, Impagliazzo, Levin, Luby — *A PRG from Any One-Way Function*, SIAM J. Comput. 1999
- Haitner, Reingold, Vadhan — *Efficiency Improvements in Constructing PRGs from OWFs*, SIAM J. Comput. 2013
- Haitner, Mazor, Silbak — *Incompressibility and Next-Block Pseudoentropy*, ITCS 2023 — https://eprint.iacr.org/2022/278
- Haitner, Vadhan — *The Many Entropies in One-Way Functions*, 2017 — https://iftachh.github.io
- NIST SP 800-90B (2018); FIPS 202 (SHAKE); FIPS 203 (ML-KEM)
- ENISA *Agreed Cryptographic Mechanisms* (draft, Apr 2026), Notes 71–72
- BSI AIS 31 v3.0 (2024)
