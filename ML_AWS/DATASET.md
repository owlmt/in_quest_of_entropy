# Datasheet — Linux RNG entropy-input capture (AWS, 2026-06-19)

## Summary
Per-event timing of the Linux kernel's runtime entropy inputs, captured live on a real EC2 host
under real network + storage load. Used to test whether future entropy contributions are predictable.

## Generation environment
- Instance: c6in.4xlarge (Nitro), region eu-west-2, Ubuntu 24.04, kernel 6.17.0-1017-aws.
- Tracer: bpftrace v0.20.2 via kprobes on add_interrupt/disk/input_randomness (BTF-backed).
- Load (drives diverse IRQs): 16 parallel inbound HTTPS streams (ENA RX queues) + fio random NVMe I/O
  + stress-ng timers. Real traffic only.

## Capture method (`provenance/capture.bt`)
Prints `source,cpu,nsecs,a,b,c` per probe hit; `nsecs` is the kernel ktime at the event.

## Schema & derivation (`provenance/bt_to_csv.py`, verified byte-for-byte vs prior pipeline)
raw_events.csv: source, cpu, nsecs, a(=irq), b, c.
rng_hw.csv: idx, source, cpu, jpos, jitter, cycles, delta_cycles, jitter16, jiffies, num,
delta1..3, min_delta, est_bits, where cycles=nsecs, delta_cycles=diff(cycles),
jitter=low10(delta_cycles), jitter16=low16(cycles), jiffies=cycles//1000, per-source delta cascade,
est_bits=min(floor(log2(min_delta)),11). Leakage columns (cycles/jiffies/idx/jitter16) are never ML features.

## Size & integrity
1,662,180 events. Full-file SHA-256 in `provenance/SHA256SUMS.txt`. This repo ships a 100k-row sample
(`provenance/sample_*_100k.csv.gz`); the full capture is archived separately (Zenodo/S3) — link TBD.

## How to reproduce
1. Launch c6in.4xlarge, Ubuntu 24.04; install bpftrace fio iperf3 stress-ng.
2. `echo 1 | sudo tee /sys/block/nvme0n1/queue/add_random`.
3. Start the load (16 curl streams + fio + stress-ng), then
   `sudo timeout 300 bpftrace capture.bt > raw_events.csv`.
4. `python3 bt_to_csv.py raw_events.csv rng_hw.csv`.
5. Run the quality gate (CPUs>=4, IRQ vectors>=4 & top<0.6, jitter==low10(delta), events>=200k).

## Quality-gate result (this capture)
PASS — 10 CPUs, 11 IRQ vectors (top 28.8%), invariant holds, jitter autocorr ~0,
delta_cycles autocorr 0.045, LSB(jitter) P(1)=0.4994.

## Intended use / limits
For studying predictability of kernel entropy inputs. bpftrace printf adds small per-event latency
(observer effect), noted as a known limitation; it adds noise, not spurious predictability.
