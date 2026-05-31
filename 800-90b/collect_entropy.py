#!/usr/bin/env python3
"""
collect_entropy.py - Linux-kernel-style entropy collection in user space.

Mimics the kernel's jitter entropy noise source. In recent kernels
(drivers/char/random.c + crypto/jitterentropy*.c), the noise source measures
the timing of a small fixed memory-access payload; the low bits of the delta
carry entropy from CPU pipeline/cache/branch-predictor non-determinism.

This is a faithful user-space approximation. The kernel's other source --
interrupt-arrival-time jitter via add_interrupt_randomness() -- is not
reproducible from user space because user space doesn't see interrupts.

References:
  - Stephan Mueller, "CPU Time Jitter Based Non-Physical True Random Number
    Generator", BSI 2014
  - drivers/char/random.c, fast_mix(), Linux 6.x
  - BSI AIS 31 v3.0, NTG.1 / non-physical NPTRNG
"""

import ctypes
import os
import time
import argparse
import sys
import collections
import math

# ---------------------------------------------------------------
# High-resolution monotonic timer via libc clock_gettime.
# Python's time.perf_counter_ns() works but adds ~100ns overhead;
# the direct libc call is closer to what the kernel's
# random_get_entropy() does (read TSC / equivalent).
# ---------------------------------------------------------------

CLOCK_MONOTONIC_RAW = 4

class _timespec(ctypes.Structure):
    _fields_ = [('tv_sec', ctypes.c_long),
                ('tv_nsec', ctypes.c_long)]

try:
    _libc = ctypes.CDLL('libc.so.6', use_errno=True)
    _clock_gettime = _libc.clock_gettime
    _clock_gettime.argtypes = [ctypes.c_int, ctypes.POINTER(_timespec)]
    _clock_gettime.restype = ctypes.c_int
    _have_libc = True
except OSError:
    _have_libc = False

def get_ns():
    if _have_libc:
        ts = _timespec()
        _clock_gettime(CLOCK_MONOTONIC_RAW, ctypes.byref(ts))
        return ts.tv_sec * 1_000_000_000 + ts.tv_nsec
    return time.perf_counter_ns()


# ---------------------------------------------------------------
# Jitter payload: small LFSR loop. The kernel's analogue lives in
# crypto/jitterentropy.c (jent_lfsr_time / jent_memaccess).
# What matters is that the work has timing variance from
# microarchitectural effects; the payload itself doesn't carry entropy.
# ---------------------------------------------------------------

def _payload(state, iters=64):
    for _ in range(iters):
        state ^= (state << 13) & 0xFFFFFFFFFFFFFFFF
        state ^= (state >> 7)
        state ^= (state << 17) & 0xFFFFFFFFFFFFFFFF
    return state


def collect(n_samples, bits=8, work_iters=64, verbose=True):
    """
    Collect n_samples; each sample is the low `bits` bits of a delta-ns.
    Equivalent to one round of the kernel's jitter measurement.
    """
    if not (1 <= bits <= 8):
        raise ValueError("bits must be in [1,8]")
    mask = (1 << bits) - 1
    out = bytearray(n_samples)
    state = 0xA3B1C2D4E5F60718

    # Warmup so first measurements aren't cold-cache outliers
    for _ in range(2000):
        state = _payload(state, work_iters)

    chunk = max(n_samples // 20, 1)
    for i in range(n_samples):
        t1 = get_ns()
        state = _payload(state, work_iters)
        t2 = get_ns()
        out[i] = (t2 - t1) & mask
        if verbose and i and i % chunk == 0:
            print(f"  ... {i:>10,} / {n_samples:,} ({100*i/n_samples:5.1f}%)",
                  file=sys.stderr)
    return bytes(out)


def quick_stats(data, bits):
    counts = collections.Counter(data)
    k_actual = len(counts)
    k_max = 2**bits
    p_max = max(counts.values()) / len(data)
    naive_h = -math.log2(p_max)
    top5 = counts.most_common(5)
    return {
        'L': len(data),
        'k_used': k_actual,
        'k_max': k_max,
        'p_max': p_max,
        'naive_min_entropy': naive_h,
        'top5': top5,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument('-n', '--samples', type=int, default=1_000_000,
                   help='Number of raw samples (default 1M, the SP 800-90B minimum)')
    p.add_argument('-b', '--bits', type=int, default=8,
                   help='Bits per sample (default 8; low bits of delta-ns)')
    p.add_argument('-w', '--work', type=int, default=64,
                   help='LFSR iterations per measurement (default 64)')
    p.add_argument('-o', '--output', default='raw_samples.bin')
    p.add_argument('--quiet', action='store_true')
    args = p.parse_args()

    print(f"# Source: jitter timing, {args.work} LFSR iters/sample, "
          f"{args.bits} low bits per delta-ns")
    print(f"# Target: {args.samples:,} samples -> {args.output}")
    t0 = time.perf_counter()
    data = collect(args.samples, args.bits, args.work, verbose=not args.quiet)
    elapsed = time.perf_counter() - t0

    with open(args.output, 'wb') as f:
        f.write(data)

    rate = args.samples / elapsed
    print(f"# Done: {len(data):,} bytes in {elapsed:.2f}s ({rate:,.0f} samples/s)")

    s = quick_stats(data, args.bits)
    print(f"#")
    print(f"# Distinct values used:        {s['k_used']} / {s['k_max']}")
    print(f"# Most common value freq:      {s['p_max']:.4f}")
    print(f"# Naive MCV min-entropy:       {s['naive_min_entropy']:.4f} bits/sample")
    print(f"# Top 5 by frequency:          {s['top5']}")
    print(f"#")
    print(f"# Next: python3 nist_90b_tests.py {args.output}")


if __name__ == '__main__':
    main()
