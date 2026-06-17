# Makefile for entropy_probe  (cuPQC SDK 0.4.1)
# ---------------------------------------------------------------------------
# Mirrors NVIDIA's official cuPQC sample build, with two 0.4.1 adjustments:
#   * libs renamed: libcupqc-pk.a / libcupqc-hash.a  ->  -lcupqc-pk -lcupqc-hash
#   * cuPQC 0.4.1 does NOT build under CUDA 13.x. Use the CUDA 12.8 (or 12.9)
#     toolkit. This Makefile auto-selects /usr/local/cuda-12.8/bin/nvcc if present
#     (as on the AWS DLAMI), else falls back to whatever nvcc is on PATH.
#
#   make CUPQC_DIR=$HOME/cupqc-sdk-0.4.1-x86_64
# ---------------------------------------------------------------------------
NVCC ?= $(shell [ -x /usr/local/cuda-12.8/bin/nvcc ] && echo /usr/local/cuda-12.8/bin/nvcc || \
                ([ -x /usr/local/cuda-12.9/bin/nvcc ] && echo /usr/local/cuda-12.9/bin/nvcc || echo nvcc))
CUPQC_DIR ?= $(HOME)/cupqc-sdk-0.4.1-x86_64
ARCH ?= native          # T4=sm_75; 'native' auto-detects the GPU present at build

NVCC_FLAGS = -dlto -arch=$(ARCH) -std=c++17 -O3
CUPQC_INCLUDE_DIR    = $(CUPQC_DIR)/include/cupqc
COMMONDX_INCLUDE_DIR = $(CUPQC_DIR)/include/

entropy_probe: entropy_probe.cu
	$(NVCC) $(NVCC_FLAGS) -L$(CUPQC_DIR)/lib/ -lcupqc-pk -lcupqc-hash \
	  -o entropy_probe entropy_probe.cu \
	  -I$(COMMONDX_INCLUDE_DIR) -I$(CUPQC_INCLUDE_DIR)

clean:
	rm -f entropy_probe
.PHONY: clean
