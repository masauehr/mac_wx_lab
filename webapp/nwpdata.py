"""4つのモデル（WRF・手元AIFS・公式AIFS・IFS）を共通の形で読み込む。

どのモデルも Model オブジェクトにして、次の形で扱う:
  m.steps            予報時間 [h] のリスト
  m.lat, m.lon       緯度・経度（WRFは2次元、0.25度の規則格子は1次元）
  m.field(ft, var)   2次元配列。var は msl[hPa]・t2[℃]・u10・v10[m/s]・wspd[m/s]・tpacc[mm, 積算]
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LABELS = {"wrf": "WRF 9km", "aifs": "AIFS（手元）", "aifs_off": "AIFS（公式）", "ifs": "IFS（ECMWF）"}
MODEL_ORDER = ["wrf", "aifs", "aifs_off", "ifs"]


class Model:
    def __init__(self, key, lat, lon, steps, getter, regular):
        self.key, self.lat, self.lon, self.steps = key, lat, lon, steps
        self._get, self.regular = getter, regular
        self._cache = {}

    @property
    def label(self):
        return LABELS[self.key]

    def field(self, ft, var):
        k = (ft, var)
        if k not in self._cache:
            self._cache[k] = self._get(ft, var)
        return self._cache[k]


# ---------- WRF ----------
def load_wrf(run_dir):
    import netCDF4 as nc

    p = Path(run_dir) / "wrf" / "wrfout"
    if not p.exists():
        return None
    d = nc.Dataset(p)
    lat, lon = d["XLAT"][0].astype(float), d["XLONG"][0].astype(float)
    cosa, sina = d["COSALPHA"][0], d["SINALPHA"][0]
    hgt = d["HGT"][0]
    n = len(d["Times"])

    def get(ft, var):
        if var == "msl":  # 簡易の海面更正（海上ではほぼ正確）
            return d["PSFC"][ft] * np.exp(9.80665 * hgt / (287.05 * d["T2"][ft])) / 100
        if var == "t2":
            return d["T2"][ft] - 273.15
        if var in ("u10", "v10", "wspd"):
            u, v = d["U10"][ft], d["V10"][ft]
            ue, ve = u * cosa - v * sina, v * cosa + u * sina  # 格子の向き → 東西・南北
            return {"u10": ue, "v10": ve, "wspd": np.hypot(u, v)}[var]
        if var == "tpacc":
            return d["RAINC"][ft] + d["RAINNC"][ft]
        raise KeyError(var)

    m = Model("wrf", lat, lon, list(range(n)), get, regular=False)
    m.landmask = d["LANDMASK"][0]
    return m


# ---------- ECMWF オープンデータ（IFS・公式AIFS） ----------
def _load_grib(path, tp_scale):
    import earthkit.data as ekd

    raw = {}
    lat = lon = None
    for f in ekd.from_source("file", str(path)):
        ll = f.to_latlon()
        la, lo = ll["lat"][:, 0], np.mod(ll["lon"][0], 360)
        order = np.argsort(lo)
        lat, lon = la, lo[order]
        p, s = f.metadata("param"), int(f.metadata("step"))
        v = f.to_numpy()[:, order]
        v = {"msl": v / 100, "2t": v - 273.15, "tp": v * tp_scale}.get(p, v)
        raw[(p, s)] = v
    steps = sorted({s for _, s in raw})

    def get(ft, var):
        if var == "wspd":
            return np.hypot(raw[("10u", ft)], raw[("10v", ft)])
        key = {"t2": "2t", "u10": "10u", "v10": "10v", "tpacc": "tp", "msl": "msl"}[var]
        return raw[(key, ft)]

    return lat, lon, steps, get


def load_ecmwf(key, init):
    path = ROOT / "data" / f"ref_{'ifs' if key == 'ifs' else 'aifs-single'}_{init}.grib2"
    if not path.exists():
        return None
    lat, lon, steps, get = _load_grib(path, 1000 if key == "ifs" else 1)  # IFSは m、公式AIFSは mm
    return Model(key, lat, lon, steps, get, regular=True)


# ---------- 手元のAIFS（anemoi出力: N320格子・6時間ごとの降水） ----------
def load_aifs(run_dir, init):
    import earthkit.regrid as ekr

    files = sorted((Path(run_dir) / "aifs").glob(f"aifs_{init}_*.npz"))
    if not files:
        return None
    lat, lon = np.linspace(90, -90, 721), np.arange(0, 360, 0.25)
    steps = [6 * (i + 1) for i in range(len(files))]

    def reg(x):
        return ekr.interpolate(x, {"grid": "N320"}, {"grid": (0.25, 0.25)})

    data = {}
    for ft, fp in zip(steps, files):
        d = np.load(fp)
        data[ft] = {"msl": reg(d["msl"]) / 100, "t2": reg(d["2t"]) - 273.15,
                    "u10": reg(d["10u"]), "v10": reg(d["10v"]), "tp": reg(d["tp"]) * 1000}
    acc, cum = {}, 0
    for ft in steps:  # 出力は6時間ごとの降水量 [m] → 積算 [mm]
        cum = cum + data[ft]["tp"]
        acc[ft] = cum.copy()

    def get(ft, var):
        if var == "wspd":
            return np.hypot(data[ft]["u10"], data[ft]["v10"])
        if var == "tpacc":
            return acc[ft]
        return data[ft][var]

    return Model("aifs", lat, lon, steps, get, regular=True)


def load_land(init):
    """陸海マスク（0.25度）。AIFSの入力（lsm）を使う"""
    import earthkit.regrid as ekr

    p = ROOT / "data" / f"aifs_input_{init}.npz"
    if not p.exists():
        return None
    lsm = np.load(p)["lsm"][0]
    return ekr.interpolate(lsm, {"grid": "N320"}, {"grid": (0.25, 0.25)}), np.linspace(90, -90, 721), np.arange(0, 360, 0.25)


def load_all(init):
    run = ROOT / "runs" / init
    out = {}
    for key, loader in [("wrf", lambda: load_wrf(run)), ("aifs", lambda: load_aifs(run, init)),
                        ("aifs_off", lambda: load_ecmwf("aifs_off", init)), ("ifs", lambda: load_ecmwf("ifs", init))]:
        try:
            m = loader()
        except Exception as e:  # 一部のモデルが欠けても他は使う
            print(f"[警告] {key} の読込に失敗: {e}", flush=True)
            m = None
        if m is not None:
            out[key] = m
    return out


def interp_to(src, dst, ft, var):
    """src モデル（規則格子）の場を、dst モデルの格子点へ補間する"""
    from scipy.interpolate import RegularGridInterpolator

    la, f = src.lat, src.field(ft, var)
    if la[0] > la[-1]:
        la, f = la[::-1], f[::-1]
    fn = RegularGridInterpolator((la, src.lon), f, bounds_error=False, fill_value=np.nan)
    if dst.regular:
        gl, go = np.meshgrid(dst.lat, dst.lon, indexing="ij")
    else:
        gl, go = dst.lat, dst.lon
    return fn(np.stack([gl.ravel(), go.ravel()], axis=-1)).reshape(gl.shape)
