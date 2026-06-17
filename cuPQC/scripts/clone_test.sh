#!/usr/bin/env bash
# Exp 3b: clone determinism. Draws an entropy buffer in K independent runs, then
# diffs. Identical buffers across true clones => the seed was cloned
# (catastrophic for VM-snapshot / container-fork deployments).
#
# Two ways to create the shared snapshot, depending on your setup:
#   (A) GPU-passthrough VM: snapshot the VM just before the call, restore K
#       copies, run ONE draw in each, copy each data/clones/clone_$i.matrix.bin
#       out, then run the sha256 diff below. This is the real test.
#   (B) Same-host back-to-back (weak baseline; NOT a true clone): catches
#       fixed/missing seeding only. This script does (B) by default.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROBE="${PROBE:-$HERE/harness/entropy_probe}"
K="${K:-4}"
mkdir -p "$HERE/data/clones"
for i in $(seq 1 "$K"); do
  "$PROBE" batchcorr --variant encaps --out "$HERE/data/clones/clone_$i" --n 1024 >/dev/null
done
echo "[clone] sha256 of each clone buffer (matches => cloned/fixed seed):"
sha256sum "$HERE"/data/clones/clone_*.matrix.bin
uniq=$(sha256sum "$HERE"/data/clones/clone_*.matrix.bin | awk '{print $1}' | sort -u | wc -l)
echo "[clone] unique buffers: $uniq / $K"
[ "$uniq" -lt "$K" ] && echo "[!!] fewer unique than runs => seed reuse under THIS configuration"
echo "  (For a real result, generate each clone from a restored pre-call VM snapshot.)"
