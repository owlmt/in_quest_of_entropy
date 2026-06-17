# Results

Empirical characterization of NVIDIA cuPQC `get_entropy()` output (ML-KEM-512,
encapsulation path — the 32-byte message *m*). All experiments run 2026-06-17.

**Run environment**

| field | value |
|---|---|
| cuPQC SDK | 0.4.1 (x86_64) |
| GPU | NVIDIA Tesla T4 (compute 7.5) |
| CUDA | runtime 12.8 / driver 13.2 (built with the 12.8 toolkit) |
| Host | AWS EC2 g4dn.2xlarge, Ubuntu 24.04 (DLAMI), kernel 6.x |
| Build note | cuPQC 0.4.1 splits the API — `ML_KEM_512` lives in `pk.hpp`, `get_entropy`/`release_entropy` in `workspace.hpp` (not pulled by `cupqc.hpp`); requires CUDA 12.x (not 13.x); `-dlto` link needs ~15 GB RAM |

---

## Exp 1 — output battery (1 GiB sample)

`ent` on 1,073,741,824 bytes:

| metric | value | ideal |
|---|---|---|
| Entropy | **8.000000 bits/byte** | 8.0 |
| Optimum compression | 0% | 0% |
| Chi-square (df 255) | 254.31, exceeded 50.05% of the time | p ≈ 0.50 |
| Arithmetic mean | 127.5012 | 127.5 |
| Monte-Carlo π | error 0.01% | 0 |
| Serial correlation | 0.000004 | 0 |

**Verdict:** fail to reject H0 — output is statistically indistinguishable from
ideal uniform random. (Necessary, not sufficient: a screen, not a proof.)

## Exp 3a — cross-call collision

| field | value |
|---|---|
| blocks drawn M | 100,000 (one `get_entropy` call each — the liboqs per-op pattern) |
| block size | 256 bits |
| expected collisions (M²/2^257) | ≈ 4e-68 |
| **observed collisions** | **0** |

**Verdict:** fail to reject H0 — no seed reuse across independent operations.

## Exp 3c — seed provenance

`strace -f` of one `get_entropy` run caught:

```
getrandom("\xaf\x2e\x96\xd9\xa9\xbc\x89\x2c", 8, GRND_NONBLOCK) = 8
openat(..., "/dev/urandom", O_RDWR) = 38
```

**Finding:** the process reads the **host Linux CSPRNG** (`getrandom` + `/dev/urandom`)
— the entropy path is **host-seeded**, not device-self-seeded. The earlier
"unauditable / possibly novel surface" concern resolves toward: the GPU PQC
entropy chain reduces to the security of the Linux RNG.

**Caveat:** `strace -f` traces the whole process, so these syscalls are not yet
firmly attributed to `get_entropy` vs. CUDA runtime/driver/glibc init, and the
`getrandom` draw was only 8 bytes. Follow-up: trace 1 vs N `get_entropy` calls and
count how `getrandom`/`urandom` accesses scale, to confirm attribution and
determine per-call vs per-process seeding.

## Exp 4 — batch / cross-instance correlation

| field | value |
|---|---|
| N blocks (single batch) | 100,000 |
| H0 off-diagonal SD (1/√N) | ≈ 0.00316 |
| max off-diagonal \|r\| | 0.0094 (~3σ — expected max over 496 position-pairs) |
| bit positions with \|z\|>3 | 0 |

**Verdict:** fail to reject H0 — no inter-position / inter-instance structure.

---

## Overall conclusion

Across output statistics (Exp 1), collisions (3a), and correlation (4), cuPQC's
`get_entropy()` output is statistically indistinguishable from an ideal CSPRNG;
nothing anomalous surfaced. Provenance (3c) locates the trust root: the path
reads the **host Linux kernel RNG**, so the security of GPU-accelerated ML-KEM
key/encapsulation randomness reduces (modulo the attribution caveat) to the
security of `getrandom()` — not an opaque on-device generator.

These tests **bound, not certify**: a clean sweep is consistent with a sound
CSPRNG and cannot, by construction, distinguish one from a keyed generator with
identical output. The actionable result is the located dependency on the Linux
RNG and the falsification of the device-self-seeded hypothesis.

*Raw 1 GiB sample (`enc.bin`) not committed; regenerate via `scripts/run_all.sh`.*
