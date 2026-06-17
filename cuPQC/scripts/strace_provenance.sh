#!/usr/bin/env bash
# Exp 3c: does get_entropy() pull from the Linux RNG on the host?
# Traces getrandom / urandom / device reads during a single batched draw.
set -euo pipefail
PROBE="${1:?usage: strace_provenance.sh ./harness/entropy_probe}"
OUT="${OUT:-data/strace.log}"
mkdir -p "$(dirname "$OUT")"
echo "[strace] tracing entropy-relevant syscalls ..."
strace -f -e trace=getrandom,openat,read -yy \
  "$PROBE" batchcorr --variant encaps --out data/_prov --n 1024 2> "$OUT" || true
echo "[strace] hits:"
grep -nE 'getrandom|/dev/u?random|hwrng|/dev/random' "$OUT" || \
  echo "  (none) -> no host Linux-RNG reads observed; likely device-self-seeded or cached."
echo "[strace] full log: $OUT"
