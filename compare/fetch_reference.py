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
PLZ = ["z"]      # 公式AIFS（AWSミラーの2025年分）は gh が無く、高度は z（ジオポテンシャル）。別ファイル _plz.grib2 に取得し、読み込み時に gh へ換算する


# 公開期間（2026-11-27 に確認）: 本家 data.ecmwf.int は直近約3日。AWSミラーは IFS 0.25度が2024-02-29〜、AIFS が2025-02-26〜
MIN_DATE = {"ifs": "20240229", "aifs-single": "20250226"}


def pick_source(init, model):
    """本家にあれば本家、無ければ AWS ミラー。範囲外なら None"""
    import urllib.request
    ymd, hh = init.strftime("%Y%m%d"), f"{init.hour:02d}"
    path = "ifs/0p25" if model == "ifs" else "aifs-single/0p25"
    url = f"https://data.ecmwf.int/forecasts/{ymd}/{hh}z/{path}/oper/{ymd}{hh}0000-0h-oper-fc.index"
    try:
        if urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=20).status == 200:
            return "ecmwf"
    except Exception:  # noqa: BLE001
        pass
    return "aws" if ymd >= MIN_DATE[model] else None


def fetch(c, target, **req):
    """一時ファイルに取得してから改名する（途中で止まった不完全なファイルを「取得済み」と誤判定しないため）"""
    from pathlib import Path
    tmp = target + ".part"
    c.retrieve(target=tmp, **req)
    Path(tmp).rename(target)


def main():
    init = datetime.strptime(sys.argv[1], "%Y%m%d%H")
    steps = [0, 6, 12, 18, 24]
    for model, kw in [("ifs", {"resol": "0p25"}), ("aifs-single", {})]:
        src = pick_source(init, model)
        if src is None:
            print(f"スキップ: {model} は {MIN_DATE[model]} 以降のみ（{sys.argv[1]} は範囲外）")
            continue
        print(f"{model}: 取得元 {src}")
        c = Client(source=src, model=model, **kw)
        base = f"data/ref_{model}_{sys.argv[1]}"
        from pathlib import Path
        d = init.strftime("%Y%m%d")
        if not Path(base + ".grib2").exists():
            fetch(c, base + ".grib2", date=d, time=init.hour, step=steps, type="fc", param=SFC)
        if not Path(base + "_pl.grib2").exists():
            fetch(c, base + "_pl.grib2", date=d, time=init.hour, step=steps, type="fc", param=PL[model],
                  levtype="pl", levelist=LEVELS)
        if model == "aifs-single" and src == "aws" and not Path(base + "_plz.grib2").exists():     # 本家のデータには gh がある（ミラーの古い分だけ z）
            try:
                fetch(c, base + "_plz.grib2", date=d, time=init.hour, step=steps, type="fc", param=PLZ, levtype="pl", levelist=LEVELS)
            except Exception as e:  # noqa: BLE001  zが無い日付でも、他のデータは使える
                print("z（高度）は取得できませんでした:", str(e)[:80])
        if model == "ifs" and not Path(base + "_sfc3.grib2").exists():
            # IFSは3時間ごとにも出る。降水 RR3 用に、間の時刻（3・9・15・21h）の地上量を取る（AIFSは6時間間隔のみ）
            fetch(c, base + "_sfc3.grib2", date=d, time=init.hour, step=[3, 9, 15, 21], type="fc", param=SFC)
        print("取得:", base)


if __name__ == "__main__":
    main()
