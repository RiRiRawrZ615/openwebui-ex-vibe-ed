#!/bin/bash
R=/home/amd/strata
L="$R/.venv/lib/python3.11/site-packages/_rocm_sdk_devel"
INC="$L/include"; LIB="$L/lib"; LLVM="$L/lib/llvm"
BC="$L/lib/llvm/amdgcn/bitcode"
echo "=== compile (device libs at $BC) ==="
"$LLVM/bin/amdclang++" --offload-arch=gfx1030 \
  --hip-device-lib-path="$BC" \
  --rocm-path="$L" \
  --driver-mode=g++ --hip-link \
  -std=c++17 -O2 \
  -I"$INC" -I"$INC/rocblas" \
  -x hip "$R/tools/hip/tune_rocblas.cpp" \
  -L"$LIB" -lrocblas -lamdhip64 \
  -Wl,-rpath,"$LIB" \
  -o /tmp/xfix/tune_rocblas 2>&1 | grep -vE 'warning:|^$|notice:|^ ' | tail -15
ls -la /tmp/xfix/tune_rocblas 2>&1
