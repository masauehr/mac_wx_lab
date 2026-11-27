#!/bin/bash
# WRF v4.7.1（dmpar・gfortran/clang）を Mac(arm64) でビルドし、理想実験 em_quarter_ss を準備する。
# 依存は Homebrew を使わず、プロジェクト内の conda 環境（.conda）に隔離する。
# 使い方: プロジェクトルートで  bash physics/build_wrf.sh   （コンパイルは約30〜60分）
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 1. 依存（gfortran・netCDF-Fortran・MPICH など）
/opt/anaconda3/bin/conda create -y -p .conda -c conda-forge --override-channels \
  gfortran clang clangxx netcdf-fortran mpich libpng jasper zlib m4 tcsh make

export PATH="$ROOT/.conda/bin:$PATH" NETCDF="$ROOT/.conda"

# 2. WRF 取得（サブセルは compile 時に自動 clone される）
[ -d physics/WRF ] || git clone --depth 1 --branch v4.7.1 https://github.com/wrf-model/WRF.git physics/WRF
cd physics/WRF

# 3. configure: 21 = GNU (gfortran/clang) dmpar、ネスト = 1（基本）
printf "21\n1\n" | ./configure
# gfortran 16 向けに型不一致・BOZ 定数のエラーを許容
sed -i.orig 's/^FCBASEOPTS_NO_G =\(.*\)$/FCBASEOPTS_NO_G =\1 -fallow-argument-mismatch -fallow-invalid-boz/' configure.wrf

# 4. コンパイル（理想実験: 局地スーパーセル）
./compile -j 8 em_quarter_ss > compile_qss.log 2>&1
echo "完了: physics/WRF/main/{ideal,wrf}.exe"
