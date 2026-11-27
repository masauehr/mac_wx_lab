"""各モデルの予報を、nwp_chart 形式の F（辞書）にする。

F = {"lats"(昇順), "lons"(0〜360), "msl"[hPa], "sp"[hPa], "u10", "v10", "t2"[℃], "tcc"[%],
     "rr"[mm/h]|None, "rr_h"(降水の平均時間),
     "p": {気圧面: {"gh"[m], "t"[℃], "u", "v", "r"[%], "w"[hPa/h]}}}
IFS・公式AIFS・手元AIFS は 0.25度の規則格子（東アジア広域に切り出し）。WRF は WRF 領域を 0.1度の規則格子にそろえる。
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]
BOX = dict(lat=(-10.0, 80.0), lon=(50.0, 190.0))   # 切り出し範囲（nwp_chart の asia 地域より少し広い）


def rh_from_q(q, t_c, p_hpa):
    """比湿 [kg/kg] → 相対湿度 [%]（Bolton式）"""
    e = q * p_hpa / (0.622 + 0.378 * q)
    es = 6.112 * np.exp(17.67 * t_c / (t_c + 243.5))
    return np.clip(100.0 * e / es, 0, 100)


def _ascending(F):
    """緯度を昇順（南→北）にそろえる"""
    if F["lats"][0] > F["lats"][-1]:
        F["lats"] = F["lats"][::-1]
        for k in ("msl", "sp", "u10", "v10", "t2", "tcc", "rr"):
            if F.get(k) is not None:
                F[k] = F[k][::-1, :]
        for z in F["p"].values():
            for k in z:
                z[k] = z[k][::-1, :]
    return F


def _crop(lat, lon, arr):
    ii = np.where((lat >= BOX["lat"][0]) & (lat <= BOX["lat"][1]))[0]
    jj = np.where((lon >= BOX["lon"][0]) & (lon <= BOX["lon"][1]))[0]
    return lat[ii], lon[jj], arr[np.ix_(ii, jj)]


# ---------- ECMWF オープンデータ（IFS・公式AIFS） ----------
_GRIB = {}


def _read_grib(path):
    """GRIB2 を (param, level|None, step) → 2次元配列 の辞書にする（東アジア広域に切り出し）"""
    if path in _GRIB:
        return _GRIB[path]
    import earthkit.data as ekd

    out, lat, lon = {}, None, None
    for f in ekd.from_source("file", str(path)):
        ll = f.to_latlon()
        la, lo = ll["lat"][:, 0], np.mod(ll["lon"][0], 360)
        order = np.argsort(lo)
        a = f.to_numpy()[:, order]
        la2, lo2, a = _crop(la, lo[order], a)
        lat, lon = la2, lo2
        lev = f.metadata("level") if f.metadata("levtype") == "pl" else None
        out[(f.metadata("param"), lev, int(f.metadata("step")))] = a.astype("float32")
    _GRIB[path] = (lat, lon, out)
    return _GRIB[path]


def ecmwf_steps(key, init):
    """気圧面データがある予報時間（6時間ごと）"""
    base = ROOT / "data" / f"ref_{'ifs' if key == 'ifs' else 'aifs-single'}_{init}"
    if not (Path(str(base) + ".grib2").exists() and Path(str(base) + "_pl.grib2").exists()):
        return []
    _, _, d = _read_grib(str(base) + ".grib2")
    return sorted({s for (_, _, s) in d})


def ecmwf_sfc3_steps(key, init):
    """IFSの3時間ごとの地上量だけがある予報時間（降水RR3用）"""
    f = ROOT / "data" / f"ref_ifs_{init}_sfc3.grib2"
    if key != "ifs" or not f.exists():
        return []
    _, _, d = _read_grib(str(f))
    return sorted({s for (_, _, s) in d})


def ecmwf_F(key, init, ft, rr_window=6, sfc_only=False):
    base = str(ROOT / "data" / f"ref_{'ifs' if key == 'ifs' else 'aifs-single'}_{init}")
    lat, lon, sfc = _read_grib(base + ".grib2")
    if key == "ifs" and Path(base + "_sfc3.grib2").exists():          # 3時間ごとの地上量を足す
        sfc = {**sfc, **_read_grib(base + "_sfc3.grib2")[2]}
    pl = {} if sfc_only else dict(_read_grib(base + "_pl.grib2")[2])
    plz = Path(base + "_plz.grib2")
    if not sfc_only and plz.exists():                       # 公式AIFS（AWSミラーの2025年分）は高度が z（ジオポテンシャル）で別提供
        for (pm, lv, st), a in _read_grib(str(plz))[2].items():
            if pm == "z" and ("gh", lv, st) not in pl:
                pl[("gh", lv, st)] = a / 9.80665              # ジオポテンシャル → ジオポテンシャル高度 [m]
    tp_scale = 1000.0 if key == "ifs" else 1.0
    F = {"lats": lat, "lons": lon, "p": {}}
    F["msl"] = sfc[("msl", None, ft)] / 100.0
    F["sp"] = sfc[("sp", None, ft)] / 100.0
    F["u10"], F["v10"] = sfc[("10u", None, ft)], sfc[("10v", None, ft)]
    F["t2"] = sfc[("2t", None, ft)] - 273.15
    tcc = sfc.get(("tcc", None, ft))            # 初期時刻（0時間）に全雲量が無いデータがある（2025-09など）→ 0（晴）で補う
    F["tcc"] = np.zeros_like(F["msl"]) if tcc is None else tcc * (100.0 if tcc.max() <= 1.5 else 1.0)
    F["rr"], F["rr_h"] = None, rr_window
    if ft >= rr_window and ("tp", None, ft) in sfc:
        prev = sfc[("tp", None, ft - rr_window)] if ("tp", None, ft - rr_window) in sfc else 0
        F["rr"] = ((sfc[("tp", None, ft)] - prev) * tp_scale / rr_window).clip(min=0)    # mm/h（直前 rr_window 時間の平均）
    for lv in LEVELS:
        try:
            z = {"gh": pl[("gh", lv, ft)], "t": pl[("t", lv, ft)] - 273.15, "u": pl[("u", lv, ft)], "v": pl[("v", lv, ft)]}
        except KeyError:
            continue
        if ("r", lv, ft) in pl:
            z["r"] = pl[("r", lv, ft)]
        elif ("q", lv, ft) in pl:
            z["r"] = rh_from_q(pl[("q", lv, ft)], z["t"], float(lv))
        else:
            z["r"] = np.full_like(z["t"], 50.0)
        z["w"] = pl[("w", lv, ft)] * 36.0 if ("w", lv, ft) in pl else np.zeros_like(z["t"])   # Pa/s → hPa/h
        F["p"][lv] = z
    return _ascending(F)


# ---------- 手元のAIFS（run_aifs.py が保存した全変数） ----------
def _aifs_files(init):
    d = ROOT / "runs" / init / "aifs"
    return sorted(d.glob(f"aifsfull_{init}_*.npz"))


def aifs_local_steps(init):
    return [6 * (i + 1) for i in range(len(_aifs_files(init)))]


def aifs_local_F(init, ft):
    files = _aifs_files(init)
    steps = aifs_local_steps(init)
    d = np.load(files[steps.index(ft)])
    F = {"lats": d["lat"], "lons": d["lon"], "p": {}}
    F["msl"], F["sp"] = d["msl"] / 100.0, d["sp"] / 100.0
    F["u10"], F["v10"], F["t2"] = d["10u"], d["10v"], d["2t"] - 273.15
    F["tcc"] = d["tcc"] * 100.0 if "tcc" in d.files else np.zeros_like(F["msl"])
    F["rr"], F["rr_h"] = (d["tp"] * 1000.0 / 6.0).clip(min=0), 6      # 出力は6時間ごとの降水量 [m]
    for lv in LEVELS:
        if f"z_{lv}" not in d.files:
            continue
        t_c = d[f"t_{lv}"] - 273.15
        F["p"][lv] = {"gh": d[f"z_{lv}"] / 9.80665, "t": t_c, "u": d[f"u_{lv}"], "v": d[f"v_{lv}"],
                      "r": rh_from_q(d[f"q_{lv}"], t_c, float(lv)), "w": d[f"w_{lv}"] * 36.0}
    return _ascending(F)


# ---------- WRF（気圧面へ鉛直補間し、規則格子へ）。対照実験と、実験ケース（領域・物理設定・地形を変えたもの）の全てを扱う ----------
_WRF = {}
WRF_LEVELS = LEVELS + [975, 950, 900, 800]


def is_wrf(key):
    """モデルキーがWRF系か（wrf = 対照の9km。x_<ケース>_d0N = 実験ケースの領域N）"""
    return key == "wrf" or key.startswith("x_")


def grid_deg(dx):
    """規則格子へ変換するときの間隔 [度]（WRFの格子間隔に合わせる）"""
    return 0.1 if dx >= 8000 else 0.03 if dx >= 2500 else 0.01 if dx >= 900 else 0.005


def wrf_sources(init):
    """{モデルキー: {path, label, deg, dx, case, dom, cfg}}。対照 'wrf' と、runs/<初期時刻>/wrfx/<ケース>/ の各領域"""
    import json

    out = {}
    ctl = ROOT / "runs" / init / "wrf" / "wrfout"
    if ctl.exists():
        out["wrf"] = dict(path=ctl, label="WRF 9km", deg=0.1, dx=9000, case="ctl", dom=1, cfg=None)
    xdir = ROOT / "runs" / init / "wrfx"
    for cj in sorted(xdir.glob("*/case.json")) if xdir.exists() else []:
        cid = cj.parent.name
        info = json.loads(cj.read_text())
        cfg = info["cfg"]
        for i, d in enumerate(cfg["domains"], 1):
            f = cj.parent / f"wrfout_d{i:02d}"
            if f.exists():
                dx = float(d["dx"])
                out[f"x_{cid}_d{i:02d}"] = dict(path=f, label=f"WRF {cfg.get('label', cid)} d{i:02d}（{dx / 1000:g}km）", deg=grid_deg(dx),
                                                dx=dx, case=cid, dom=i, cfg=cfg)
    return out


def _wrf_ds(init, key="wrf"):
    import netCDF4 as nc

    k = (init, key)
    if k not in _WRF:
        src = wrf_sources(init)[key]
        deg = src["deg"]
        ds = nc.Dataset(src["path"])
        ds.set_auto_mask(False)                 # マスク配列にしない（補間で使うため）
        lat, lon = ds["XLAT"][0].astype(float), ds["XLONG"][0].astype(float)
        # 領域に内接する矩形（deg刻み）
        m = 1.0 * deg
        la0, la1 = np.ceil(lat[0, :].max() / deg) * deg + m, np.floor(lat[-1, :].min() / deg) * deg - m
        lo0, lo1 = np.ceil(lon[:, 0].max() / deg) * deg + m, np.floor(lon[:, -1].min() / deg) * deg - m
        glat = np.round(np.arange(la0, la1 + 1e-6, deg), 4)
        glon = np.round(np.arange(lo0, lo1 + 1e-6, deg), 4)
        from scipy.spatial import Delaunay

        tri = Delaunay(np.stack([lon.ravel(), lat.ravel()], axis=-1))
        # ファイルの最初の時刻が、初期時刻から何時間後か（親より遅れて開始するネストは off>0）
        from datetime import datetime as _dt
        t0 = _dt.strptime("".join(c.decode() for c in ds["Times"][0]), "%Y-%m-%d_%H:%M:%S")
        off = int(round((t0 - _dt.strptime(init, "%Y%m%d%H")).total_seconds() / 3600.0))
        _WRF[k] = dict(ds=ds, lat=lat, lon=lon, glat=glat, glon=glon, tri=tri, times=len(ds["Times"]), deg=deg, off=off)
    return _WRF[k]


def wrf_steps(init, key="wrf"):
    if key not in wrf_sources(init):
        return []
    w = _wrf_ds(init, key)
    return [ft for ft in range(w["off"], w["off"] + w["times"]) if ft % 3 == 0]        # 初期時刻から3時間ごと（遅れて開始するネストは開始以降）


def wrf_hours(init, key="wrf"):
    """出力のある全時刻（毎時）"""
    if key not in wrf_sources(init):
        return []
    w = _wrf_ds(init, key)
    return list(range(w["off"], w["off"] + w["times"]))


def wrf_bbox(init, key="wrf"):
    w = _wrf_ds(init, key)
    return (float(w["glon"][0]), float(w["glon"][-1]), float(w["glat"][0]), float(w["glat"][-1]))


def _regrid(w, field):
    """WRFの曲線格子の場 → 0.1度の規則格子"""
    from scipy.interpolate import LinearNDInterpolator

    f = LinearNDInterpolator(w["tri"], np.asarray(field, float).ravel())
    gl, go = np.meshgrid(w["glat"], w["glon"], indexing="ij")
    return f(go, gl).astype("float32")


def _to_levels(var3d, logp, plev):
    """モデル面の3次元場 → 指定した気圧面（log p で線形補間。地面より下は最下層の値）。logp: (nz, ny, nx) 下から上へ減少"""
    out = np.empty(var3d.shape[1:], np.float32)
    lt = np.log(plev * 100.0)
    nz = logp.shape[0]
    idx = (logp > lt).sum(axis=0)                          # lt より気圧が高い（下の）面の数
    k1 = np.clip(idx, 1, nz - 1)
    k0 = k1 - 1
    l0 = np.take_along_axis(logp, k0[None], 0)[0]
    l1 = np.take_along_axis(logp, k1[None], 0)[0]
    v0 = np.take_along_axis(var3d, k0[None], 0)[0]
    v1 = np.take_along_axis(var3d, k1[None], 0)[0]
    wgt = np.clip((lt - l0) / (l1 - l0), -0.0, 1.0)         # 地面より下(idx=0)は最下層、外挿はしない
    out[:] = v0 + (v1 - v0) * wgt
    return out


def wrf_F(init, ft, rr_window=6, levels=None, key="wrf"):
    w = _wrf_ds(init, key)
    ds = w["ds"]
    ft = ft - w["off"]                       # 初期時刻からの時間 → このファイルの時刻の番号
    G, R = 9.80665, 287.05
    p = ds["P"][ft] + ds["PB"][ft]                                         # Pa (nz, ny, nx)
    th = ds["T"][ft] + 300.0
    tk = th * (p / 1e5) ** 0.2854
    ph = ds["PH"][ft] + ds["PHB"][ft]
    z = 0.5 * (ph[1:] + ph[:-1]) / G                                       # m（質量面）
    u = 0.5 * (ds["U"][ft][:, :, 1:] + ds["U"][ft][:, :, :-1])
    v = 0.5 * (ds["V"][ft][:, 1:, :] + ds["V"][ft][:, :-1, :])
    ww = 0.5 * (ds["W"][ft][1:] + ds["W"][ft][:-1])
    cosa, sina = ds["COSALPHA"][0], ds["SINALPHA"][0]
    ue, ve = u * cosa - v * sina, v * cosa + u * sina
    q = ds["QVAPOR"][ft]
    rh = rh_from_q(q, tk - 273.15, p / 100.0)
    omega = -(p / (R * tk)) * G * ww * 36.0                                  # hPa/h（上昇が負）
    logp = np.log(p)
    F = {"lats": w["glat"].copy(), "lons": w["glon"].copy(), "p": {}}
    # データのある範囲（広い地域の図で、周りを空白にして、この枠を示す）
    F["frame"] = (float(w["glon"][0]), float(w["glon"][-1]), float(w["glat"][0]), float(w["glat"][-1]))
    t2 = ds["T2"][ft]
    psfc = ds["PSFC"][ft]
    h = ds["HGT"][0]
    F["msl"] = _regrid(w, psfc * np.exp(G * h / (R * t2)) / 100.0)
    F["sp"] = _regrid(w, psfc / 100.0)
    F["u10"] = _regrid(w, ds["U10"][ft] * cosa - ds["V10"][ft] * sina)
    F["v10"] = _regrid(w, ds["V10"][ft] * cosa + ds["U10"][ft] * sina)
    F["t2"] = _regrid(w, t2 - 273.15)
    # 全雲量の目安: 雲水・雲氷・雪・霰の柱の量から（実測の雲量ではなく近似）
    cw = (ds["QCLOUD"][ft] + ds["QICE"][ft] + ds["QSNOW"][ft] if "QSNOW" in ds.variables else ds["QCLOUD"][ft] + ds["QICE"][ft])
    path = (cw * (p / (R * tk)) * np.gradient(z, axis=0)).sum(axis=0) * 1000.0      # g/m2
    F["tcc"] = _regrid(w, 100.0 * (1.0 - np.exp(-path / 20.0)))
    window = rr_window if ft >= rr_window else max(ft, 1)
    acc = lambda t: ds["RAINC"][t] + ds["RAINNC"][t]
    F["rr"] = None if ft == 0 else _regrid(w, ((acc(ft) - acc(ft - window)) / window).clip(min=0))
    F["rr_h"] = window
    for lv in (levels if levels is not None else WRF_LEVELS):
        F["p"][lv] = {"gh": _regrid(w, _to_levels(z, logp, lv)), "t": _regrid(w, _to_levels(tk, logp, lv) - 273.15),
                      "u": _regrid(w, _to_levels(ue, logp, lv)), "v": _regrid(w, _to_levels(ve, logp, lv)),
                      "r": _regrid(w, _to_levels(rh, logp, lv)), "w": _regrid(w, _to_levels(omega, logp, lv))}
    return F
