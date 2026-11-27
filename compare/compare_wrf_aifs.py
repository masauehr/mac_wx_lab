"""WRF（沖縄周辺9km）・AIFS（手元）・公式AIFS を、IFS(HRES)を参照にして WRF の格子上で比べる。
使い方: python compare/compare_wrf_aifs.py <初期時刻YYYYMMDDHH> <wrfout> <AIFS出力ディレクトリ>
出力: 標準出力にMarkdown表、outputs/compare_*.png に図。"""
import sys
from pathlib import Path

import earthkit.data as ekd
import earthkit.regrid as ekr
import matplotlib
import netCDF4 as nc
import numpy as np
from scipy.interpolate import RegularGridInterpolator

matplotlib.use("Agg")
import matplotlib.pyplot as plt

init, wrfout, aifs_dir = sys.argv[1], sys.argv[2], Path(sys.argv[3])
STEPS = [6, 12, 18, 24]
EDGE = 10  # WRFの境界緩和帯（5格子）より広く除外

# --- WRF ---
w = nc.Dataset(wrfout)
lat, lon = w["XLAT"][0], w["XLONG"][0]
cosa, sina = w["COSALPHA"][0], w["SINALPHA"][0]
hgt, land = w["HGT"][0], w["LANDMASK"][0]
pts = np.stack([lat.ravel(), lon.ravel()], axis=-1)


def wrf_fields(h):
    p, t2 = w["PSFC"][h], w["T2"][h]
    u, v = w["U10"][h], w["V10"][h]
    return {
        "msl": p * np.exp(9.80665 * hgt / (287.05 * t2)) / 100,           # hPa（簡易海面更正）
        "2t": t2 - 273.15,
        "wspd": np.hypot(u, v),
        "tp": w["RAINC"][h] + w["RAINNC"][h],                              # mm（積算）
    }


# --- 0.25度の規則格子のモデル（IFS・公式AIFS）を WRF 格子へ補間 ---
def to_wrf(field2d, la, lo):
    """la: 降順の緯度、lo: 0〜360の経度。WRF格子点へ双線形補間"""
    if la[0] > la[-1]:
        la, field2d = la[::-1], field2d[::-1]
    f = RegularGridInterpolator((la, lo), field2d, bounds_error=False, fill_value=np.nan)
    return f(pts).reshape(lat.shape)


def load_grib(path):
    out = {}
    for f in ekd.from_source("file", str(path)):
        key = (f.metadata("param"), int(f.metadata("step")))
        ll = f.to_latlon()
        la, lo = ll["lat"][:, 0], np.mod(ll["lon"][0], 360)
        order = np.argsort(lo)
        out[key] = (f.to_numpy()[:, order], la, lo[order])
    return out


def from_grib(path, tp_scale=1000):
    """tp_scale: 降水の単位換算。IFSは m（×1000でmm）、公式AIFSは既にmm（×1）"""
    raw = load_grib(path)
    res = {s: {} for s in [0] + STEPS}
    for (p, s), (a, la, lo) in raw.items():
        v = to_wrf(a, la, lo)
        name = {"msl": "msl", "2t": "2t", "tp": "tp"}.get(p)
        if p == "msl": v = v / 100
        if p == "2t": v = v - 273.15
        if p == "tp": v = v * tp_scale
        if name:
            res[s][name] = v
        if p in ("10u", "10v"):
            res[s][p] = v
    for s in res:
        if "10u" in res[s]:
            res[s]["wspd"] = np.hypot(res[s]["10u"], res[s]["10v"])
    return res


ifs = from_grib(f"data/ref_ifs_{init}.grib2")
aifs_off = from_grib(f"data/ref_aifs-single_{init}.grib2", tp_scale=1)

# --- 手元のAIFS（N320格子 → 0.25度 → WRF格子） ---
aifs = {s: {} for s in STEPS}
cum = 0
for i, s in enumerate(STEPS):
    d = np.load(sorted(aifs_dir.glob(f"aifs_{init}_*.npz"))[i])
    def reg(x):
        r = ekr.interpolate(x, {"grid": "N320"}, {"grid": (0.25, 0.25)})
        return to_wrf(r, np.linspace(90, -90, 721), np.arange(0, 360, 0.25))
    aifs[s]["msl"] = reg(d["msl"]) / 100
    aifs[s]["2t"] = reg(d["2t"]) - 273.15
    aifs[s]["wspd"] = np.hypot(reg(d["10u"]), reg(d["10v"]))
    cum = cum + reg(d["tp"]) * 1000   # 出力は予報ステップ（6時間）ごとの降水 [m] → 積算[mm]
    aifs[s]["tp"] = cum.copy()

models = {"IFS": ifs, "AIFS(手元)": aifs, "AIFS(公式)": aifs_off, "WRF": {s: wrf_fields(s) for s in STEPS}}
mask = np.zeros(lat.shape, bool); mask[EDGE:-EDGE, EDGE:-EDGE] = True


def six_hour_rain(m, s):
    """積算降水から直前6時間分を取り出す"""
    return m[s]["tp"] - (m[s - 6]["tp"] if s > 6 else 0)


def stats(a, b):
    ok = mask & np.isfinite(a) & np.isfinite(b)
    d = (a - b)[ok]
    return d.mean(), np.sqrt((d ** 2).mean()), np.corrcoef(a[ok], b[ok])[0, 1]


print(f"初期時刻 {init}（UTC）／参照 = IFS(HRES)／WRF格子の内部（境界{EDGE}点除外）。各セルは「偏差 / RMSE / 相関」\n")
labels = {"msl": "海面気圧 [hPa]", "2t": "2m気温 [℃]", "wspd": "10m風速 [m/s]", "tp": "6時間降水 [mm]"}
for var, lab in labels.items():
    print(f"### {lab}\n\n| 予報時間 | AIFS(手元) | AIFS(公式) | WRF |\n|---|---|---|---|")
    for s in STEPS:
        row = []
        for name in ["AIFS(手元)", "AIFS(公式)", "WRF"]:
            m = models[name]
            a, b = (six_hour_rain(m, s), six_hour_rain(ifs, s)) if var == "tp" else (m[s][var], ifs[s][var])
            row.append("%+.2f / %.2f / %.2f" % stats(a, b))
        print(f"| +{s}h | " + " | ".join(row) + " |")
    print()
# 降水量の全体像（領域平均・最大）
print("### 領域内の降水（24時間積算）\n\n| モデル | 領域平均 [mm] | 最大 [mm] |\n|---|---|---|")
for name in ["IFS", "AIFS(手元)", "AIFS(公式)", "WRF"]:
    a = models[name][24]["tp"][mask]
    print(f"| {name} | {np.nanmean(a):.2f} | {np.nanmax(a):.1f} |")

# --- 図: +24h と 降水(24h) ---
Path("outputs").mkdir(exist_ok=True)
fig, axs = plt.subplots(3, 4, figsize=(20, 13))
for j, name in enumerate(["IFS", "AIFS(手元)", "AIFS(公式)", "WRF"]):
    m = models[name]
    for i, (var, cm, kw) in enumerate([("msl", "viridis", {}), ("wspd", "YlGnBu", {"vmin": 0, "vmax": 25}),
                                        ("tp", "Blues", {"vmin": 0, "vmax": 60})]):
        v = m[24][var]
        im = axs[i, j].pcolormesh(lon, lat, v, cmap=cm, **kw)
        axs[i, j].contour(lon, lat, land, [0.5], colors="k", linewidths=0.5)
        axs[i, j].set_title(f"{name} {'msl' if var=='msl' else 'wind' if var=='wspd' else '24h rain'} +24h")
        fig.colorbar(im, ax=axs[i, j])
plt.tight_layout(); plt.savefig(f"outputs/compare_{init}.png", dpi=70)
