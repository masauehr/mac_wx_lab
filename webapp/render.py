"""nwp_chart と同じ図種（高層〜地上の13種）の天気図PNGを、モデル・地域・背景地図ごとに作る。

出力: webapp/output/<初期時刻>/<背景地図>/<地域>/<モデル>/<図種>/f<予報時間3桁>.png
      差分図・検証図は <初期時刻>/common/ 以下（背景地図なし）。索引は <初期時刻>/index.json
使い方: python webapp/render.py <初期時刻YYYYMMDDHH> [--maps pale,relief] [--procs 6]
"""
import argparse
import json
import multiprocessing as mp
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTHONWARNINGS", "ignore")

import fieldsF  # noqa: E402
import nwpcharts as nc  # noqa: E402
import basemap as bm  # noqa: E402

OUT = HERE / "output"
# WRF領域（固定）に合わせた地域。他のモデルもこの範囲で見られる
nc.REGIONS["wrf"] = dict(label="沖縄周辺 WRF領域（9km）", kind="pc", center=(127.5, 26.0), width_deg=10.0,
                         bbox=(122.5, 132.5, 22.1, 29.8), barb_n=12, coast="10m", zoom=3, exclude=(),
                         label_zones=("east_china_sea",))
REGION_ORDER = ["japan", "okinawa", "asia", "wrf"]
LABELS = {"wrf": "WRF 9km", "aifs": "AIFS（手元）", "aifs_off": "AIFS（公式）", "ifs": "IFS（ECMWF）"}
MODEL_ORDER = ["wrf", "aifs", "aifs_off", "ifs"]
EXTRA = [("d_msl", "差：海面気圧（IFS比）"), ("d_t2", "差：2m気温（IFS比）"),
         ("d_wspd", "差：10m風速（IFS比）"), ("d_rain6", "差：6時間降水（IFS比）"), ("verif", "検証（IFS比のRMSE）")]


def model_steps(mk, init):
    if mk == "wrf":
        return fieldsF.wrf_steps(init)
    if mk == "aifs":
        return fieldsF.aifs_local_steps(init)
    return fieldsF.ecmwf_steps(mk, init)


def build_F(mk, init, ft):
    if mk == "wrf":
        return fieldsF.wrf_F(init, ft)
    if mk == "aifs":
        return fieldsF.aifs_local_F(init, ft)
    return fieldsF.ecmwf_F(mk, init, ft)


def regions_for(mk):
    return ["wrf"] if mk == "wrf" else REGION_ORDER


def chart_ok(chart, region, F):
    spec = nc.CHARTS[chart]
    if "regions" in spec and region not in spec["regions"]:
        return False
    if any(lv not in F["p"] for lv in spec.get("levels", [])):
        return False
    if spec.get("cloud") and F.get("tcc") is None:
        return False
    return True


def _unit(args):
    """1つの(モデル, 予報時間)について、全地域・全図種・全背景地図を描く（Fを1回だけ作る）"""
    init, mk, ft, maps = args
    F = build_F(mk, init, ft)
    init_dt = datetime.strptime(init, "%Y%m%d%H")
    done = []
    for region in regions_for(mk):
        for chart in nc.CHARTS:
            if not chart_ok(chart, region, F):
                continue
            for kind in maps:
                p = OUT / init / kind / region / mk / chart / f"f{ft:03d}.png"
                p.parent.mkdir(parents=True, exist_ok=True)
                try:
                    nc.render_chart(chart, region, F, init_dt, ft, str(p), model=LABELS[mk], quantize=True, basemap=kind)
                except Exception as e:  # noqa: BLE001  1枚の失敗で全体を止めない
                    print(f"[失敗] {mk} {region} {chart} f{ft:03d} {kind}: {e}", flush=True)
                    continue
            done.append((region, mk, chart, ft))
    return done


def render_run(init, maps=("pale",), procs=6, progress=print):
    units = []
    steps = {}
    for mk in MODEL_ORDER:
        st = model_steps(mk, init)
        if st:
            steps[mk] = st
            units += [(init, mk, ft, list(maps)) for ft in st]
    if not units:
        raise RuntimeError("このrunにはモデルのデータがありません")
    progress(f"作図: {len(units)}単位（モデル×予報時間）、背景地図 {list(maps)}、{procs}並列")
    hours = {}
    n = 0
    with mp.get_context("spawn").Pool(procs) as pool:
        for res in pool.imap_unordered(_unit, units):
            for region, mk, chart, ft in res:
                hours.setdefault(region, {}).setdefault(mk, {}).setdefault(chart, []).append(ft)
            n += 1
            progress(f"作図 {n}/{len(units)}")
    # 差分図・検証図（背景地図なし）
    import render_extra as rx
    import nwpdata as nd
    models = nd.load_all(init)
    st_ = rx.stats(models, init) if "ifs" in models else {}
    common = OUT / init / "common"
    if "wrf" in models and "ifs" in models:
        for mk in [m for m in MODEL_ORDER if m in models and m != "ifs"]:
            for chart, (var, _, _) in rx.DIFF.items():
                for ft in models[mk].steps:
                    if ft % 6 or rx.diff_field(models, mk, ft, var) is None:
                        continue
                    p = common / "wrf" / mk / chart / f"f{ft:03d}.png"
                    rx.draw_chart(models, nd.load_land(init), init, mk, "okinawa", chart, ft, p)
                    hours.setdefault("wrf", {}).setdefault(mk, {}).setdefault(chart, []).append(ft)
        if st_:
            src = common / "wrf" / "wrf" / "verif" / "f000.png"
            rx.draw_verif(st_, init, src)
            import shutil
            for mk in list(hours.get("wrf", {})):
                if mk != "wrf":
                    (common / "wrf" / mk / "verif").mkdir(parents=True, exist_ok=True)
                    shutil.copy(src, common / "wrf" / mk / "verif" / "f000.png")
                hours["wrf"][mk]["verif"] = [0]
    for region in hours:
        for mk in hours[region]:
            for chart in hours[region][mk]:
                hours[region][mk][chart] = sorted(set(hours[region][mk][chart]))
    index = {
        "init": init, "labels": LABELS, "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "charts": [{"id": c, "label": s["label"]} for c, s in nc.CHARTS.items()] + [{"id": c, "label": l} for c, l in EXTRA],
        "common": [c for c, _ in EXTRA],
        "maps": [{"id": k, "label": bm.KINDS[k]} for k in bm.KINDS if k != "none" and (OUT / init / k).exists()],   # 既に作った背景地図も含める
        "regions": {r: {"name": nc.REGIONS[r]["label"], "models": {
            mk: {"label": LABELS[mk], "hours": hours[r][mk]} for mk in MODEL_ORDER if mk in hours.get(r, {})}}
            for r in REGION_ORDER if r in hours},
        "stats": st_,
    }
    (OUT / init / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    progress(f"完了: {sum(len(c) for r in hours.values() for m in r.values() for c in m.values())}コマ")
    return index


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("init")
    ap.add_argument("--maps", default="pale")
    ap.add_argument("--procs", type=int, default=6)
    a = ap.parse_args()
    render_run(a.init, maps=tuple(a.maps.split(",")), procs=a.procs)
