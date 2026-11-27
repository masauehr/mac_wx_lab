"""WRF実事例（沖縄周辺9km）の出力を図にする。使い方: python physics/plot_okinawa.py <wrfout> <png>"""
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import netCDF4 as nc
import numpy as np

d = nc.Dataset(sys.argv[1])
lon, lat = d["XLONG"][0], d["XLAT"][0]
n = len(d["Times"])
times = ["".join(c.decode() for c in d["Times"][i]) for i in range(n)]
for i in range(0, n, 6):
    p = d["PSFC"][i]; t2 = d["T2"][i]; h = d["HGT"][0]
    slp = p * np.exp(9.80665 * h / (287.05 * t2)) / 100  # 簡易海面更正
    print(times[i], f"SLP min {slp.min():.1f} max {slp.max():.1f} hPa | T2 {t2.min()-273.15:.1f}~{t2.max()-273.15:.1f}℃ | "
          f"10m風 最大 {np.hypot(d['U10'][i], d['V10'][i]).max():.1f} m/s | 積算降水 最大 {(d['RAINC'][i] + d['RAINNC'][i]).max():.1f} mm")
fig, axs = plt.subplots(1, 3, figsize=(16, 4.8))
i = n - 1
rain = d["RAINC"][i] + d["RAINNC"][i]
for ax, v, ttl, cm in [
    (axs[0], d["T2"][i] - 273.15, "T2 [C]", "RdYlBu_r"),
    (axs[1], np.hypot(d["U10"][i], d["V10"][i]), "10m wind [m/s]", "viridis"),
    (axs[2], rain, "24h rain [mm]", "Blues"),
]:
    m = ax.pcolormesh(lon, lat, v, cmap=cm); ax.contour(lon, lat, d["LANDMASK"][0], [0.5], colors="k", linewidths=0.6)
    ax.set_title(f"{ttl}  {times[i]}"); fig.colorbar(m, ax=ax)
plt.tight_layout(); plt.savefig(sys.argv[2], dpi=90)
