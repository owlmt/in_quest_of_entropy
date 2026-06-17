#!/usr/bin/env bash
# Runs Exp 1, 3a, 4 end-to-end. Build the harness first (see harness/Makefile).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROBE="${PROBE:-$HERE/harness/entropy_probe}"
VARIANT="${VARIANT:-encaps}"
mkdir -p "$HERE/data"

echo "== Exp 1: dump =="
"$PROBE" dump --variant "$VARIANT" --out "$HERE/data/enc" --target-bytes "${BYTES:-1073741824}"
python3 "$HERE/analysis/analyze.py" dump "$HERE/data/enc"
echo "  -> now run external batteries, e.g.:"
echo "     PractRand:  RNG_test stdin < $HERE/data/enc.bin"
echo "     ent:        ent $HERE/data/enc.bin"
echo "     NIST STS:   assess 1048576 < $HERE/data/enc.bin"

echo "== Exp 3a: collision =="
"$PROBE" collision --variant "$VARIANT" --out "$HERE/data/col" --calls "${CALLS:-5000000}" --per-call 1 || \
  echo "  (nonzero exit => collision detected; see data/col.collisions.txt)"
python3 "$HERE/analysis/analyze.py" collision "$HERE/data/col"

echo "== Exp 4: batchcorr =="
"$PROBE" batchcorr --variant "$VARIANT" --out "$HERE/data/bc" --n "${N:-100000}"
python3 "$HERE/analysis/analyze.py" batchcorr "$HERE/data/bc"

echo "Done. Figures in data/*.png ; fill results/RESULTS.md"
