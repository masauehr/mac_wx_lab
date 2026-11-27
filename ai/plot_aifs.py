"""AIFS 出力（npz）の簡易プロット: 海面気圧・2m気温。使い方: python ai/plot_aifs.py <npz> <png>"""
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as tri
import numpy as np

d = np.load(sys.argv[1])
lon = np.where(d["lon"] > 180, d["lon"] - 360, d["lon"])
lat = d["lat"]
fig, axs = plt.subplots(2, 1, figsize=(11, 10))
for ax, key, scale, off, cmap, name in [
    (axs[0], "msl", 0.01, 0, "viridis", "MSLP [hPa]"),
    (axs[1], "2t", 1, -273.15, "RdBu_r", "2m temperature [C]"),
]:
    v = d[key] * scale + off
    ax.tricontourf(tri.Triangulation(lon, lat), v, levels=20, cmap=cmap)
    ax.set_title(name)
    print(key, "min/max", float(v.min()), float(v.max()))
plt.tight_layout()
plt.savefig(sys.argv[2], dpi=90)
