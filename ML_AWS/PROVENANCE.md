# Provenance — authenticity of the capture

**Claim.** `raw_events.csv` / `rng_hw.csv` are real Linux kernel entropy-input timings captured
on a genuine AWS EC2 host. No value is synthetic, simulated, or PRNG-derived.

## Cryptographic proof it is a real EC2 instance (not a simulator)
`provenance/instance-identity.{json,sig,pkcs7}` is AWS's signed instance identity document.
Verify against AWS's regional public certificate (see AWS docs: "instance identity documents"):

    openssl smime -verify -in provenance/instance-identity.pkcs7 -inform PEM \
        -certfile aws-public-cert.pem -noverify

A successful verification proves the document (instance-id, type c6in.4xlarge, region eu-west-2,
AMI, pendingTime) was signed by AWS for a running instance — something a local simulator cannot produce.

## Corroborating hardware evidence (in provenance/)
- `dmidecode_system.txt`, `dmi_id.txt` — sys_vendor = "Amazon EC2", product = the instance type.
- `proc_interrupts.txt` — real MSI-X device IRQs (ena-Tx-Rx queues, nvme0q1/q2). Their vector
  numbers are exactly the values in the dataset's `num` column (26-35): the data's IRQ identities
  match the live hardware.
- `dmesg_drivers.txt` — ENA + NVMe driver initialisation and the Nitro hypervisor.
- `ethtool_ens5_stats.txt`, `proc_net_dev.txt` — multi-GB real inbound traffic counters from the
  download load that generated the RX interrupts.
- `kprobes_available.txt`, `btf.txt`, `bpftrace_version.txt`, `capture.bt` — capture was done by
  bpftrace kprobes on the kernel's `add_*_randomness` entropy path (BTF-verified), reading what the
  kernel actually consumes — not a userspace generator.

## Statistical hallmarks of genuine concurrent hardware
- Multi-CPU out-of-order timestamp interleaving (per-CPU perf-buffer flush), corrected by a stable
  sort in `bt_to_csv.py` — an artifact only real concurrent interrupts produce.
- Per-source delta cascade across 10 distinct CPUs and 11 IRQ vectors.

## Integrity
`SHA256SUMS.txt` pins the full `raw_events.csv` / `rng_hw.csv`; the 100k-row samples in this folder
are the head of those files and hash-consistent with them.
