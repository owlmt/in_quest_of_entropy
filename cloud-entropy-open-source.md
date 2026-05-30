# The state of open source in cloud entropy: AWS, Azure, and GCP

A companion note to *In Quest of Entropy*. The taxonomy in the main post asks,
for every entropy claim, **which layer the claim lives at and who you have to
trust**. This note applies that question to the three major public clouds and
answers a narrow, practical version of it: when a Linux VM on AWS, Azure, or
GCP gets its randomness, **which parts of that pipeline can you read as source
code, and which parts are a vendor black box?**

The short answer is the same for all three, and it is worth stating plainly
before the detail:

> The code that **consumes and manages** entropy inside the guest is open and
> auditable. The hardware/firmware that **generates** the entropy inside the
> hypervisor is closed. The trust boundary sits exactly at the hand-off from
> the host to the guest. What differs between the three clouds is only the
> *mechanism* of that hand-off, not whether the source is open.

This matters for the same reason the rest of *In Quest of Entropy* matters: a
security claim is only as good as the weakest layer you cannot inspect. For
cloud entropy, that weakest-to-inspect layer is identical across vendors.

## The pipeline, and where it goes closed

On every major cloud the chain on a Linux VM is:

    [host hardware RNG]  ->  [hand-off device]  ->  guest kernel CRNG  ->  /dev/urandom, getrandom(2)
        CLOSED (vendor)       OPEN (mainline)        OPEN (mainline)        OPEN (mainline)

Everything from the hand-off device rightward is mainline Linux: you can read
it, rebuild it, and audit it. Everything to the left of it — the silicon and
firmware that actually produce the bits — is proprietary to the cloud
provider. You can test its *behaviour* empirically, but you cannot read its
source.

## The guest consumer: identical and open on all three clouds

Regardless of cloud, a Linux guest manages entropy with the same mainline
code. These are the files that *use* the entropy once it arrives:

| Component | What it does | Source (license) |
|-----------|--------------|------------------|
| Core CRNG | implements `/dev/random`, `/dev/urandom`, `getrandom(2)`; pool mixing, reseed logic | [`drivers/char/random.c`](https://github.com/torvalds/linux/blob/master/drivers/char/random.c) (GPL-2.0 OR BSD-3-Clause) |
| HWRNG framework | registers hardware RNG sources, exposes `/dev/hwrng`, `rng_current`, `rng_available` | [`drivers/char/hw_random/core.c`](https://github.com/torvalds/linux/blob/master/drivers/char/hw_random/core.c) (GPL) |
| VM Generation ID | reseeds the CRNG on clone/snapshot-restore via `add_vmfork_randomness()` | [`drivers/virt/vmgenid.c`](https://github.com/torvalds/linux/blob/master/drivers/virt/vmgenid.c) (GPL-2.0) |
| Early/boot seeding | seeds the RNG early in boot from firmware before userspace runs | [`drivers/firmware/efi/libstub/random.c`](https://github.com/torvalds/linux/blob/master/drivers/firmware/efi/libstub/random.c) (GPL) |

This is the layer the companion proof-of-concept
(`k8s-entropy-poc`) actually probes: whether the guest reseeds on clone,
whether two cloned nodes diverge, whether health tests see a reused stream.
All of it is readable source, on every cloud.

## The hand-off device: open on the guest side, three different mechanisms

Each cloud hands entropy from its closed host into the open guest through a
different device. In all three cases the **guest-side driver is mainline and
open**; only what sits behind it is proprietary.

### Amazon Web Services (Nitro)

AWS exposes a hardware RNG through the **Nitro Secure Module (NSM)**. The guest
driver registers it as `nsm-hwrng` and the kernel pulls entropy from it. The
NSM driver was upstreamed in Linux 6.8.

- Guest driver (open): [`drivers/misc/nsm.c`](https://github.com/torvalds/linux/blob/master/drivers/misc/nsm.c) — the `nsm-hwrng` backend is defined here (the `nsm_rng_read` function and the `hwrng` registration).
- Userspace SDK (open, Apache-2.0): [`aws/aws-nitro-enclaves-sdk-c`](https://github.com/aws/aws-nitro-enclaves-sdk-c)
- Closed: the Nitro security chip and hypervisor firmware that produce the bits NSM serves.

Operational check inside a Nitro instance/enclave: confirm
`cat /sys/devices/virtual/misc/hw_random/rng_current` reports `nsm-hwrng`.

### Microsoft Azure (Hyper-V)

Azure runs on a customised **Hyper-V**. It seeds the guest two ways: Generation 2
(UEFI) VMs get early entropy via the standard EFI RNG protocol, and Hyper-V
additionally provides entropy through a custom ACPI table named **OEM0**,
refreshed on every boot, which the kernel mixes in with
`add_bootloader_randomness()` and then zeroes out.

- Guest seeding code (open): Hyper-V init in [`arch/x86/kernel/cpu/mshyperv.c`](https://github.com/torvalds/linux/blob/master/arch/x86/kernel/cpu/mshyperv.c) and the broader [`arch/x86/hyperv/`](https://github.com/torvalds/linux/tree/master/arch/x86/hyperv) tree (GPL).
- EFI early seeding (open): [`drivers/firmware/efi/libstub/random.c`](https://github.com/torvalds/linux/blob/master/drivers/firmware/efi/libstub/random.c)
- Upstream patch that added OEM0 seeding (2024): "x86/hyperv: Use Hyper-V entropy to seed guest random number generator" — see the LKML thread at <https://lkml.iu.edu/hypermail/linux/kernel/2403.2/01366.html>
- Closed: the Hyper-V host RNG that fills OEM0.

Note: OEM0 seeding for Generation 1 (BIOS) VMs is recent (2024 mainline). An
older Azure image on an older kernel may not have this path and would rely on
whatever the guest gathers itself.

### Google Cloud Platform (KVM)

GCP runs on a customised **KVM** hypervisor and uses the standard,
paravirtualised **virtio-rng** device. It is pre-enabled on all Linux VMs by
default and continuously refills the guest pool from the host. This is the
most transparent of the three hand-offs, because virtio-rng is a public,
standardised interface implemented by many open hypervisors — there is no
proprietary shim in the guest path at all.

- Guest driver (open): [`drivers/char/hw_random/virtio-rng.c`](https://github.com/torvalds/linux/blob/master/drivers/char/hw_random/virtio-rng.c) (GPL)
- Device/spec (open): virtio-rng in the VirtIO specification; QEMU reference backend
- Closed: Google's host-side RNG that feeds the virtio-rng backend.

Operational check: `lsmod | grep rng` should show `virtio_rng`; and
`cat /sys/devices/virtual/misc/hw_random/rng_current` should report `virtio_rng`.

## Side-by-side

| | AWS | Azure | GCP |
|---|-----|-------|-----|
| Hypervisor | Nitro | Hyper-V | KVM (customised) |
| Hand-off mechanism | Nitro Secure Module (`nsm-hwrng`) | OEM0 ACPI table + EFI RNG protocol | virtio-rng |
| Hand-off timing | on demand / periodic | each boot (+ early via EFI) | continuous |
| Guest driver | `drivers/misc/nsm.c` | `arch/x86/hyperv/`, EFI libstub | `drivers/char/hw_random/virtio-rng.c` |
| Guest driver open? | yes (upstream 6.8) | yes | yes |
| Generating source open? | no | no | no |
| `rng_current` value | `nsm-hwrng` | (mixed via bootloader/EFI) | `virtio_rng` |

## Why this is the right lens

Two conclusions fall out, and both reinforce the main post's thesis.

First, the part you can verify by **reading code** is the same on all three
clouds, and it is the larger part: how entropy is mixed, credited, reseeded,
and dispensed. The part you can only verify by **testing behaviour** is the
generating source, and it is the same black box on all three. So the realistic
assurance posture for cloud entropy is "audit the open consumer by reading;
audit the closed source by experiment" — which is exactly why empirical
proof-of-concept testing (does a clone actually reseed? do two nodes actually
diverge?) has value the source review cannot provide.

Second, because the guest consumer is open and identical, the platform's
*safe* behaviours are also the same across clouds. All three reseed cloned
VMs — by VM Generation ID, by per-boot OEM0/EFI seeding, or by a continuous
virtio-rng feed — so spontaneous CRNG reuse on a freshly launched node is
mitigated by construction everywhere. The residual risk is therefore **not**
the platform RNG; it is build-time material baked into an image before any of
this machinery runs, which collides byte-for-byte across the fleet regardless
of how good the live entropy feed is. That failure is cloud-independent,
because it happens above this entire pipeline. It is demonstrated in the
companion repository `k8s-entropy-poc`.

## Source index (quick links)

Consumes entropy (open, all clouds):
- Core CRNG — https://github.com/torvalds/linux/blob/master/drivers/char/random.c
- HWRNG framework — https://github.com/torvalds/linux/blob/master/drivers/char/hw_random/core.c
- VM Generation ID reseed — https://github.com/torvalds/linux/blob/master/drivers/virt/vmgenid.c
- EFI early seeding — https://github.com/torvalds/linux/blob/master/drivers/firmware/efi/libstub/random.c

Hands off entropy (open guest driver per cloud):
- AWS NSM — https://github.com/torvalds/linux/blob/master/drivers/misc/nsm.c
- AWS Nitro Enclaves SDK — https://github.com/aws/aws-nitro-enclaves-sdk-c
- Azure Hyper-V init — https://github.com/torvalds/linux/blob/master/arch/x86/kernel/cpu/mshyperv.c
- Azure Hyper-V tree — https://github.com/torvalds/linux/tree/master/arch/x86/hyperv
- GCP virtio-rng — https://github.com/torvalds/linux/blob/master/drivers/char/hw_random/virtio-rng.c

Generates entropy (closed, no public source): Nitro security chip (AWS),
Hyper-V host RNG (Azure), KVM host RNG (GCP).

*Links verified against torvalds/linux `master` at the time of writing. File
paths in the mainline kernel can move between versions; if a link 404s, search
the filename in the current tree.*
