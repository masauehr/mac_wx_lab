"""WRF理想実験(em_quarter_ss)の出力を図にする。使い方: python physics/plot_qss.py <wrfout> <png>"""
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import netCDF4 as nc

d = nc.Dataset(sys.argv[1])
n = len(d["Times"])
fig, axs = plt.subplots(1, 3, figsize=(15, 4.5))
for ax, t in zip(axs[:2], [n // 2, n - 1]):
    w = d["W"][t, 20]  # 中層(約5km)の鉛直流
    m = ax.pcolormesh(w, cmap="RdBu_r", vmin=-30, vmax=30)
    ax.set_title(f"W [m/s] mid-level t={t * 10}min")
    fig.colorbar(m, ax=ax)
m = axs[2].pcolormesh(d["RAINNC"][-1], cmap="Blues")
axs[2].set_title("Accumulated rain [mm] t=120min")
fig.colorbar(m, ax=axs[2])
plt.tight_layout()
plt.savefig(sys.argv[2], dpi=90)
