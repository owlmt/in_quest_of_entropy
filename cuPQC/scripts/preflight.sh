#!/usr/bin/env bash
# preflight.sh -- fail fast on the three things that bit us, BEFORE building.
#   1) GPU compute capability >= 7.0      (MX250 sm_61 was rejected)
#   2) nvcc is CUDA 12.x, NOT 13.x         (cuPQC 0.4.1 won't build on 13)
#   3) libcupqc-pk.a present in the SDK    (0.4.1 renamed the libs)
set -u
CUPQC_DIR="${CUPQC_DIR:-$HOME/cupqc-sdk-0.4.1-x86_64}"
fail=0

echo "== GPU =="
if command -v nvidia-smi >/dev/null; then
  nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader
  cc=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d ' ')
  major=${cc%%.*}; minor=${cc##*.}
  if [ "$major" -lt 7 ]; then echo "  [FAIL] compute cap $cc < 7.0 (cuPQC needs Volta+)"; fail=1
  else echo "  [ok] compute cap $cc"; fi
else echo "  [FAIL] nvidia-smi not found"; fail=1; fi

echo "== nvcc =="
NVCC="$([ -x /usr/local/cuda-12.8/bin/nvcc ] && echo /usr/local/cuda-12.8/bin/nvcc || \
        ([ -x /usr/local/cuda-12.9/bin/nvcc ] && echo /usr/local/cuda-12.9/bin/nvcc || command -v nvcc))"
if [ -z "$NVCC" ]; then echo "  [FAIL] no nvcc found"; fail=1
else
  ver=$("$NVCC" --version | sed -n 's/.*release \([0-9]*\.[0-9]*\).*/\1/p')
  echo "  using: $NVCC  (release $ver)"
  case "$ver" in
    12.*) echo "  [ok] CUDA 12.x" ;;
    13.*) echo "  [FAIL] CUDA $ver -- cuPQC 0.4.1 will not build. Use cuda-12.8:"
          echo "         export PATH=/usr/local/cuda-12.8/bin:\$PATH"; fail=1 ;;
    *)    echo "  [warn] unexpected CUDA $ver" ;;
  esac
fi

echo "== SDK =="
if [ -f "$CUPQC_DIR/lib/libcupqc-pk.a" ]; then echo "  [ok] $CUPQC_DIR/lib/libcupqc-pk.a"
else echo "  [FAIL] libcupqc-pk.a not under $CUPQC_DIR/lib (set CUPQC_DIR)"; fail=1; fi
[ -f "$CUPQC_DIR/include/cupqc/cupqc.hpp" ] && echo "  [ok] cupqc.hpp" || \
  { echo "  [FAIL] cupqc.hpp missing"; fail=1; }

echo
[ "$fail" -eq 0 ] && echo "PREFLIGHT PASSED -- safe to: make -C harness CUPQC_DIR=$CUPQC_DIR" \
                  || echo "PREFLIGHT FAILED -- fix the [FAIL] lines above before building."
exit $fail
