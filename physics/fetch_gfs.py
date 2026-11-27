"""GFS 0.25度（AWS Open Data: noaa-gfs-bdp-pds）から WPS(ungrib) に必要な変数だけ取得する。

.idx を読み、必要な変数のバイト範囲だけを取得して1つのGRIB2に連結する。
使い方: python physics/fetch_gfs.py 2026112618 24 3      # 初期時刻 予報時間 間隔
"""
import re
import sys
import urllib.request
from pathlib import Path

import truststore

truststore.inject_into_ssl()  # macOSの証明書ストアを使う

BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{ymd}/{hh}/atmos/gfs.t{hh}z.pgrb2.0p25.f{f:03d}"
# 取得する変数（Vtable.GFS が使うもの）。"..." は idx の「変数:層」に部分一致
WANT = re.compile(
    r":(TMP|UGRD|VGRD|RH|HGT):\d+ mb:|:PRMSL:mean sea level:|:MSLET:mean sea level:|:PRES:surface:|:HGT:surface:"
    r"|:TMP:2 m above ground:|:RH:2 m above ground:|:(UGRD|VGRD):10 m above ground:"
    r"|:(TSOIL|SOILW):[\d.]+-[\d.]+ m below ground:|:TMP:surface:|:LAND:surface:|:ICEC:surface:"
    r"|:WEASD:surface:|:SNOD:surface:"
)


def get(url, rng=None):
    req = urllib.request.Request(url, headers={"Range": f"bytes={rng}"} if rng else {})
    return urllib.request.urlopen(req, timeout=120).read()


def main():
    init, hours, step = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    ymd, hh = init[:8], init[8:]
    out = Path("data/gfs") / init
    out.mkdir(parents=True, exist_ok=True)
    for f in range(0, hours + 1, step):
        dst = out / f"gfs.t{hh}z.pgrb2.0p25.f{f:03d}"
        if dst.exists():
            continue
        url = BASE.format(ymd=ymd, hh=hh, f=f)
        lines = get(url + ".idx").decode().strip().split("\n")
        starts = [int(l.split(":")[1]) for l in lines]
        parts = []
        for i, l in enumerate(lines):
            if WANT.search(l + ":") and "ave" not in l:
                end = starts[i + 1] - 1 if i + 1 < len(lines) else ""
                parts.append((starts[i], end))
        with open(dst.with_suffix(".part"), "wb") as fo:
            for s, e in parts:
                fo.write(get(url, f"{s}-{e}"))
        dst.with_suffix(".part").rename(dst)
        print(f"f{f:03d}: {len(parts)}レコード {dst.stat().st_size / 1e6:.0f}MB", flush=True)


if __name__ == "__main__":
    main()
