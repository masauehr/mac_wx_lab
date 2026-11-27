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
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTHONWARNINGS", "ignore")

import fieldsF  # noqa: E402
import nwpcharts as nc  # noqa: E402
import basemap as bm  # noqa: E402

OUT = HERE / "output"
R_ROOT = HERE.parent
# WRF領域（固定）に合わせた地域。他のモデルもこの範囲で見られる
nc.REGIONS["wrf"] = dict(label="沖縄周辺 WRF領域（9km）", kind="pc", center=(127.5, 26.0), width_deg=10.0,
                         bbox=(122.5, 132.5, 22.1, 29.8), barb_n=12, coast="10m", zoom=3, exclude=(),
                         label_zones=("east_china_sea",))
REGION_ORDER = ["japan", "okinawa", "asia", "wrf"]
# 降水の時間幅の違う図（RR3＝直前3時間の平均降水強度、RR1＝直前1時間）。出力間隔が短いモデルだけ作れる:
#   WRF（1時間ごと）: RR1・RR3 / IFS（3時間ごと）: RR3 / AIFS（6時間ごと）: なし（既定の図が6時間平均）
VARIANTS = [("surf_rr3", "surf", 3, "地上（気圧・風・降水）RR3"), ("surf_rr1", "surf", 1, "地上（気圧・風・降水）RR1"),
            ("tenki_rr3", "tenki", 3, "地上（天気・気圧・SSI）RR3"), ("tenki_rr1", "tenki", 1, "地上（天気・気圧・SSI）RR1")]
for _vid, _base, _w, _label in VARIANTS:
    nc.CHARTS[_vid] = dict(nc.CHARTS[_base], label=_label)
VARIANT_IDS = [v[0] for v in VARIANTS]
VARIANT_WINDOW = {v[0]: v[2] for v in VARIANTS}
LABELS = {"wrf": "WRF 9km", "aifs": "AIFS（手元）", "aifs_off": "AIFS（公式）", "ifs": "IFS（ECMWF）"}
MODEL_ORDER = ["wrf", "aifs", "aifs_off", "ifs"]          # 基本の並び。実験ケースは対照の直後に入る（init ごとに動的）


def models_of(init):
    """この初期時刻にあるWRF系モデル（対照・実験）を、対照→実験→AIFS→IFS の順で返す。ラベルも登録する"""
    src = fieldsF.wrf_sources(init)
    for k, v in src.items():
        LABELS[k] = v["label"]
    return [k for k in src] + [m for m in MODEL_ORDER if m != "wrf"]


def experiments_info(init):
    """この初期時刻のWRF実験（設定・地形編集・改造版の使用）。画面の「実験の設定」と公開サイトで表示する"""
    import json as _json
    out = []
    for cj in sorted((R_ROOT / "runs" / init / "wrfx").glob("*/case.json")) if (R_ROOT / "runs" / init / "wrfx").exists() else []:
        info = _json.loads(cj.read_text())
        out.append({"id": cj.parent.name, "label": info["cfg"].get("label", cj.parent.name), "cfg": info["cfg"], "patched": info.get("patched", False),
                    "terrain_notes": info.get("terrain_notes", []), "estimate_min": info.get("estimate_min"), "finished": info.get("finished")})
    return out


def img_label(mk):
    """図に書く名前（図の文字は日本語が出ないフォントなので、実験は英数字にする）"""
    src = fieldsF.wrf_sources(INIT_CTX[0]).get(mk) if INIT_CTX else None
    if src and mk != "wrf":
        d = src["cfg"]["domains"][src["dom"] - 1]
        return f"WRF {src['case']} d{src['dom']:02d} ({float(d['dx']) / 1000:g}km)"
    return LABELS.get(mk, mk)


INIT_CTX = []


def region_key(model):
    return "r_" + model[2:]                                  # x_<ID>_dNN → r_<ID>_dNN


def register_regions(init):
    """実験ケースの各領域を、地域として登録する（中心・大きさはデータの範囲から）。作図の各プロセスでも呼ぶ"""
    out = []
    for k, v in fieldsF.wrf_sources(init).items():
        if k == "wrf" or same_d01(init, k):          # 対照と同じ領域は、地域「WRF領域」で見られるので別の地域を作らない
            continue
        lo0, lo1, la0, la1 = fieldsF.wrf_bbox(init, k)
        deg = v["deg"]
        n_lon = (lo1 - lo0) / deg
        nc.REGIONS[region_key(k)] = dict(
            label=f"{v['label']}", kind="pc", center=((lo0 + lo1) / 2, (la0 + la1) / 2), width_deg=max(1.0, (lo1 - lo0) * 1.08),
            bbox=(lo0, lo1, la0, la1), barb_n=max(2, int(round(n_lon / 10))), coast="10m", zoom=3 if deg >= 0.1 else 1, exclude=(),
            label_zones=())
        out.append(region_key(k))
    return out


def regions_all(init):
    return REGION_ORDER + register_regions(init)
EXTRA = [("d_msl", "差：海面気圧（IFS比）"), ("d_t2", "差：2m気温（IFS比）"),
         ("d_wspd", "差：10m風速（IFS比）"), ("d_rain6", "差：6時間降水（IFS比）"), ("verif", "検証（IFS比のRMSE）"),
         ("dc_msl", "実験−対照：海面気圧"), ("dc_t2", "実験−対照：2m気温"), ("dc_wspd", "実験−対照：10m風速"),
         ("dc_rain6", "実験−対照：6時間降水"), ("dc_rainacc", "実験−対照：積算降水")]


def chart_list():
    """図種の並び（nwp_chartと同じ順。降水の時間幅違いは基本の図の直後）"""
    out = []
    for c, spec in nc.CHARTS.items():
        if c in VARIANT_IDS:
            continue
        out.append({"id": c, "label": spec["label"]})
        out += [{"id": v, "label": nc.CHARTS[v]["label"]} for v in VARIANT_IDS if v.startswith(c + "_")]
    return out


def variants_for(mk, ft):
    """このモデル・予報時間で作れる降水の時間幅違いの図 [(図種ID, 時間幅h)]"""
    out = []
    if fieldsF.is_wrf(mk):
        if ft >= 1:
            out += [("surf_rr1", 1), ("tenki_rr1", 1)]
        if ft >= 3 and ft % 3 == 0:
            out += [("surf_rr3", 3), ("tenki_rr3", 3)]
    elif mk == "ifs" and ft >= 3 and ft % 3 == 0:
        out += [("surf_rr3", 3)] + ([("tenki_rr3", 3)] if ft % 6 == 0 else [])
    return out


def rr_for(mk, init, ft, w):
    """降水強度（mm/h・直前 w 時間の平均）と時間幅"""
    if fieldsF.is_wrf(mk):
        t = fieldsF.wrf_F(init, ft, rr_window=w, levels=[], key=mk)
    else:
        t = fieldsF.ecmwf_F(mk, init, ft, rr_window=w, sfc_only=True)
    return t["rr"], t["rr_h"]


def model_steps(mk, init):
    if fieldsF.is_wrf(mk):
        return fieldsF.wrf_steps(init, mk)
    if mk == "aifs":
        return fieldsF.aifs_local_steps(init)
    return fieldsF.ecmwf_steps(mk, init)


def build_F(mk, init, ft):
    if fieldsF.is_wrf(mk):
        return fieldsF.wrf_F(init, ft, key=mk)
    if mk == "aifs":
        return fieldsF.aifs_local_F(init, ft)
    return fieldsF.ecmwf_F(mk, init, ft)


def regions_for(mk, init=None):
    """全モデルが全地域で見られる。WRFは領域が狭いので、広い地図ではデータのある範囲（赤の破線の枠）だけ描き、周りは背景地図のみ"""
    return regions_all(init) if init else REGION_ORDER


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
    """1つの(モデル, 予報時間)について、全地域・全図種・全背景地図を描く（Fを1回だけ作る）。
    mode='var' は、気圧面データのない時刻（WRFの毎時・IFSの3時間間隔）の降水の時間幅違いの図だけ"""
    init, mk, ft, maps, mode = args
    INIT_CTX[:] = [init]
    models_of(init)                                          # ラベル登録
    register_regions(init)                                   # 実験ケースの領域（各プロセスで必要）
    init_dt = datetime.strptime(init, "%Y%m%d%H")
    done = []
    if mode == "full":
        F = build_F(mk, init, ft)
        plan = [(c, None) for c in nc.CHARTS if c not in VARIANT_IDS] + [(c, w) for c, w in variants_for(mk, ft)]
    else:
        vs = variants_for(mk, ft)
        if not vs:
            return done
        w0 = vs[0][1]
        F = fieldsF.wrf_F(init, ft, rr_window=w0, levels=[850, 700], key=mk) if fieldsF.is_wrf(mk) else \
            fieldsF.ecmwf_F(mk, init, ft, rr_window=w0, sfc_only=True)
        plan = [(c, w) for c, w in vs if w == w0 and (fieldsF.is_wrf(mk) or c.startswith("surf"))]
    for region in regions_for(mk, init):
        for chart, w in plan:
            Fc = F
            if w is not None and mode == "full":
                Fc = dict(F)
                Fc["rr"], Fc["rr_h"] = rr_for(mk, init, ft, w)
                if Fc["rr"] is None:                              # 遅れて開始するネストの最初の時刻（降水の差が取れない）
                    continue
            elif w is not None and F.get("rr") is None:
                continue
            if not chart_ok(chart, region, Fc):
                continue
            for kind in maps:
                p = OUT / init / kind / region / mk / chart / f"f{ft:03d}.png"
                p.parent.mkdir(parents=True, exist_ok=True)
                try:
                    nc.render_chart(chart, region, Fc, init_dt, ft, str(p), model=img_label(mk), quantize=True, basemap=kind)
                except Exception as e:  # noqa: BLE001  1枚の失敗で全体を止めない
                    print(f"[失敗] {mk} {region} {chart} f{ft:03d} {kind}: {e}", flush=True)
                    continue
            done.append((region, mk, chart, ft))
    return done


def scan_hours(init, models=None, regions=None):
    """出力ディレクトリを走査して {地域: {モデル: {図種: [予報時間]}}} を作る（背景地図の違いは無視して和集合）"""
    hours = {}
    base = OUT / init
    for pdir in base.glob("*/*/*/*"):          # <背景地図>/<地域>/<モデル>/<図種>
        if not pdir.is_dir():
            continue
        chart, mk, region = pdir.name, pdir.parent.name, pdir.parent.parent.name
        if (models and mk not in models) or (regions and region not in regions):
            continue
        fts = {int(f.stem[1:]) for f in pdir.glob("f*.png")}
        if fts:
            hours.setdefault(region, {}).setdefault(mk, {}).setdefault(chart, set()).update(fts)
    return {r: {m: {c: sorted(f) for c, f in cs.items()} for m, cs in ms.items()} for r, ms in hours.items()}


DC = {"dc_msl": ("msl", "hPa", 2.0), "dc_t2": ("t2", "℃", 2.0), "dc_wspd": ("wspd", "m/s", 5.0),
      "dc_rain6": ("rain6", "mm", 20.0), "dc_rainacc": ("rainacc", "mm", 60.0)}


def same_d01(init, key):
    """実験の第1領域が対照（9km・121×101・中心26N127.5E）と同じ格子か（同じなら差を取れる）"""
    src = fieldsF.wrf_sources(init).get(key)
    if not src or src["dom"] != 1 or src["cfg"] is None:
        return False
    d = src["cfg"]["domains"][0]
    return (float(d["dx"]), int(d["n_we"]), int(d["n_sn"]), list(map(float, d["center"]))) == (9000.0, 121, 101, [26.0, 127.5])


def _dc_field(F0, F1, var, ft, init, k0, k1):
    import numpy as np
    if var == "msl":
        return F1["msl"] - F0["msl"]
    if var == "t2":
        return F1["t2"] - F0["t2"]
    if var == "wspd":
        return np.hypot(F1["u10"], F1["v10"]) - np.hypot(F0["u10"], F0["v10"])
    ds0, ds1 = fieldsF._wrf_ds(init, k0)["ds"], fieldsF._wrf_ds(init, k1)["ds"]
    w0, w1 = fieldsF._wrf_ds(init, k0), fieldsF._wrf_ds(init, k1)
    acc = lambda ds, w, t: fieldsF._regrid(w, ds["RAINC"][t] + ds["RAINNC"][t])  # noqa: E731
    if var == "rainacc":
        return acc(ds1, w1, ft) - acc(ds0, w0, ft)
    if var == "rain6" and ft >= 6:
        return (acc(ds1, w1, ft) - acc(ds1, w1, ft - 6)) - (acc(ds0, w0, ft) - acc(ds0, w0, ft - 6))
    return None


def render_dc(init, progress=print):
    """対照実験との差（実験−対照）の図。第1領域が対照と同じ実験だけ。出力は common/wrf/<実験モデル>/<図>/"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    out = {}
    if "wrf" not in fieldsF.wrf_sources(init):
        return out
    w0 = fieldsF._wrf_ds(init, "wrf")
    for key in [k for k in fieldsF.wrf_sources(init) if same_d01(init, k)]:
        label = LABELS.get(key, key)
        for chart, (var, unit, lim) in DC.items():
            for ft in fieldsF.wrf_steps(init, key):
                if ft == 0 or (var == "rain6" and ft < 6):
                    continue
                F0, F1 = fieldsF.wrf_F(init, ft, levels=[]), fieldsF.wrf_F(init, ft, levels=[], key=key)
                z = _dc_field(F0, F1, var, ft, init, "wrf", key)
                if z is None:
                    continue
                fig, ax = plt.subplots(figsize=(9.5, 7.0), dpi=100)
                cs = ax.contourf(F1["lons"], F1["lats"], z, levels=np.linspace(-lim, lim, 13), cmap="RdBu_r" if var not in ("rain6", "rainacc") else "BrBG", extend="both")
                ax.contour(w0["lon"], w0["lat"], w0["ds"]["LANDMASK"][0], [0.5], colors="k", linewidths=0.7)
                ax.set_xlim(F1["lons"][0], F1["lons"][-1]); ax.set_ylim(F1["lats"][0], F1["lats"][-1])
                ax.set_aspect(1 / np.cos(np.deg2rad(np.mean(F1["lats"]))))
                fig.colorbar(cs, ax=ax, label=f"{unit}（実験 − 対照）")
                vt = datetime.strptime(init, "%Y%m%d%H") + timedelta(hours=ft)
                ax.set_title(f"{label} − 対照（WRF 9km）　{CHART_LABEL_DC[chart]}　初期 {init[:4]}-{init[4:6]}-{init[6:8]} {init[8:]}UTC +{ft:02d}h（有効 {vt:%m/%d %H}UTC）", fontsize=10)
                pth = OUT / init / "common" / "wrf" / key / chart / f"f{ft:03d}.png"
                pth.parent.mkdir(parents=True, exist_ok=True)
                fig.savefig(pth, dpi=100); plt.close(fig)
                out.setdefault(key, {}).setdefault(chart, []).append(ft)
    progress(f"対照実験比の図: {sum(len(v) for d in out.values() for v in d.values())}枚")
    return out


CHART_LABEL_DC = {"dc_msl": "海面気圧", "dc_t2": "2m気温", "dc_wspd": "10m風速", "dc_rain6": "6時間降水", "dc_rainacc": "積算降水"}


def render_run(init, maps=("pale",), procs=6, progress=print, only=None):
    units = []
    steps = {}
    order = models_of(init)
    register_regions(init)
    for mk in order:
        st = model_steps(mk, init)
        if st:
            steps[mk] = st
            if only is None or mk in only or (fieldsF.is_wrf(mk) and "wrf" in only):
                units += [(init, mk, ft, list(maps), "full") for ft in st]
    # 気圧面データのない時刻の、降水の時間幅違いの図（WRFの毎時・IFSの3時間間隔）
    for wk in [k for k in steps if fieldsF.is_wrf(k)]:
        if only is None or wk in only or "wrf" in only:
            units += [(init, wk, ft, list(maps), "var") for ft in fieldsF.wrf_hours(init, wk) if ft not in steps[wk] and ft >= 1]
    if "ifs" in steps and (only is None or "ifs" in only):
        units += [(init, "ifs", ft, list(maps), "var") for ft in fieldsF.ecmwf_sfc3_steps("ifs", init)]
    if not units and only is None:
        raise RuntimeError("このrunにはモデルのデータがありません")
    progress(f"作図: {len(units)}単位（モデル×予報時間）、背景地図 {list(maps)}、{procs}並列")
    n = 0
    if units:
        with mp.get_context("spawn").Pool(procs) as pool:
            for res in pool.imap_unordered(_unit, units):
                n += 1
                progress(f"作図 {n}/{len(units)}")
    hours = scan_hours(init, order, regions_all(init))       # 既にある図（他のモデル・背景地図）も含めて索引にする
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
    for key, cs in render_dc(init, progress).items():
        for chart, fts in cs.items():
            hours.setdefault("wrf", {}).setdefault(key, {})[chart] = fts
    for region in hours:
        for mk in hours[region]:
            for chart in hours[region][mk]:
                hours[region][mk][chart] = sorted(set(hours[region][mk][chart]))
    index = {
        "init": init, "labels": {k: LABELS[k] for k in order}, "model_order": order, "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "charts": chart_list() + [{"id": c, "label": l} for c, l in EXTRA],
        "common": [c for c, _ in EXTRA],
        "maps": [{"id": k, "label": bm.KINDS[k]} for k in bm.KINDS if k != "none" and (OUT / init / k).exists()],   # 既に作った背景地図も含める
        "regions": {r: {"name": nc.REGIONS[r]["label"], "models": {
            mk: {"label": LABELS[mk], "hours": hours[r][mk]} for mk in order if mk in hours.get(r, {})}}
            for r in regions_all(init) if r in hours},
        "stats": st_,
        "experiments": experiments_info(init),
    }
    (OUT / init / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    progress(f"完了: {sum(len(c) for r in hours.values() for m in r.values() for c in m.values())}コマ")
    return index


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("init")
    ap.add_argument("--maps", default="pale")
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--only", default="", help="作図するモデルを限る（例: wrf）。索引は既存の図も含めて作り直す")
    a = ap.parse_args()
    render_run(a.init, maps=tuple(a.maps.split(",")), procs=a.procs, only=[x for x in a.only.split(",") if x] or None)
