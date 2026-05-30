# Cloud entropy compared: use, production, dependencies, and what is closed

A comparison across AWS, Azure, and GCP along four axes: how a guest **uses**
entropy, how the platform **produces** it, what **parameters** decide whether
the result is good and unique, and which parts are **open or closed**.

A note on what can be known. The consumer side (how entropy is used) is
mainline Linux and fully verifiable from source. The producer side (how the
bits are generated) is proprietary on all three clouds, so that axis is
described from vendor documentation and reasonable inference, not from code.
Statements below are marked [verified from source/docs] or [inferred] where
the distinction matters.

## 1. How entropy is USED (the consumer)

This is identical on all three clouds, because it is the same mainline Linux
code regardless of where the VM runs. [verified from source]

- Applications call `getrandom(2)` or read `/dev/urandom` / `/dev/random`.
- The kernel CRNG (`drivers/char/random.c`) maintains a single entropy pool
  per node, mixes inputs into it, and expands them with a ChaCha-based DRBG.
- Inputs mixed in: interrupt timing and other device noise; the CPU
  instruction (RDRAND/RDSEED) if trusted; the platform hand-off device
  (virtio-rng / NSM / OEM0) once registered; early firmware seed (EFI).
- On clone or snapshot-restore, the VM Generation ID driver
  (`drivers/virt/vmgenid.c`) calls `add_vmfork_randomness()` to force a
  reseed so the cloned node does not continue the parent's stream.

Consequence that matters for the rest of this note: because the consumer is
shared and open, the **way entropy is used is not where the clouds differ**.
They differ only in how the bits are produced and handed over.

## 2. How entropy is PRODUCED (the source)

This is where the three diverge, and it is the closed part. What is public:

| | AWS | Azure | GCP |
|---|-----|-------|-----|
| Hypervisor | Nitro | Hyper-V (customised) | KVM (customised) |
| Stated host source | hardware RNG in the Nitro security chip [vendor docs] | host RNG feeding an ACPI table; KMS uses NIST SP800-90 DRBG seeded by a hardware source [vendor docs] | host entropy pool on the physical machine [vendor docs] |
| How it reaches the guest | Nitro Secure Module, exposed as `nsm-hwrng` | OEM0 ACPI table refreshed each boot; plus EFI RNG protocol at early boot | virtio-rng paravirtual device, continuous |
| Conditioning / DRBG design | not public [inferred: standard SP800-90-style] | not public for the host RNG [inferred] | not public for the host backend [inferred] |
| Physical noise source | not public | not public | not public |

Cross-cutting source on all three: the **CPU instruction** (Intel RDRAND/
RDSEED, AMD equivalent). It is available directly to the guest and the kernel
can mix it in. Intel/AMD document the high-level design, but the silicon is
closed, and it has a history of silent failure (e.g. AMD Zen 5 RDSEED
returning zero, CrossTalk/SRBDS cross-core leakage), which is why Linux only
*credits* it as full entropy when `random.trust_cpu` is set, and why
post-Snowden practice is to mix it rather than trust it alone.

Bottom line on production: **no cloud publishes the generator.** You get a
vendor assertion plus, at best, a NIST/AIS conformance claim. The only
independent check available to a tenant is empirical (does output actually
look uniform, and do clones actually diverge), not a source review.

## 3. What the result DEPENDS ON (parameters)

Whether a given VM ends up with good, unique entropy is decided by these
parameters. Most of them live in the open guest layer, so they are checkable;
the last one is orthogonal to the platform entirely.

1. **Guest kernel version.** VM Generation ID reseeding via ACPI needs kernel
   >= 5.18; the DeviceTree path needs >= 6.10. On older kernels the automatic
   reseed-on-clone does not happen, which is the main realistic exposure.
   [verified from source/docs]
2. **Whether the reseed signal fires on clone.** vmgenid only helps if the
   hypervisor actually changes the generation ID on snapshot/restore/migrate.
   AWS, Azure, and GCP all implement a fresh-randomness signal, but a custom
   image or an exotic clone path can bypass it.
3. **Whether the hand-off device is present and registered.** Check
   `cat /sys/devices/virtual/misc/hw_random/rng_current`: expect `nsm-hwrng`
   (AWS enclaves), `virtio_rng` (GCP, and many AWS/Azure Linux images), or an
   OEM0/EFI-credited boot seed (Azure). If none is registered, the guest
   falls back to slower self-gathered entropy.
4. **Trust flags.** `random.trust_cpu` (credit RDRAND immediately) and
   `random.trust_bootloader` (credit the firmware/EFI/OEM0 seed). These change
   how fast the CRNG reaches a seeded state and what it counts as entropy.
5. **Hand-off timing.** Boot-only (Azure OEM0), continuous (GCP virtio-rng),
   or on-demand (AWS NSM). Continuous feeds cover the post-clone window best;
   a boot-only seed relies on the reseed signal firing for clones.
6. **CPU RDRAND/RDSEED availability and trust.** Present on modern instance
   types; subject to the silent-failure caveats above.
7. **Image build hygiene.** Orthogonal to all of the above. If a secret, key,
   or seed file is baked into the node image at build time, every node built
   from that image shares it byte-for-byte, no matter how good the live
   entropy feed is, because the value was fixed before any of this machinery
   ran. This is the dominant residual risk and it is cloud-independent.

## 4. Open or closed: the verdict by layer

| Layer | AWS | Azure | GCP | Open? |
|-------|-----|-------|-----|-------|
| Application use of entropy | `getrandom`/`/dev/urandom` | same | same | open (mainline) |
| Kernel CRNG | `drivers/char/random.c` | same | same | open (GPL/BSD) |
| Reseed-on-clone | vmgenid | vmgenid | vmgenid | open (GPL) |
| Hand-off guest driver | `drivers/misc/nsm.c` | `arch/x86/hyperv/`, EFI libstub | `drivers/char/hw_random/virtio-rng.c` | open (GPL) |
| Hand-off mechanism/spec | NSM (custom, driver upstream) | OEM0 ACPI + EFI RNG protocol | virtio-rng (public spec) | open contract; GCP most standard |
| Host-side backend | Nitro firmware | Hyper-V host RNG | KVM host backend | closed |
| Physical entropy source | Nitro chip | host hardware | host hardware | closed |

The pattern is the same on every cloud: **open from the kernel up to and
including the guest-side hand-off driver; closed from the host backend down to
the silicon.** The clouds differ only in the hand-off mechanism (NSM vs OEM0
vs virtio-rng) and its timing (on-demand vs boot vs continuous), not in where
the open/closed line falls.

## 5. What this means for assurance

- The **use** of entropy is the same everywhere and fully auditable, so it is
  not a differentiator and not a risk you cannot inspect.
- The **production** of entropy is closed everywhere, so trust in it reduces
  to vendor assurance plus empirical testing. No source review is possible.
- The **dependencies** that decide quality and uniqueness live mostly in the
  open guest layer (kernel version, reseed signal, registered device, trust
  flags), so they are checkable per VM, and the safe defaults on a current
  managed image generally hold.
- The **dominant residual risk** is none of the platform machinery. It is
  build-time material baked into an image, which collides across the fleet
  regardless of cloud or entropy quality. That is the finding demonstrated in
  the companion repository `k8s-entropy-poc`, and this comparison shows why it
  is cloud-independent: it sits above the entire entropy pipeline that the
  three clouds implement in common.

## Source index

Open (consumer + hand-off, all verified against torvalds/linux master):
- CRNG: https://github.com/torvalds/linux/blob/master/drivers/char/random.c
- HWRNG framework: https://github.com/torvalds/linux/blob/master/drivers/char/hw_random/core.c
- vmgenid reseed: https://github.com/torvalds/linux/blob/master/drivers/virt/vmgenid.c
- EFI early seed: https://github.com/torvalds/linux/blob/master/drivers/firmware/efi/libstub/random.c
- AWS NSM: https://github.com/torvalds/linux/blob/master/drivers/misc/nsm.c
- AWS Nitro Enclaves SDK: https://github.com/aws/aws-nitro-enclaves-sdk-c
- Azure Hyper-V: https://github.com/torvalds/linux/blob/master/arch/x86/kernel/cpu/mshyperv.c and https://github.com/torvalds/linux/tree/master/arch/x86/hyperv
- GCP virtio-rng: https://github.com/torvalds/linux/blob/master/drivers/char/hw_random/virtio-rng.c

Closed (no public source): Nitro chip/firmware (AWS), Hyper-V host RNG (Azure),
KVM host backend (GCP), CPU RDRAND/RDSEED silicon (Intel/AMD).
