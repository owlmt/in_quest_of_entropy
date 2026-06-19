# ML_AWS — Predictability of a Linux RNG Entropy Source (AWS redo)

Empirical re-run of the entropy-predictability study on a real Linux host on AWS,
replacing the earlier idle WSL2 capture. No synthetic, simulated, or PRNG-derived
data is used at any stage; the only admissible input is real kernel-traced entropy.

## Research question
Can future entropy contributions be predicted from past observations? Targets, always
future (t+1): `delta_cycles(t+1)`, `jitter(t+1)`, `LSB(jitter(t+1))`. Same-row
deterministic reconstructions are excluded by construction.

## Background (prior WSL2 result, degenerate environment)
WSL2 produced an idle, single-interrupt VM dominated by one near-periodic CPU-0 timer.
Findings there: `jitter(t+1)` unpredictable (R^2 ~ 0 across all models/windows/features);
`LSB(jitter(t+1))` indistinguishable from chance from jitter history (AUC ~ 0.50,
permutation p ~ 0.57); `delta_cycles(t+1)` ~25% predictable but only from delta history
— the coarse periodic carrier, decoupled from the entropy word (prediction RMSE >> the
10-bit mask). The environment is unrepresentative, which motivates this AWS redo.

## Methodology
- Capture (only valid source): bpftrace kprobes on `add_interrupt_randomness`,
  `add_input_randomness`, `add_disk_randomness`. Real workload drives diverse IRQs
  (iperf3 = NIC, fio = NVMe, stress-ng = scheduler/IPI). Userspace RDTSC jitter is NOT used.
- Leakage discipline: monotonic-timestamp columns (`cycles`, `jiffies`, `idx`, `nsecs`,
  `jitter16`) are NEVER features. Invariant checked every capture: `jitter == low10(delta_cycles)`.
- Validation: chronological 70/15/15, never shuffle. Baselines: persistence,
  seasonal-naive (dominant period), majority-class / mean. Controls: shuffle,
  permutation test. CIs by bootstrap; AUC with Hanley–McNeil CI.
- Metrics: next-bit — AUC, PR-AUC, balanced accuracy, MCC, Brier; value — MAE, RMSE,
  R^2, skill-vs-persistence.
- Model search: linear (Ridge/ElasticNet/Logistic), tree (RF/ExtraTrees/XGBoost/
  LightGBM/CatBoost), neural (MLP/CNN-1D/TCN/LSTM/GRU/BiLSTM/Transformer/Informer/TFT),
  probabilistic (HMM/Bayes net), time-series (ARIMA/SARIMA/Prophet). Windows 4–512.
  Feature sets A–H (raw lags, spectral/FFT-wavelet, autocorr, entropy-rate, multi-res).
  Optuna >= 100 trials per family.
- Stopping rule: no statistically significant gain over persistence/seasonal/majority
  for three consecutive search rounds.

## Resources
- Capture host: c6in.4xlarge (Nitro, network-optimized), eu-west-2, Ubuntu 24.04.
  Bare-metal upgrade (c5n.metal) pending On-Demand Standard vCPU quota L-1216C47A -> 80.
- ML host (post quality-gate): g5.4xlarge (A10G 24 GB, 16 vCPU, 64 GB RAM) for the
  full grid incl. GPU sequence models and Optuna.
- Tracing: bpftrace; conversion: bt_to_csv.py; analysis: pandas/numpy/scikit-learn,
  XGBoost/LightGBM/CatBoost, PyTorch, statsmodels.

## Plan
1. Launch real-Linux capture host; pre-install tracer + load tools.
2. Drive real interrupt load (iperf3, fio, stress-ng) — no synthetic data.
3. Capture kprobes on add_interrupt/input/disk_randomness.
4. Convert to raw_events.csv + rng_hw.csv on host.
5. Download both to the ML host.
6. QUALITY GATE before any ML (see below).
7. Launch GPU ML host only if gate passes.
8. Run full model/window/feature/Optuna grid with all controls and CIs.
9. Apply stopping rule; record the verdict.
10. Tear down all instances; commit results here.

## Quality gate (must pass before ML)
Source/CPU diversity (not single-IRQ), event count, monotonic-column sanity,
leakage-column exclusion, `jitter == low10(delta_cycles)` invariant, per-source autocorr.
A capture that collapses to a single periodic carrier is rejected and recaptured.

## Milestone log (facts only)
- 2026-06-19  AWS CLI configured (eu-west-2).
- 2026-06-19  vCPU quota: Standard L-1216C47A -> 80 approved; G/VT L-DB2E81BA -> 32 requested.
- 2026-06-19  Capture host: c6in.4xlarge, Ubuntu 24.04, kernel 6.17.0-1017-aws; bpftrace v0.20.2, BTF present.
- 2026-06-19  RNG kprobes verified: add_interrupt/input/disk/device/timer/hwgenerator/vmfork_randomness.
- 2026-06-19  Diversity validated under real load (16 NIC streams + fio NVMe): 10 CPUs, 10 IRQ vectors
              (8 ENA Tx-Rx queues 26-33 + nvme0q1/q2 34-35), 0 lost events.
- 2026-06-19  Real timed capture: 1,662,184 IRQ events in 300s (~5,540/s), 0 lost. ~50% NVMe / ~50% NIC.
- 2026-06-19  bt_to_csv.py verified: reproduces prior WSL2 rng_hw.csv byte-for-byte (15/15 cols, 921,872 rows);
              cascade is per-source; est_bits = min(floor(log2(min_delta)), 11).
- 2026-06-19  QUALITY GATE PASS on AWS rng_hw.csv: 1,662,180 events, 10 CPUs, 11 IRQ vectors (top 28.8%),
              invariant holds, cycles monotonic, 11 zero-delta rows.
- 2026-06-19  KEY FACTS vs WSL2: jitter autocorr ~0 (unchanged); delta_cycles autocorr collapsed
              0.57 (WSL2) -> 0.045 (multi-IRQ); LSB(jitter) P(1)=0.4994 (balanced) vs 0.416 WSL2.
              -> WSL2 carrier + LSB bias were single-periodic-interrupt artifacts; only jitter (~0) remains.
- 2026-06-19  Provenance bundle committed (29 files): AWS-signed instance identity (pkcs7),
              dmidecode/proc_interrupts/dmesg/ethtool hardware evidence, capture.bt, bt_to_csv.py,
              SHA256SUMS of full files, 100k-row samples. See ML_AWS/PROVENANCE.md + DATASET.md.
- PENDING     GPU ML host + full pipeline.
- PENDING     Scientific verdict + teardown.
