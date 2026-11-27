"""比較・天気図用の参照データ取得: ECMWF IFS（HRES）と公式AIFS の同じ初期時刻の予報（オープンデータ・CC BY 4.0）。
地上（msl・2t・10u・10v・tp・tcc・sp）と、13の気圧面（gh・t・u・v・w と、IFSは r・AIFSは q）。
使い方: python compare/fetch_reference.py 2026112618"""
import sys
from datetime import datetime

import truststore
from ecmwf.opendata import Client

truststore.inject_into_ssl()
LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]
SFC = ["msl", "2t", "10u", "10v", "tp", "tcc", "sp"]
PL = {"ifs": ["gh", "t", "u", "v", "r", "w"], "aifs-single": ["gh", "t", "u", "v", "q", "w"]}


def main():
    init = datetime.strptime(sys.argv[1], "%Y%m%d%H")
    steps = [0, 6, 12, 18, 24]
    for model, kw in [("ifs", {"resol": "0p25"}), ("aifs-single", {})]:
        c = Client(source="ecmwf", model=model, **kw)
        base = f"data/ref_{model}_{sys.argv[1]}"
        c.retrieve(date=init.strftime("%Y%m%d"), time=init.hour, step=steps, type="fc", param=SFC,
                   target=base + ".grib2")
        c.retrieve(date=init.strftime("%Y%m%d"), time=init.hour, step=steps, type="fc", param=PL[model],
                   levtype="pl", levelist=LEVELS, target=base + "_pl.grib2")
        print("取得:", base)


if __name__ == "__main__":
    main()
