"""比較用の参照データ取得: ECMWF IFS（HRES）と公式AIFS の同じ初期時刻の予報（オープンデータ・CC BY 4.0）。
使い方: python compare/fetch_reference.py 2026112618"""
import sys
from datetime import datetime

import truststore
from ecmwf.opendata import Client

truststore.inject_into_ssl()
init = datetime.strptime(sys.argv[1], "%Y%m%d%H")
steps = [0, 6, 12, 18, 24]
for model, kw in [("ifs", {"resol": "0p25"}), ("aifs-single", {})]:
    target = f"data/ref_{model}_{sys.argv[1]}.grib2"
    Client(source="ecmwf", model=model, **kw).retrieve(
        date=init.strftime("%Y%m%d"), time=init.hour, step=steps, type="fc",
        param=["msl", "2t", "10u", "10v", "tp"], target=target)
    print("取得:", target)
