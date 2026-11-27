"""任意の高度（気圧面 hPa・海抜高度 m）と表示範囲（緯度経度の矩形）を指定して、1枚の図を作る。

ローカルのWebアプリ（custom.html）から呼ばれる。モデル: WRF・手元AIFS・公式AIFS・IFS。
- 気圧面: WRF はモデル面から任意の気圧面へ直接補間。AIFS・IFS は提供される13面（1000〜50hPa）の間を log p で線形補間（補間と表示）
- 海抜高度 m: WRF だけ（モデル面の高さで補間）。AIFS・IFS は不可
- 図: 塗り（変数）＋ 等値線（変数・間隔）＋ 風（矢羽・矢印）。背景は地理院タイル等（basemap.py）
"""
import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTHONWARNINGS", "ignore")
import matplotlib

matplotlib.use("Agg")
import cartopy.crs as ccrs  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from scipy.ndimage import zoom  # noqa: E402

import basemap as bm  # noqa: E402
import fieldsF  # noqa: E402
import nwpcharts as nc  # noqa: E402

OUTDIR = HERE / "custom_out"
LOCK = threading.Lock()
PC = ccrs.PlateCarree()
for _f in ["Hiragino Sans", "Hiragino Kaku Gothic ProN", "AppleGothic", "Arial Unicode MS"]:
    if any(_f == x.name for x in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = _f
        break
plt.rcParams["axes.unicode_minus"] = False

LABELS = {"wrf": "WRF 9km", "aifs": "AIFS（手元）", "aifs_off": "AIFS（公式）", "ifs": "IFS（ECMWF）"}


def label_of(init, model):
    """モデルの表示名（実験ケースは設定名つき）"""
    if model in LABELS:
        return LABELS[model]
    return fieldsF.wrf_sources(init).get(model, {}).get("label", model)
# 変数: (表示名, 単位, 既定の塗り色, 既定の刻み)
VARS = {
    "t": ("気温", "℃", "RdYlBu_r", 2.0),
    "gh": ("ジオポテンシャル高度", "m", "viridis", None),
    "wspd": ("風速", "m/s", "YlGnBu", 5.0),
    "rh": ("相対湿度", "%", "BrBG", 10.0),
    "dd": ("湿数（T−Td）", "℃", "YlOrBr", 3.0),
    "vort": ("相対渦度", "1e-6/s", "RdBu_r", 20.0),
    "div": ("発散", "1e-6/s", "PuOr_r", 20.0),
    "w": ("鉛直p速度 ω（上昇が負）", "hPa/h", "RdBu", 20.0),
    "q": ("混合比", "g/kg", "GnBu", 2.0),
    "ept": ("相当温位", "K", "Spectral_r", 5.0),
    "theta": ("温位", "K", "Spectral_r", 4.0),
}
SFC_VARS = {
    "t2": ("2m気温", "℃", "RdYlBu_r", 2.0), "msl": ("海面気圧", "hPa", "viridis", 2.0),
    "wspd10": ("10m風速", "m/s", "YlGnBu", 2.0), "rr": ("降水強度（直前の平均）", "mm/h", "Blues", None),
    "tcc": ("全雲量", "%", "Greys", 10.0),
}


# ---------- 鉛直方向の補間 ----------
def _interp_p(F, lev):
    """F['p'] の等圧面データを log p で線形補間して lev[hPa] の場にする。(場の辞書, 厳密か)"""
    levs = sorted(F["p"])                                    # 小さい順（上空→地上）
    if lev in F["p"]:
        return dict(F["p"][lev]), True
    lo = max([l for l in levs if l < lev], default=None)
    hi = min([l for l in levs if l > lev], default=None)
    if lo is None or hi is None:
        raise ValueError(f"{lev}hPa はこのモデルの範囲外です（{levs[0]}〜{levs[-1]}hPa）")
    w = (np.log(lev) - np.log(lo)) / (np.log(hi) - np.log(lo))
    return {k: F["p"][lo][k] * (1 - w) + F["p"][hi][k] * w for k in F["p"][lo]}, False


def _wrf_levels_fields(init, ft, plev=None, zm=None, key="wrf"):
    """WRF: 指定の気圧面 / 海抜高度での場（規則格子）。key は対照 wrf または実験ケース x_<ID>_dNN"""
    w = fieldsF._wrf_ds(init, key)
    ds = w["ds"]
    G, R = 9.80665, 287.05
    p = ds["P"][ft] + ds["PB"][ft]
    tk = (ds["T"][ft] + 300.0) * (p / 1e5) ** 0.2854
    ph = ds["PH"][ft] + ds["PHB"][ft]
    z = 0.5 * (ph[1:] + ph[:-1]) / G
    u = 0.5 * (ds["U"][ft][:, :, 1:] + ds["U"][ft][:, :, :-1])
    v = 0.5 * (ds["V"][ft][:, 1:, :] + ds["V"][ft][:, :-1, :])
    ww = 0.5 * (ds["W"][ft][1:] + ds["W"][ft][:-1])
    cosa, sina = ds["COSALPHA"][0], ds["SINALPHA"][0]
    ue, ve = u * cosa - v * sina, v * cosa + u * sina
    rh = fieldsF.rh_from_q(ds["QVAPOR"][ft], tk - 273.15, p / 100.0)
    omega = -(p / (R * tk)) * G * ww * 36.0
    fields = {"gh": z, "t": tk - 273.15, "u": ue, "v": ve, "r": rh, "w": omega}
    out = {}
    if plev is not None:
        logp = np.log(p)
        for k, a in fields.items():
            out[k] = fieldsF._regrid(w, fieldsF._to_levels(a, logp, plev))
        if plev < 1000:                                       # 地面より下は最下層の値で埋まるので隠す
            sp = fieldsF._regrid(w, ds["PSFC"][ft] / 100.0)
            for k in out:
                out[k] = np.where(sp < plev, np.nan, out[k])
    else:
        nz = z.shape[0]
        idx = (z < zm).sum(axis=0)                            # zm より低い面の数
        ok = (idx > 0) & (idx < nz)
        k1 = np.clip(idx, 1, nz - 1)
        k0 = k1 - 1
        z0 = np.take_along_axis(z, k0[None], 0)[0]
        z1 = np.take_along_axis(z, k1[None], 0)[0]
        wgt = np.clip((zm - z0) / (z1 - z0), 0, 1)
        for k, a in fields.items():
            v0 = np.take_along_axis(a, k0[None], 0)[0]
            v1 = np.take_along_axis(a, k1[None], 0)[0]
            val = np.where(ok, v0 + (v1 - v0) * wgt, np.nan)
            out[k] = fieldsF._regrid(w, val)
        out["gh"] = np.full_like(out["t"], zm)
    return out, w["glat"], w["glon"]


def get_fields(model, init, ft, level, unit, rr_window=6):
    """(場の辞書, lats, lons, 注記) を返す。level: 数値 または 'sfc'"""
    note = ""
    if level == "sfc":
        if model in ("aifs", "aifs_off") and rr_window != 6:
            raise ValueError("AIFS は6時間間隔の出力のため、降水は6時間平均のみです（RR3・RR1 は WRF・IFS で選べます）")
        if model == "ifs" and rr_window not in (3, 6):
            raise ValueError("IFS の降水は3時間・6時間平均です（RR1 は WRF のみ）")
        F = fieldsF.wrf_F(init, ft, rr_window=rr_window, levels=[], key=model) if fieldsF.is_wrf(model) else fieldsF.aifs_local_F(init, ft) if model == "aifs" \
            else fieldsF.ecmwf_F(model, init, ft, rr_window=rr_window, sfc_only=True)
        d = {"t2": F["t2"], "msl": F["msl"], "wspd10": np.hypot(F["u10"], F["v10"]), "tcc": F["tcc"],
             "u": F["u10"], "v": F["v10"]}
        if F.get("rr") is not None:
            d["rr"] = F["rr"]
        return d, F["lats"], F["lons"], note
    if unit == "m":
        if not fieldsF.is_wrf(model):
            raise ValueError("メートル高度は WRF だけで作れます（AIFS・IFS は等圧面のデータのみ）")
        d, la, lo = _wrf_levels_fields(init, ft, zm=float(level), key=model)
        return d, la, lo, f"海抜 {level:g}m（モデル面の高さで補間。地面がそれより高い所は空白）"
    lev = float(level)
    if fieldsF.is_wrf(model):
        d, la, lo = _wrf_levels_fields(init, ft, plev=lev, key=model)
        return d, la, lo, "WRFのモデル面から気圧面へ補間（地面より下は空白）"
    F = fieldsF.aifs_local_F(init, ft) if model == "aifs" else fieldsF.ecmwf_F(model, init, ft)
    d, exact = _interp_p(F, int(lev) if lev == int(lev) else lev)
    if not exact:
        note = f"{lev:g}hPa は提供面（13面）の間を log p で線形補間した値です"
    return d, F["lats"], F["lons"], note


def derived(d, lats, lons, var, level_hpa):
    """変数 var の2次元配列（lats×lons）を返す"""
    if var == "t": return d["t"]
    if var == "gh": return d["gh"]
    if var == "wspd": return np.hypot(d["u"], d["v"])
    if var == "rh": return d["r"]
    if var == "dd": return d["t"] - nc.dewpoint(d["t"], d["r"])
    if var == "vort": return nc.vorticity(d["u"], d["v"], lats, lons)
    if var == "div": return nc.divergence(d["u"], d["v"], lats, lons) * 1e6
    if var == "w": return d["w"]
    if var == "q": return nc.mixing_q(d["t"], d["r"], level_hpa)
    if var in ("ept", "theta"):
        import metpy.calc as mpcalc
        from metpy.units import units
        td = np.minimum(nc.dewpoint(d["t"], d["r"]), d["t"])
        if var == "ept":
            return mpcalc.equivalent_potential_temperature(level_hpa * units.hPa, d["t"] * units.degC, td * units.degC).to("K").m
        return mpcalc.potential_temperature(level_hpa * units.hPa, d["t"] * units.degC).to("K").m
    return d[var]


def _levels(a, step, n_target=14):
    lo, hi = np.nanpercentile(a, 1), np.nanpercentile(a, 99)
    if step is None or step <= 0:
        raw = (hi - lo) / n_target
        mag = 10 ** np.floor(np.log10(max(raw, 1e-9)))
        step = min([m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw], default=raw)
    return np.arange(np.floor(lo / step) * step, np.ceil(hi / step) * step + step * 0.5, step), step


def _crop(a, lats, lons, bbox, pad=None):
    lon0, lon1, lat0, lat1 = bbox
    if pad is None:                                           # 枠の縦横比の調整で範囲が広がるぶんの余白
        pad = max(1.0, 0.3 * max(lon1 - lon0, lat1 - lat0))
    ii = np.where((lats >= lat0 - pad) & (lats <= lat1 + pad))[0]
    jj = np.where((lons >= lon0 - pad) & (lons <= lon1 + pad))[0]
    if len(ii) < 2 or len(jj) < 2:
        raise ValueError("指定範囲にこのモデルのデータがありません（WRFは領域 122.5〜132.5E・22.1〜29.8N のみ）")
    return a[np.ix_(ii, jj)], lats[ii], lons[jj]


def render(p):
    """p: dict(kind=map|xsection|emagram, model, init, ft, …) → (png path, メモ)
    map: 水平の図（level, unit, fill, contour, interval, fill_step, wind, bbox, basemap, cmap, rr_window）
    xsection: 鉛直断面図（始点・終点 lat0,lon0,lat1,lon1、ptop、shade、contours、barbs、wind）
    emagram: エマグラム（地点 lat,lon、ptop、pt=温位エマグラム）"""
    with LOCK:
        k = p.get("kind") or "map"
        if k == "xsection":
            return _render_section(p)
        if k == "emagram":
            return _render_emagram(p)
        return _render(p)


# ---------- 断面図・エマグラム（nwp_chart_web と同じ描画コードを使う） ----------
XS_LEVELS = [1000, 975, 950, 925, 900, 875, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150, 100]


def _F_for_profile(model, init, ft, points):
    """断面・エマグラム用の F（WRFは22の気圧面へ補間。AIFS・IFSは提供の13面）。範囲外の点は ValueError"""
    if fieldsF.is_wrf(model):
        lon0, lon1, lat0, lat1 = fieldsF.wrf_bbox(init, model)
        for la, lo in points:
            if not (lat0 <= la <= lat1 and lon0 <= lo <= lon1):
                raise ValueError(f"WRFの領域外です（{lon0:g}〜{lon1:g}°E・{lat0:g}〜{lat1:g}°N）。始点・終点・地点を領域内に指定してください")
        return fieldsF.wrf_F(init, ft, levels=XS_LEVELS, key=model)
    F = fieldsF.aifs_local_F(init, ft) if model == "aifs" else fieldsF.ecmwf_F(model, init, ft)
    for la, lo in points:
        if not (F["lats"][0] <= la <= F["lats"][-1] and F["lons"][0] <= lo % 360 <= F["lons"][-1]):
            raise ValueError("データの範囲外です（東アジア広域: 50〜190°E・-10〜80°N）")
    return F


def _out_path(p):
    key = hashlib.md5(json.dumps(p, sort_keys=True).encode()).hexdigest()[:12]
    OUTDIR.mkdir(exist_ok=True)
    return key, OUTDIR / f"{key}.png"


def _render_section(p):
    import xsection
    model, init, ft = p["model"], p["init"], int(p["ft"])
    a = (float(p["lat0"]), float(p["lon0"])); b = (float(p["lat1"]), float(p["lon1"]))
    if a == b:
        raise ValueError("始点と終点が同じです")
    key, out = _out_path(p)
    F = _F_for_profile(model, init, ft, [a, b])
    opts = {"shade": p.get("shade") or "div", "contours": p.get("contours") or ["theta", "ept", "wind"],
            "barbs": p.get("barbs") or "front", "ptop": xsection.parse_ptop(p.get("ptop"))}
    xsection.render_xsection(F, datetime.strptime(init, "%Y%m%d%H"), ft, a, b, str(out), model=label_of(init, model),
                             name=p.get("name", ""), quantize=False, wind=p.get("wind") or "uv", opts=opts)
    note = "" if model == "wrf" else "AIFS・IFSの断面は提供13面（1000〜50hPa）の間を補間して描きます"
    (OUTDIR / f"{key}.json").write_text(json.dumps({"params": p, "note": note}, ensure_ascii=False))
    return out, note


def _render_emagram(p):
    import emagram
    model, init, ft = p["model"], p["init"], int(p["ft"])
    lat, lon = float(p["lat"]), float(p["lon"])
    key, out = _out_path(p)
    F = _F_for_profile(model, init, ft, [(lat, lon)])
    emagram.render_emagram(F, datetime.strptime(init, "%Y%m%d%H"), ft, lat, lon, str(out), model=label_of(init, model),
                           name=p.get("name", ""), quantize=False, ptop=int(p.get("ptop") or 100), pt=bool(p.get("pt")))
    note = "最寄りの格子点のプロファイルです" + ("" if model == "wrf" else "（AIFS・IFSは提供13面。細かい層は log p で補間）")
    (OUTDIR / f"{key}.json").write_text(json.dumps({"params": p, "note": note}, ensure_ascii=False))
    return out, note


def _render(p):
    model, init, ft = p["model"], p["init"], int(p["ft"])
    unit = p.get("unit", "hPa")
    level = "sfc" if p.get("level") in ("sfc", "", None) else float(p["level"])
    bbox = [float(x) for x in p["bbox"]]                     # lon0, lon1, lat0, lat1
    lon0, lon1, lat0, lat1 = bbox
    if not (lon1 > lon0 and lat1 > lat0 and lat1 - lat0 <= 100 and lon1 - lon0 <= 200):
        raise ValueError("表示範囲の指定が不正です（東端>西端、北端>南端、幅200度・高さ100度以内）")
    key = hashlib.md5(json.dumps(p, sort_keys=True).encode()).hexdigest()[:12]
    OUTDIR.mkdir(exist_ok=True)
    out = OUTDIR / f"{key}.png"

    d, lats, lons, note = get_fields(model, init, ft, level, unit, int(p.get("rr_window") or 6))
    lev_hpa = float(level) if level != "sfc" and unit != "m" else 850.0
    svars = SFC_VARS if level == "sfc" else VARS
    fill_id, cont_id = p.get("fill") or None, p.get("contour") or None

    def field(var):
        if level == "sfc":
            return d[var]
        return derived(d, lats, lons, var, lev_hpa)

    # --- 図の枠（メルカトル）
    proj = ccrs.Mercator(central_longitude=(lon0 + lon1) / 2)
    x0, y0 = proj.transform_point(lon0, lat0, PC)
    x1, y1 = proj.transform_point(lon1, lat1, PC)
    wpx = 950
    ax_w, ax_h = 0.86, None
    aspect = (y1 - y0) / (x1 - x0)
    map_w_px = wpx * ax_w
    map_h_px = map_w_px * aspect
    map_h_px = min(max(map_h_px, 340), 900)
    fig_h = (map_h_px + 170) / 100.0
    fig = plt.figure(figsize=(wpx / 100.0, fig_h), dpi=100)
    ax = fig.add_axes([0.07, 100 / (fig_h * 100), ax_w - 0.07 + 0.0, map_h_px / (fig_h * 100)], projection=proj)
    # 枠の実際の縦横比に合わせて範囲を調整（引き伸ばし防止）
    frame_aspect = map_h_px / (wpx * (ax_w - 0.07))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw = (x1 - x0) / 2
    hh = hw * frame_aspect
    if hh < (y1 - y0) / 2:
        hh = (y1 - y0) / 2
        hw = hh / frame_aspect
    extent = (cx - hw, cx + hw, cy - hh, cy + hh)
    ax.set_extent(extent, crs=proj)
    kind = p.get("basemap", "pale")
    if kind != "none":
        if len(bm._cache) > 12:
            bm._cache.clear()
        W = int(wpx * (ax_w - 0.07)); H = int(map_h_px)
        img = bm.for_axes(kind, proj, extent, f"custom{key}", W, H)
        ax.imshow(img, origin="upper", extent=extent, transform=proj, zorder=0.5, interpolation="bilinear")
        ax.set_extent(extent, crs=proj)
    # 海岸線
    import cartopy.feature as cfeature
    res = "10m" if (lon1 - lon0) < 40 else "50m"
    ax.add_feature(cfeature.COASTLINE.with_scale(res), linewidth=0.8, edgecolor="k", zorder=4)
    ax.add_feature(cfeature.NaturalEarthFeature("cultural", "admin_0_boundary_lines_land", "50m"),
                   linewidth=0.4, edgecolor="#555", facecolor="none", zorder=4)
    gstep = 1 if (lon1 - lon0) <= 8 else 2 if (lon1 - lon0) <= 20 else 5 if (lon1 - lon0) <= 60 else 10
    gl = ax.gridlines(crs=PC, draw_labels=True, linewidth=0.5, color="#444", alpha=0.7,
                      xlocs=np.arange(-180, 361, gstep), ylocs=np.arange(-80, 91, gstep))
    gl.top_labels = gl.right_labels = False
    gl.xlabel_style = gl.ylabel_style = {"size": 8}
    gl.set_zorder(4)

    notes = [note] if note else []
    legend_items = []

    def plot_grid(a):
        a2, la2, lo2 = _crop(a, lats, lons, bbox)
        zf = 3 if lo2[1] - lo2[0] >= 0.2 else 2
        z = zoom(a2, zf, order=1, mode="nearest", grid_mode=False) if min(a2.shape) > 1 else a2
        lonf = np.linspace(lo2[0], lo2[-1], z.shape[1]); latf = np.linspace(la2[0], la2[-1], z.shape[0])
        X, Y = np.meshgrid(lonf, latf)
        xy = proj.transform_points(PC, X, Y)
        return xy[..., 0], xy[..., 1], z

    if fill_id:
        a = field(fill_id)
        name, unit_s, cmap, step = svars[fill_id]
        X, Y, Z = plot_grid(a)
        step_in = float(p["fill_step"]) if p.get("fill_step") else step
        if fill_id == "rr":
            lv = np.array([0.1, 0.5, 1, 2, 4, 8, 16, 32])
        else:
            lv, _ = _levels(a, step_in)
        cf = ax.contourf(X, Y, Z, levels=lv, cmap=p.get("cmap") or cmap, extend="both", alpha=0.72 if kind != "none" else 1.0, zorder=2)
        cb = fig.colorbar(cf, ax=ax, orientation="horizontal", pad=0.07, fraction=0.045, aspect=45)
        cb.set_label(f"{name} [{unit_s}]", fontsize=9); cb.ax.tick_params(labelsize=8)
    if cont_id:
        a = field(cont_id)
        name, unit_s, _, step = svars[cont_id]
        X, Y, Z = plot_grid(a)
        lv, st = _levels(a, float(p["interval"]) if p.get("interval") else step)
        cs = ax.contour(X, Y, Z, levels=lv, colors="#202020", linewidths=0.9, zorder=3)
        ax.clabel(cs, fmt="%g", fontsize=7)
        legend_items.append(f"等値線: {name} {st:g}{unit_s}ごと")
    wind = p.get("wind") or "none"
    if wind != "none":
        u2, la2, lo2 = _crop(d["u"], lats, lons, bbox)
        v2, _, _ = _crop(d["v"], lats, lons, bbox)
        n = max(1, int(round(max(u2.shape) / 22)))
        X, Y = np.meshgrid(lo2, la2)
        xy = proj.transform_points(PC, X, Y)
        xs, ys = xy[..., 0][::n, ::n], xy[..., 1][::n, ::n]
        if wind == "barbs":
            ax.barbs(xs, ys, u2[::n, ::n], v2[::n, ::n], length=5.2, linewidth=0.6, color="#102040",
                     barb_increments=dict(half=2.5, full=5, flag=25), zorder=5)
        else:
            q = ax.quiver(xs, ys, u2[::n, ::n], v2[::n, ::n], color="#102040", scale=None, width=0.0022, zorder=5)
            ax.quiverkey(q, 0.92, 1.03, 10, "10 m/s", labelpos="E", fontproperties={"size": 8})
    # --- 見出し
    init_dt = datetime.strptime(init, "%Y%m%d%H")
    vt = init_dt + timedelta(hours=ft)
    jst = vt + timedelta(hours=9)
    lv_txt = "地上" if level == "sfc" else (f"海抜{level:g}m" if unit == "m" else f"{level:g}hPa")
    parts = [x for x in (f"塗り: {svars[fill_id][0]}" if fill_id else "", legend_items[0] if legend_items else "",
                         {"barbs": "風: 矢羽", "arrows": "風: 矢印"}.get(wind, "")) if x]
    fig.text(0.07, 1 - 18 / (fig_h * 100), f"{label_of(init, model)}　{lv_txt}　" + "　".join(parts), fontsize=12, weight="bold", va="center")
    fig.text(0.07, 1 - 42 / (fig_h * 100),
             f"初期 {init_dt:%Y-%m-%d %H}UTC　+{ft:02d}h　有効 {vt:%m/%d %H}UTC（{jst:%d日%H時}JST）　{lon0:g}〜{lon1:g}E {lat0:g}〜{lat1:g}N",
             fontsize=9.5, color="#334", va="center")
    fig.text(0.995, 12 / (fig_h * 100), "公式予報ではありません　" + (bm.credit(kind) if kind != "none" else ""),
             fontsize=7, color="#555", ha="right", va="center")
    for n_ in notes:
        fig.text(0.07, 12 / (fig_h * 100), n_, fontsize=7.5, color="#b35c00", va="center")
    fig.savefig(out, dpi=100)
    plt.close(fig)
    (OUTDIR / f"{key}.json").write_text(json.dumps({"params": p, "note": "；".join(notes)}, ensure_ascii=False))
    return out, "；".join(notes)


XS_PRESETS = [
    {"name": "沖縄: 東西（26°N, 122.5〜132.3°E）", "p": [26, 122.5, 26, 132.3]}, {"name": "沖縄: 南北（127.5°E, 23〜29.5°N）", "p": [23, 127.5, 29.5, 127.5]},
    {"name": "九州〜東シナ海（33°N 130°E → 28°N 124°E）", "p": [33, 130, 28, 124]},
    {"name": "日本列島を横切る（東西 35°N, 128〜145°E）", "p": [35, 128, 35, 145]}, {"name": "南北（135°E, 20〜45°N）", "p": [20, 135, 45, 135]},
]
POINTS = [
    {"name": "那覇", "lat": 26.2, "lon": 127.7}, {"name": "石垣島", "lat": 24.3, "lon": 124.2}, {"name": "南大東島", "lat": 25.8, "lon": 131.2},
    {"name": "福岡", "lat": 33.6, "lon": 130.4}, {"name": "東京", "lat": 35.7, "lon": 139.7}, {"name": "札幌", "lat": 43.1, "lon": 141.3},
    {"name": "台北", "lat": 25.0, "lon": 121.5}, {"name": "上海", "lat": 31.2, "lon": 121.5},
]

PRESETS = [
    {"name": "日本周辺", "bbox": [118, 152, 22, 50]}, {"name": "沖縄周辺", "bbox": [118, 138, 20, 34]},
    {"name": "九州・西日本", "bbox": [127, 140, 30, 37]}, {"name": "関東・東日本", "bbox": [136, 146, 33, 41]},
    {"name": "東アジア広域", "bbox": [60, 170, 0, 60]}, {"name": "WRF領域（沖縄周辺9km）", "bbox": [122.8, 132.2, 22.3, 29.6]},
    {"name": "台湾周辺", "bbox": [116, 126, 20, 27]}, {"name": "南西諸島（拡大）", "bbox": [124, 132, 24, 30]},
]


def options():
    """画面に出す選択肢: 計算済みの初期時刻・モデル・予報時間、変数、プリセット"""
    runs = []
    for d in sorted((HERE.parent / "runs").glob("[0-9]*"), reverse=True):
        init = d.name
        models = {}
        refs = HERE.parent / "data"
        for mk, name in (("ifs", "ifs"), ("aifs_off", "aifs-single")):
            if (refs / f"ref_{name}_{init}.grib2").exists() and (refs / f"ref_{name}_{init}_pl.grib2").exists():
                models[mk] = {"label": LABELS[mk], "steps": [0, 6, 12, 18, 24]}
        n = len(list((d / "aifs").glob(f"aifsfull_{init}_*.npz")))
        if n:
            models["aifs"] = {"label": LABELS["aifs"], "steps": [6 * (i + 1) for i in range(n)]}
        for key, src in fieldsF.wrf_sources(init).items():
            models[key] = {"label": src["label"], "steps": fieldsF.wrf_hours(init, key)}
        if models:
            runs.append({"init": init, "models": models})
    return {"runs": runs, "vars": {k: {"label": v[0], "unit": v[1], "step": v[3]} for k, v in VARS.items()},
            "sfc_vars": {k: {"label": v[0], "unit": v[1], "step": v[3]} for k, v in SFC_VARS.items()},
            "presets": PRESETS, "basemaps": bm.KINDS, "xs_presets": XS_PRESETS, "points": POINTS}


def history(n=24):
    items = []
    for j in sorted(OUTDIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:n]:
        if j.with_suffix(".png").exists():
            d = json.loads(j.read_text())
            items.append({"url": f"/custom_out/{j.stem}.png", "key": j.stem, "params": d["params"], "note": d.get("note", ""),
                          "published": j.stem in {x["key"] for x in load_published()}, "blocked": publish_blocked(d["params"])})
    return items


# ---------- 公開サイトのギャラリー（ローカルで選んだ図だけを公開する） ----------
PUBLISH_LIST = HERE / "published_custom.json"
GSI_KINDS = ("pale", "relief", "hillshade", "std")       # 国土地理院タイル（公開不可。NOTICE.md）


def describe(p):
    """図の説明文（ギャラリーの見出し）"""
    init, model, ft = p["init"], p["model"], int(p["ft"])
    head = f"{label_of(init, model)}　初期 {init[:4]}-{init[4:6]}-{init[6:8]} {init[8:]}UTC +{ft:02d}h　"
    k = p.get("kind") or "map"
    if k == "xsection":
        return head + f"鉛直断面 ({p['lat0']}N,{p['lon0']}E)→({p['lat1']}N,{p['lon1']}E)　上端{p.get('ptop', 100)}hPa　色塗り:{p.get('shade', '')}"
    if k == "emagram":
        return head + f"{'温位エマグラム' if p.get('pt') else 'エマグラム'}　{p.get('name') or str(p['lat']) + 'N ' + str(p['lon']) + 'E'}"
    lv = "地上" if p.get("level") in ("sfc", "", None) else (f"海抜{p['level']}m" if p.get("unit") == "m" else f"{p['level']}hPa")
    sv = SFC_VARS if lv == "地上" else VARS
    nm = lambda v: sv[v][0] if v in sv else ""  # noqa: E731
    b = p["bbox"]
    return head + f"{lv}　塗り:{nm(p.get('fill'))}　等値線:{nm(p.get('contour'))}　{b[0]}〜{b[1]}E {b[2]}〜{b[3]}N"


def publish_blocked(p):
    """公開してはいけない図の理由（国土地理院タイルを背景にした平面図）。なければ None"""
    if (p.get("kind") or "map") == "map" and p.get("basemap", "pale") in GSI_KINDS:
        return "国土地理院タイルを背景にした図は公開できません（加工画像の再配布の可否が未確認。NOTICE.md）。背景を「標高の陰影」か「なし」にして作り直してください"
    return None


def load_published():
    try:
        return json.loads(PUBLISH_LIST.read_text())
    except Exception:  # noqa: BLE001
        return []


def set_published(key, on):
    import re as _re
    if not _re.fullmatch(r"[0-9a-f]{12}", key or ""):
        raise ValueError("不正なキーです")
    meta = OUTDIR / f"{key}.json"
    if not meta.exists() or not (OUTDIR / f"{key}.png").exists():
        raise ValueError("その図は見つかりません")
    items = [x for x in load_published() if x["key"] != key]
    if on:
        d = json.loads(meta.read_text())
        why = publish_blocked(d["params"])
        if why:
            raise ValueError(why)
        items.append({"key": key, "caption": describe(d["params"]), "note": d.get("note", "")})
    PUBLISH_LIST.write_text(json.dumps(items, ensure_ascii=False, indent=1))
    return items
