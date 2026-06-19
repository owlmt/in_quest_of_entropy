# ML_AWS — Real-Hardware Predictability Study of Linux RNG Entropy Inputs

Empirical companion to *On the Entropy Ceiling of the Linux Deterministic Random Bit
Generator*. This directory tests, on real Amazon EC2 hardware, whether the entropy the
Linux kernel **credits** at each interrupt is predictable by a machine-learning adversary
— and therefore whether it constitutes genuine conditional min-entropy toward the DRBG
seed.

**Status: complete.** Capture, full ML pipeline, and GPU cross-check are done, committed,
and provenance-anchored. All EC2 instances have been terminated.

---

## Research question

For the credited per-event timing the kernel mixes via `add_interrupt_randomness()` /
`add_input_randomness()` / `add_disk_randomness()`, can any predictor — given realistic
adversary side information (coarse cadence, source/CPU identity, load, VM lifecycle) —
beat chance on the **next** value of:

- `delta_cycles` — the raw inter-arrival,
- `jitter` = `delta_cycles mod 2^10` — the low-order timing word,
- `lsb(jitter)` — the credited entropy bit?

A statistically significant predictor would mean the kernel credits entropy that an
adversary can remove, violating the legitimacy precondition of the seedless-robustness
security argument (Chung et al., 2024). A clean null supports the per-event leg of that
precondition **in the captured regime**.

---

## Methodology

**Capture (the only valid method).** Entropy inputs are read at the kernel boundary with
`bpftrace` kprobes on the three `add_*_randomness()` functions. Userspace RDTSC/jitter
tools capture execution-time jitter, *not* what the kernel RNG consumes, and are not used.

**Why AWS, not WSL2.** An idle WSL2 VM is a single-IRQ environment; its timing is
dominated by one periodic timer and is unrepresentative. We capture on a Nitro instance
under real, diverse load so the IRQ mix (NIC + NVMe across many CPUs) reflects a deployed
server.

**Capture host.** `c6in.4xlarge` (`i-03f35caaffd75850c`, eu-west-2), Ubuntu 24.04,
kernel `6.17.0-1017-aws`, `bpftrace 0.20.2`. Load: `fio` + `stress-ng` + 16 parallel
1 GB network download streams, lighting up 8 ENA Tx/Rx queues (IRQ vectors 26–33) and
`nvme0q1`/`nvme0q2` (34/35) across 10 CPUs.

**Capture run.** 300 s on the three kprobes → **1,662,184 events, 0 lost**, ≈50% NVMe /
≈50% NIC, 10 CPUs, 11 IRQ vectors. `bt_to_csv.py` reconstructs the kernel sample fields
(`cycles`, `delta_cycles`, `jitter`, `jitter16`, `jiffies`, `num`, per-source delta
cascade, `est_bits`) and was verified byte-for-byte (15/15 columns) against a WSL2 ground
truth before use.

**Quality gate (PASS).** On the 1,662,180-row AWS dataset: `jitter` autocorrelation ≈ 0
(unchanged vs WSL2); `delta_cycles` autocorrelation **collapsed 0.57 → 0.045** (the
single-timer carrier is gone with a real IRQ mix); `lsb` P(1) = 0.4994 (balanced, vs
0.416 on WSL2). The carrier and LSB bias seen on WSL2 were single-interrupt artifacts.

---

## Pipeline

`rng_pipeline.py` — processed-feature study. Per target: persistence / seasonal /
mean / majority baselines; window × feature-set sweep (W ∈ {4…512}, feature sets A–H,
Ridge + LightGBM probes); Optuna (100 trials/family) over 8 tabular families; GPU
sequence nets (CNN1D, TCN, LSTM, GRU, BiLSTM, Transformer at W ∈ {64, 256}); bootstrap and
Hanley–McNeil AUC confidence intervals; permutation tests; chronological splits; JSON
report.

`rng_raw.py` — raw per-stream study. Models each source (`num`) and CPU stream directly
(addresses the reviewer concern that processed features could be artifacts): per-stream
`dnsecs(t+1)` and `lsb(dnsecs)(t+1)`, next-source classification, and round-trip
reconstruction. Shuffle control permutes each series *before* windowing.

**Evaluation discipline.** ML verdict is read against the **majority-class base rate**
(not 0.5) for imbalanced bits; ROC-AUC is reported with a Hanley–McNeil CI; a persistence
baseline is printed beside every R². No synthetic / simulated / PRNG-derived data is used
anywhere — real hardware capture only.

---

## Results

Full processed pipeline, real Nitro capture, N = 1,662,180 events:

| Target | What it is | Best result | Controls | Verdict |
|---|---|---|---|---|
| `delta_cycles` | raw inter-arrival | R² = **+0.033** (XGBoost, W=128, fs=H) | skill-vs-persist 0.505, CI lower 0.4996; all sequence nets ≈ 0 | residual coarse carrier (~3%), **not entropy-bearing** |
| `jitter` | low-10 timing word | R² = **+4.5×10⁻⁵** (all models ≈ 0) | persistence R² = −1.006 | **null** |
| `lsb(jitter)` | credited entropy bit | AUC = **0.5039** (BiLSTM, W=64) | CI [0.4986, 0.5092] **crosses 0.5**, majority base 0.5008, MCC 0.005, perm p = 0.17 | **NO predictability** |

Raw per-stream study (decomposing the merged stream surfaces real *carrier* structure
that does **not** propagate to the payload): next-source prediction 0.466 acc vs 0.281
marginal (11 classes); coarse `dnsecs` R² up to 0.215 (per-CPU) and 0.15–0.16 (ENA
queues), ≈ 0 (NVMe). **Every** stream's credited-bit AUC ≈ 0.50 with CIs containing 0.5,
MCC ≈ 0, shuffle ≈ 0.50, round-trip correlation ≈ 0 — *including* per-source isolation.

**Reading.** Predictable carrier (which device, roughly when); unpredictable payload (the
credited sub-microsecond jitter). Coarse predictability does not reach the entropy bits.
Per-event predictability is therefore *rejected* in this benign multi-source regime — a
statistical rejection bounded by the CIs and the searched hypothesis class, not a proof.
This does **not** test the collapse regimes (VM clone/snapshot-rollback, adversary-clocked
timing, pool starvation) under which the count-based crediting rule fails to enforce
legitimacy; that gap is argued formally in the paper.

---

## GPU cross-check (cuPQC)

To rule out a GPU post-quantum library as a covert weak source: `cuPQC/entropy_probe.cu`
on `g4dn.2xlarge` (T4, CUDA 12.8) produced output statistically indistinguishable from
ideal — 8.000 bits/byte, zero collisions, no cross-instance correlation — with `strace`
showing host-seeded randomness via `getrandom` / `/dev/urandom`. (cuPQC 0.4.1 requires
`pk.hpp` + `workspace.hpp`, not just `cupqc.hpp`, and does not build under CUDA 13.x.)
Results and figures under `cuPQC/`.

---

## Provenance

`provenance/` anchors the dataset to real EC2:

- AWS-signed instance identity (`instance-identity.{json,pkcs7,sig}`) — cryptographic
  proof of `c6in.4xlarge` / eu-west-2 / `i-03f35caaffd75850c`.
- Hardware evidence: `dmidecode`, `/proc/interrupts`, `dmesg`, `ethtool`.
- `capture.bt`, `cloud-init.sh`, `capture_run.sh`, `SHA256SUMS`, 100k-row samples.
- `PROVENANCE.md` and `DATASET.md` (datasheet).

---

## Repository layout

```
ML_AWS/
├── README.md              # this file
├── bt_to_csv.py           # bpftrace log -> rng_hw.csv (verified vs ground truth)
├── rng_pipeline.py        # processed-feature study (baselines, sweep, Optuna, GPU nets)
├── rng_raw.py             # raw per-stream study
├── results/
│   ├── results.json       # machine-readable report
│   └── console.txt         # full run log incl. VERDICT block
├── cuPQC/                 # GPU entropy cross-check (harness, results, figures)
└── provenance/            # signed identity + hardware evidence + datasheet
```

---

## Reproduce

Capture host (Nitro, under load):

```bash
sudo bpftrace capture.bt > raw_events.log     # 300 s, three add_*_randomness kprobes
python bt_to_csv.py raw_events.log rng_hw.csv  # reconstruct kernel sample fields
```

ML host (GPU, DLAMI PyTorch):

```bash
source /opt/pytorch/bin/activate               # uses `python`, not `python3`
mkdir -p results
python rng_pipeline.py --csv rng_hw.csv --out results \
  --optuna-trials 100 --family-timeout 300 \
  --windows 4,8,16,32,64,128,256,512 --seq-windows 64,256 | tee results/console.txt
```

---

## Relation to the paper

These measurements are the empirical leg of the seed-security argument. The bridge
section (`drbg_empirical_bridge.tex`) connects them to the entropy-ceiling theorems:
the credited stream is treated as a block source whose per-event rate the kernel asserts
by **count**, not by measured conditional min-entropy. Under the legitimacy hypothesis
the seed has min{2^(n/2), 2^(λ/2)} = 2^128 security for n = λ = 256 (Chung et al., 2024,
with matching attacks). The empirical null supports the per-event leg of that hypothesis
in the benign regime; the runtime rule does not enforce it in general.
