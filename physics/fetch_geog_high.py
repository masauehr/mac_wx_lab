"""WPS_GEOG 高解像度版（必須分・圧縮 約2.8GB）を取得して、3km（ネスト）用に使えるようにする。

- 取得して data/geog_dl/ に展開（展開後は約29GB。必要な項目だけ展開して容量を抑える）
- 低解像度版（9km用）と高解像度版を、1つの data/geog_dl/WPS_GEOG にまとめる（WPS は geog_data_path を1つしか取れない）
  親領域は geog_data_res='lowres'、ネスト領域は 'default'（30秒≒1km）を使う
使い方: .venv/bin/python physics/fetch_geog_high.py
"""
import tarfile
import urllib.request
from pathlib import Path

import truststore

truststore.inject_into_ssl()
URL = "https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_high_res_mandatory.tar.gz"
BASE = Path("data/geog_dl")
TGZ = BASE / "geog_high_res_mandatory.tar.gz"
HIGH = BASE / "WPS_GEOG_HIGH_RES"
LOW = BASE / "WPS_GEOG_LOW_RES"
ALL = BASE / "WPS_GEOG"
# 展開する項目（GEOGRID.TBL の default が参照するもののうち、必須分）。それ以外は展開しない
WANT = ("topo_gmted2010_30s", "modis_landuse_20class_30s_with_lakes", "soiltype_top_30s", "soiltype_bot_30s",
        "greenfrac_fpar_modis", "albedo_modis", "lai_modis_10m", "maxsnowalb_modis", "soiltemp_1deg",
        "orogwd_10m", "varsso_10m", "lake_depth", "topobath_30s", "clayfrac_5m", "sandfrac_5m", "erod")


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    if not TGZ.exists():
        print("ダウンロード:", URL, flush=True)
        urllib.request.urlretrieve(URL, str(TGZ) + ".part")
        Path(str(TGZ) + ".part").rename(TGZ)
    HIGH.mkdir(exist_ok=True)
    print("展開（必要な項目だけ）…", flush=True)
    n = 0
    with tarfile.open(TGZ) as t:
        for m in t:
            parts = Path(m.name).parts
            top = parts[1] if len(parts) > 1 and parts[0] == "WPS_GEOG" else (parts[0] if parts else "")
            if top in WANT:
                dest = HIGH / "/".join(parts[1:] if parts[0] == "WPS_GEOG" else parts)
                if m.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                elif m.isfile() and not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with t.extractfile(m) as src, open(dest, "wb") as out:
                        while chunk := src.read(1 << 22):
                            out.write(chunk)
                    n += 1
    print(f"展開したファイル: {n}", flush=True)
    # まとめフォルダ（シンボリックリンク）。高解像度版を優先し、無い項目は低解像度版から
    ALL.mkdir(exist_ok=True)
    for src_dir in (LOW, HIGH):
        for d in src_dir.iterdir():
            if d.is_dir():
                link = ALL / d.name
                if link.is_symlink() or link.exists():
                    link.unlink()
                link.symlink_to(d.resolve())
    print("まとめ:", sorted(p.name for p in ALL.iterdir()), flush=True)


if __name__ == "__main__":
    main()
