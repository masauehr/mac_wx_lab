"""公式AIFS（AWSミラー）の z（ジオポテンシャル）だけを、.index のバイト範囲で直接取得する。

ecmwf-opendata ライブラリは、ミラーが 503 を返すと2分待って再試行するため、数十MBでも非常に遅くなることがある。
ここでは、必要な z（13の気圧面×各予報時間）の範囲だけを、並列で取得する。
使い方: python compare/fetch_z_direct.py 2025092200
出力: data/ref_aifs-single_<初期時刻>_plz.grib2（読み込み時に fieldsF が gh へ換算する）
"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import truststore

truststore.inject_into_ssl()
LEVELS = {"1000", "925", "850", "700", "600", "500", "400", "300", "250", "200", "150", "100", "50"}
STEPS = [0, 6, 12, 18, 24]
HOSTS = ["https://storage.googleapis.com/ecmwf-open-data", "https://ecmwf-forecasts.s3.amazonaws.com"]   # Google Cloud → AWS の順


def get(url, rng=None, tries=6):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"Range": f"bytes={rng}"} if rng else {})
            return urllib.request.urlopen(req, timeout=60).read()
        except Exception as e:  # noqa: BLE001  503など。少し待って再試行
            if k == tries - 1:
                raise
            time.sleep(2 * (k + 1))


def main():
    init = sys.argv[1]
    ymd, hh = init[:8], init[8:]
    jobs = []
    for host in HOSTS:                                    # 取得元を順に試す（どれかが 503 のとき）
        base = f"{host}/{ymd}/{hh}z/aifs-single/0p25/oper/{ymd}{hh}0000-{{s}}h-oper-fc"
        try:
            jobs = []
            for s in STEPS:
                url = base.format(s=s)
                for line in get(url + ".index", tries=3).decode().strip().split("\n"):
                    m = json.loads(line)
                    if m.get("param") == "z" and m.get("levtype") == "pl" and str(m.get("levelist")) in LEVELS:
                        jobs.append((s, url + ".grib2", f"{m['_offset']}-{m['_offset'] + m['_length'] - 1}"))
            print("取得元:", host, flush=True)
            break
        except Exception as e:  # noqa: BLE001
            print("取得元を切り替え:", host, "→", str(e)[:60], flush=True)
    if not jobs:
        sys.exit("どの取得元でも z の一覧を取得できませんでした")
    print(f"取得するフィールド: {len(jobs)}", flush=True)
    with ThreadPoolExecutor(8) as ex:
        data = list(ex.map(lambda j: get(j[1], j[2]), jobs))
    out = Path(f"data/ref_aifs-single_{init}_plz.grib2")
    tmp = Path(str(out) + ".part2")
    tmp.write_bytes(b"".join(data))
    tmp.rename(out)
    print(f"完了: {out} {out.stat().st_size / 1e6:.0f}MB", flush=True)


if __name__ == "__main__":
    main()
