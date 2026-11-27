"""WPS_GEOG（低解像度・必須分 約150MB）を取得して data/ に展開する。"""
import tarfile
import urllib.request

import truststore

truststore.inject_into_ssl()  # macOSの証明書ストアを使う（uv版Pythonは既定で証明書が空）
from pathlib import Path

URL = "https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_low_res_mandatory.tar.gz"
dl = Path("data/geog_dl"); dl.mkdir(parents=True, exist_ok=True)
tgz = dl / "geog_low_res_mandatory.tar.gz"
if not tgz.exists():
    urllib.request.urlretrieve(URL, tgz)
with tarfile.open(tgz) as t:
    t.extractall(dl, filter="data")
print("展開完了:", [p.name for p in dl.iterdir()])
