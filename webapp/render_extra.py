"""差分図（IFS比）・検証図・統計。nwp_chart 形式の天気図は render.py が作る（このファイルはその補助）。

出力: webapp/output/<初期時刻>/<地域>/<モデル>/<図種>/f<予報時間3桁>.png、<初期時刻>/index.json
使い方: python webapp/render.py <初期時刻YYYYMMDDHH>
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nwpdata as nd  # noqa: E402

OUT = Path(__file__).resolve().parent / "output"
for _f in ["Hiragino Sans", "Hiragino Kaku Gothic ProN", "AppleGothic", "Arial Unicode MS"]:
    if any(_f == x.name for x in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = _f
        break
plt.rcParams["axes.unicode_minus"] = False

REGIONS = {
    "okinawa": {"name": "沖縄周辺", "lat": (21.5, 30.5), "lon": (121.5, 133.5), "grid": 2, "isobar": 2},
    "eastasia": {"name": "東アジア", "lat": (5, 50), "lon": (100, 155), "grid": 5, "isobar": 4},
}
CHARTS = [
    ("msl", "海面気圧・10m風"), ("t2", "2m気温"), ("wspd", "10m風速"),
    ("rain6", "6時間降水"), ("rainacc", "積算降水"),
    ("d_msl", "差：海面気圧（IFS比）"), ("d_t2", "差：2m気温（IFS比）"),
    ("d_wspd", "差：10m風速（IFS比）"), ("d_rain6", "差：6時間降水（IFS比）"),
    ("verif", "検証（IFS比のRMSE）"),
]
CHART_LABEL = dict(CHARTS)
DIFF = {"d_msl": ("msl", "hPa", 1.0), "d_t2": ("t2", "℃", 2.0), "d_wspd": ("wspd", "m/s", 5.0), "d_rain6": ("rain6", "mm", 20.0)}
RAIN_LEVELS = [0.5, 1, 2, 4, 8, 16, 32, 64, 128]
RAIN_COLORS = ["#e8f1fb", "#bcd9f2", "#8ec0ea", "#5ca3de", "#2f7fcb", "#f2d649", "#f08a2e", "#d62f2f", "#8e1b6f"]
RAINACC_LEVELS = [1, 2, 5, 10, 20, 40, 80, 120, 160, 200]
EDGE = 10  # WRFの境界緩和帯を除外する格子数（検証用）


def jst(dt_utc):
    return (dt_utc + timedelta(hours=9)).strftime("%d日%H時")


def rain6(m, ft):
    """直前6時間の降水 [mm]。取れなければ None"""
    if ft < 6:
        return None
    if ft - 6 == 0:
        return m.field(ft, "tpacc") if ft in m.steps else None
    if ft in m.steps and (ft - 6) in m.steps:
        return m.field(ft, "tpacc") - m.field(ft - 6, "tpacc")
    return None


def value(m, ft, var):
    """図種に使う量 var を取り出す（msl・t2・wspd・rain6・rainacc）"""
    if var == "rain6":
        return rain6(m, ft)
    if var == "rainacc":
        return m.field(ft, "tpacc") if ft in m.steps else None
    return m.field(ft, var) if ft in m.steps else None


def crop(m, region, arr):
    """規則格子を地域で切り出す。(lat, lon, arr) を返す。WRF（曲線格子）はそのまま"""
    if not m.regular:
        return m.lat, m.lon, arr
    (la0, la1), (lo0, lo1) = region["lat"], region["lon"]
    ii = np.where((m.lat >= la0) & (m.lat <= la1))[0]
    jj = np.where((m.lon >= lo0) & (m.lon <= lo1))[0]
    return m.lat[ii], m.lon[jj], arr[np.ix_(ii, jj)]


def on_target(m, target, ft, var):
    """モデル m の量 var を、target モデルの格子へそろえる（検証・差分用）"""
    if m.regular and not target.regular:
        tmp = m  # 規則格子 → WRF格子
        if var == "rain6":
            a, b = rain6(tmp, ft), None
            if a is None:
                return None
            return _interp_arr(tmp, target, a)
        a = value(tmp, ft, var)
        return None if a is None else _interp_arr(tmp, target, a)
    a = value(m, ft, var)
    return a


def _interp_arr(src, dst, arr):
    from scipy.interpolate import RegularGridInterpolator

    la, f = src.lat, arr
    if la[0] > la[-1]:
        la, f = la[::-1], f[::-1]
    fn = RegularGridInterpolator((la, src.lon), f, bounds_error=False, fill_value=np.nan)
    return fn(np.stack([dst.lat.ravel(), dst.lon.ravel()], axis=-1)).reshape(dst.lat.shape)


def diff_field(models, mkey, ft, var):
    """モデル − IFS。WRFはIFSをWRF格子へ補間。IFS自身は None"""
    if mkey == "ifs" or "ifs" not in models:
        return None
    m, ref = models[mkey], models["ifs"]
    a = value(m, ft, var)
    if a is None:
        return None
    if m.regular:
        b = value(ref, ft, var)
        return None if b is None else a - b
    b = on_target(ref, m, ft, var)
    return None if b is None else a - b


def valid_time(init_dt, ft):
    return init_dt + timedelta(hours=ft)


def draw_chart(models, land, init, mkey, region_key, chart, ft, path):
    region = REGIONS[region_key]
    m = models[mkey]
    init_dt = datetime.strptime(init, "%Y%m%d%H")
    fig = plt.figure(figsize=(9.5, 7.6), dpi=100)
    ax = fig.add_axes([0.07, 0.09, 0.78, 0.82])
    cax = fig.add_axes([0.88, 0.12, 0.022, 0.76])
    title_extra = ""
    cf = None
    barbs = None
    if chart in DIFF:
        var, unit, lim = DIFF[chart]
        v = diff_field(models, mkey, ft, var)
        la, lo, z = crop(m, region, v)
        lv = np.linspace(-lim, lim, 11)
        cf = ax.contourf(lo, la, z, levels=lv, cmap="RdBu_r", extend="both")
        title_extra = f"（{unit}）"
        cbl = unit
    elif chart in ("msl", "wspd"):
        la, lo, w = crop(m, region, m.field(ft, "wspd"))
        cf = ax.contourf(lo, la, w, levels=np.arange(0, 26, 2), cmap="YlGnBu", extend="max")
        cbl = "10m風速 [m/s]"
        if chart == "msl":
            la2, lo2, p = crop(m, region, m.field(ft, "msl"))
            lv = np.arange(np.floor(np.nanmin(p) / region["isobar"]) * region["isobar"], np.nanmax(p) + region["isobar"], region["isobar"])
            cs = ax.contour(lo2, la2, p, levels=lv, colors="k", linewidths=0.8)
            ax.clabel(cs, fmt="%d", fontsize=7)
        _, _, u = crop(m, region, m.field(ft, "u10"))
        _, _, vv = crop(m, region, m.field(ft, "v10"))
        n = max(1, int(round(min(np.shape(u)) / 22)))
        if m.regular:
            X, Y = np.meshgrid(lo, la)
        else:
            X, Y = lo, la
        ax.barbs(X[::n, ::n], Y[::n, ::n], u[::n, ::n], vv[::n, ::n], length=4.2, linewidth=0.5,
                 barb_increments=dict(half=2.5, full=5, flag=25), color="#223")
    elif chart == "t2":
        la, lo, t = crop(m, region, m.field(ft, "t2"))
        lv = np.arange(8, 30, 1) if region_key == "okinawa" else np.arange(-30, 36, 3)
        cf = ax.contourf(lo, la, t, levels=lv, cmap="RdYlBu_r", extend="both")
        cbl = "2m気温 [℃]"
    elif chart in ("rain6", "rainacc"):
        r = value(m, ft, chart)
        la, lo, z = crop(m, region, r)
        lv = RAIN_LEVELS if chart == "rain6" else RAINACC_LEVELS
        cols = RAIN_COLORS if chart == "rain6" else RAIN_COLORS + ["#4b0f5a"]
        cf = ax.contourf(lo, la, z, levels=lv, colors=cols[: len(lv)], extend="max")
        cbl = "降水量 [mm]"
    # 海岸線（陸海マスクの0.5等値線）
    if region_key == "okinawa" and "wrf" in models:  # 沖縄はWRFの9km陸海マスクで細かい海岸線
        w = models["wrf"]
        ax.contour(w.lon, w.lat, w.landmask, [0.5], colors="#444", linewidths=0.7)
    elif land is not None:
        lm, lla, llo = land
        ii = np.where((lla >= region["lat"][0]) & (lla <= region["lat"][1]))[0]
        jj = np.where((llo >= region["lon"][0]) & (llo <= region["lon"][1]))[0]
        ax.contour(llo[jj], lla[ii], lm[np.ix_(ii, jj)], [0.5], colors="#444", linewidths=0.7)
    if region_key == "eastasia" and "wrf" in models:  # WRF領域の枠
        w = models["wrf"]
        edge = np.concatenate([w.lon[0], w.lon[:, -1], w.lon[-1][::-1], w.lon[::-1, 0]])
        edgela = np.concatenate([w.lat[0], w.lat[:, -1], w.lat[-1][::-1], w.lat[::-1, 0]])
        ax.plot(edge, edgela, color="#c0392b", linewidth=1.2)
    ax.set_xlim(*region["lon"]); ax.set_ylim(*region["lat"])
    ax.set_aspect(1 / np.cos(np.deg2rad(np.mean(region["lat"]))))
    g = region["grid"]
    ax.set_xticks(np.arange(np.ceil(region["lon"][0] / g) * g, region["lon"][1] + 0.1, g))
    ax.set_yticks(np.arange(np.ceil(region["lat"][0] / g) * g, region["lat"][1] + 0.1, g))
    ax.grid(color="#999", linewidth=0.4, alpha=0.6)
    ax.tick_params(labelsize=8)
    if cf is not None:
        cb = fig.colorbar(cf, cax=cax); cb.ax.tick_params(labelsize=8); cb.set_label(cbl, fontsize=9)
    vt = valid_time(init_dt, ft)
    fig.text(0.07, 0.945, f"{m.label}　{CHART_LABEL[chart]}{title_extra}", fontsize=13, weight="bold")
    fig.text(0.07, 0.915, f"初期 {init_dt:%Y-%m-%d %H}UTC　+{ft:02d}h　有効 {vt:%m/%d %H}UTC（{jst(vt)}JST）　{region['name']}",
             fontsize=9.5, color="#334")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=100)
    plt.close(fig)


# ---------- 検証統計 ----------
def stats(models, init):
    """IFS を参照に、WRF の格子（なければ沖縄周辺を切り出した格子）上で偏差・RMSE・相関を出す"""
    if "ifs" not in models:
        return {}
    tgt = models.get("wrf") or models["ifs"]
    mask = np.zeros(tgt.lat.shape if not tgt.regular else (len(tgt.lat), len(tgt.lon)), bool)
    if tgt.regular:
        r = REGIONS["okinawa"]
        la, lo = np.meshgrid(tgt.lat, tgt.lon, indexing="ij")
        mask = (la >= r["lat"][0]) & (la <= r["lat"][1]) & (lo >= r["lon"][0]) & (lo <= r["lon"][1])
    else:
        mask[EDGE:-EDGE, EDGE:-EDGE] = True
    res = {}
    for var in ["msl", "t2", "wspd", "rain6"]:
        res[var] = {}
        for mk in ["aifs", "aifs_off", "wrf"]:
            if mk not in models:
                continue
            res[var][mk] = {}
            for ft in [6, 12, 18, 24]:
                a = on_target(models[mk], tgt, ft, var)
                b = on_target(models["ifs"], tgt, ft, var)
                if a is None or b is None:
                    continue
                ok = mask & np.isfinite(a) & np.isfinite(b)
                d = (a - b)[ok]
                res[var][mk][ft] = {"bias": float(d.mean()), "rmse": float(np.sqrt((d ** 2).mean())),
                                    "corr": float(np.corrcoef(a[ok], b[ok])[0, 1])}
    prec = {}
    for mk in ["ifs", "aifs", "aifs_off", "wrf"]:
        if mk in models:
            a = on_target(models[mk], tgt, 24, "rainacc")
            if a is not None:
                prec[mk] = {"mean": float(np.nanmean(a[mask])), "max": float(np.nanmax(a[mask]))}
    res["precip24"] = prec
    return res


def draw_verif(st, init, path):
    fig, axs = plt.subplots(2, 2, figsize=(9.5, 7.6), dpi=100)
    names = {"msl": "海面気圧 RMSE [hPa]", "t2": "2m気温 RMSE [℃]", "wspd": "10m風速 RMSE [m/s]", "rain6": "6時間降水 RMSE [mm]"}
    colors = {"aifs": "#1a4fd0", "aifs_off": "#7aa0ee", "wrf": "#d4571f"}
    for ax, var in zip(axs.ravel(), names):
        for mk, series in st.get(var, {}).items():
            fts = sorted(series)
            ax.plot(fts, [series[f]["rmse"] for f in fts], "o-", color=colors[mk], label=nd.LABELS[mk])
        ax.set_title(names[var], fontsize=10); ax.set_xticks([6, 12, 18, 24]); ax.grid(alpha=0.4)
        ax.set_xlabel("予報時間 [h]", fontsize=8)
    axs[0, 0].legend(fontsize=8)
    fig.suptitle(f"IFS を参照にした RMSE（初期 {init[:4]}-{init[4:6]}-{init[6:8]} {init[8:]}UTC・沖縄周辺）", fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=100)
    plt.close(fig)


def render_run(init, progress=print):
    models = nd.load_all(init)
    if not models:
        raise RuntimeError("このrunにはモデルのデータがありません")
    land = nd.load_land(init)
    out = OUT / init
    index = {"init": init, "charts": [{"id": c, "label": l} for c, l in CHARTS], "regions": {}, "labels": nd.LABELS}
    # 描画対象を数える
    jobs = []
    for rk, region in REGIONS.items():
        for mk, m in models.items():
            if mk == "wrf" and rk != "okinawa":
                continue
            for chart, _ in CHARTS:
                if chart == "verif":
                    continue
                if chart in DIFF and (rk != "okinawa" or mk == "ifs"):
                    continue
                for ft in m.steps:
                    jobs.append((rk, mk, chart, ft))
    progress(f"作図: {len(jobs)}枚")
    reg_models = {rk: {} for rk in REGIONS}
    n = 0
    for rk, mk, chart, ft in jobs:
        m = models[mk]
        if chart == "rain6" and rain6(m, ft) is None:
            continue
        if chart == "rainacc" and ft == 0:
            continue
        if chart in DIFF:
            var = DIFF[chart][0]
            if diff_field(models, mk, ft, var) is None:
                continue
        p = out / rk / mk / chart / f"f{ft:03d}.png"
        draw_chart(models, land, init, mk, rk, chart, ft, p)
        reg_models[rk].setdefault(mk, {}).setdefault(chart, []).append(ft)
        n += 1
        if n % 25 == 0:
            progress(f"作図 {n}/{len(jobs)}")
    st = stats(models, init)
    if st and "wrf" in models:
        src = out / "okinawa" / "wrf" / "verif" / "f000.png"
        draw_verif(st, init, src)
        import shutil
        for mk in list(reg_models["okinawa"]):  # どのモデルを選んでも同じ検証図を出す
            if mk != "wrf":
                (out / "okinawa" / mk / "verif").mkdir(parents=True, exist_ok=True)
                shutil.copy(src, out / "okinawa" / mk / "verif" / "f000.png")
            reg_models["okinawa"][mk]["verif"] = [0]
    for rk, region in REGIONS.items():
        index["regions"][rk] = {"name": region["name"], "models": {
            mk: {"label": nd.LABELS[mk], "hours": hrs} for mk, hrs in reg_models[rk].items()}}
    index["stats"] = st
    index["rendered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    progress(f"完了: {n}枚")
    return index


if __name__ == "__main__":
    render_run(sys.argv[1])
