#!/bin/bash
# WRF実事例（沖縄周辺9km・GFS初期値・24h）を最初から最後まで実行する。
# 前提: physics/build_wrf.sh（WRF em_real）と WPS のビルド、data/geog_dl/WPS_GEOG_LOW_RES、
#       data/gfs/<初期時刻>/ にGFS（physics/fetch_gfs.py で取得）があること。
# 使い方: プロジェクトルートで  bash physics/run_case.sh physics/cases/okinawa_2026112618 2026112618
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CASE="$ROOT/$1"; INIT="$2"
export PATH="$ROOT/.conda/bin:$PATH"
cd "$CASE"
W="$ROOT/physics/WRF"; P="$ROOT/physics/WPS"
mkdir -p geogrid metgrid
cp -f "$P/geogrid/GEOGRID.TBL.ARW" geogrid/GEOGRID.TBL
cp -f "$P/metgrid/METGRID.TBL.ARW" metgrid/METGRID.TBL
ln -sf "$P/ungrib/Variable_Tables/Vtable.GFS" Vtable
ln -sf "$P/geogrid.exe" "$P/ungrib.exe" "$P/metgrid.exe" "$W/main/real.exe" "$W/main/wrf.exe" .
for f in "$W"/run/{LANDUSE.TBL,VEGPARM.TBL,SOILPARM.TBL,GENPARM.TBL,RRTMG_LW_DATA,RRTMG_SW_DATA}; do ln -sf "$f" .; done

rm -f GFS:* met_em* GRIBFILE.*
./geogrid.exe > geogrid.log 2>&1
"$P/link_grib.csh" "$ROOT"/data/gfs/"$INIT"/gfs.t*z.pgrb2.0p25.f0* > /dev/null
./ungrib.exe > ungrib.log 2>&1
./metgrid.exe > metgrid.log 2>&1
find . -maxdepth 1 \( -name "GFS:*" -o -name "GRIBFILE.*" \) -delete    # WPSの中間ファイル（約6.6GB）。metgridの後は不要（表示・作図は使わない）
mpirun -np 1 ./real.exe > real.log 2>&1
mpirun -np 6 ./wrf.exe > wrf_run.log 2>&1      # 6プロセス: 24h約417秒（M5 Pro）
tail -2 rsl.out.0000
