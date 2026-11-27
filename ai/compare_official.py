"""手元のAIFS出力を、ECMWF公式のAIFS（オープンデータ）と比べる。
使い方: python ai/compare_official.py <YYYYMMDDHH> <step時間> <npzのパス>
"""
import sys
from datetime import datetime

import earthkit.data as ekd
import earthkit.regrid as ekr
import numpy as np
from ecmwf.opendata import Client

init, step, path = datetime.strptime(sys.argv[1], "%Y%m%d%H"), int(sys.argv[2]), sys.argv[3]
mine = np.load(path)
target = f"data/official_aifs_{sys.argv[1]}_{step}.grib2"
Client(source="ecmwf", model="aifs-single").retrieve(
    date=init.strftime("%Y%m%d"), time=init.hour, step=step, type="fc",
    param=["msl", "2t"], target=target)
for f in ekd.from_source("file", target):
    key = f.metadata("param")
    v = np.roll(f.to_numpy(), -f.shape[1] // 2, axis=1)
    v = ekr.interpolate(v, {"grid": (0.25, 0.25)}, {"grid": "N320"})
    m = mine[key]
    print(f"{key}: 公式 min/max {v.min():.1f}/{v.max():.1f} | 手元 {m.min():.1f}/{m.max():.1f} | "
          f"RMSE {np.sqrt(np.mean((v - m) ** 2)):.2f} bias {np.mean(m - v):.2f}")
