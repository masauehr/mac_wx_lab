"""WRF 実験ケース: 設定（JSON）から WPS/WRF の設定ファイルを作り、地形を編集し、実行する。

設定の例（対照実験の既定は DEFAULT_CFG）:
{
  "id": "nocu", "label": "対流パラメタなし", "run_hours": 24, "history_interval": 60,
  "domains": [ {"dx": 9000, "n_we": 121, "n_sn": 101, "center": [26.0, 127.5]},             # d01（親）
               {"dx": 3000, "n_we": 151, "n_sn": 151, "center": [26.4, 127.9]} ],           # d02（ネスト。格子間隔は親の 1/3 か 1/5）
  "physics": {"cu_physics": 0, "qfx_scale": 0.0},                                           # namelist の上書き（領域ごとのリストも可）
  "terrain": [ {"type": "remove_land", "bbox": [127.6, 128.4, 26.0, 26.9]} ]                # 地形・陸海の編集（geogrid の出力を編集）
}
使い方: python physics/wrfcase.py run <設定JSON> <初期時刻YYYYMMDDHH>   /   estimate <設定JSON>   /   check <設定JSON>
"""
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PHYS = ROOT / "physics"

DEFAULT_CFG = {
    "id": "ctl", "label": "対照（9km）", "run_hours": 24, "history_interval": 60,
    "domains": [{"dx": 9000, "n_we": 121, "n_sn": 101, "center": [26.0, 127.5]}],
    "physics": {}, "terrain": [],
}

PRESETS = [
    {"id": "ctl", "label": "対照（9km・沖縄周辺）", "cfg": {"label": "対照（9km）"}},
    {"id": "nest3", "label": "3kmネスト（沖縄本島周辺 約450km四方）", "cfg": {"label": "3kmネスト", "domains": [
        {"dx": 9000, "n_we": 121, "n_sn": 101, "center": [26.0, 127.5]}, {"dx": 3000, "n_we": 151, "n_sn": 151, "center": [26.4, 127.9]}]}},
    {"id": "nest1", "label": "1kmネスト（9→3→1km・沖縄本島周辺・6時間）", "cfg": {"label": "1kmネスト", "run_hours": 6, "domains": [
        {"dx": 9000, "n_we": 121, "n_sn": 101, "center": [26.0, 127.5]}, {"dx": 3000, "n_we": 151, "n_sn": 151, "center": [26.4, 127.9]},
        {"dx": 1000, "n_we": 181, "n_sn": 181, "center": [26.4, 127.9]}]}},
    {"id": "nocu", "label": "対流パラメタリゼーションなし（9km）", "cfg": {"label": "積雲なし", "physics": {"cu_physics": 0}}},
    {"id": "noevap", "label": "地表蒸発なし（改造版WRF）", "cfg": {"label": "蒸発なし", "physics": {"qfx_scale": 0.0}}},
    {"id": "noflux", "label": "地表フラックスなし（顕熱・潜熱とも。標準WRF）", "cfg": {"label": "フラックスなし", "physics": {"isfflx": 0}}},
    {"id": "flat", "label": "沖縄本島の地形を平坦化（標高0m）", "cfg": {"label": "地形平坦化", "terrain": [{"type": "set", "value": 0, "bbox": [127.6, 128.4, 26.0, 26.95]}]}},
    {"id": "noisl", "label": "沖縄本島を海にする", "cfg": {"label": "島なし", "terrain": [{"type": "remove_land", "bbox": [127.6, 128.4, 26.0, 26.95]}]}},
    {"id": "mtn2", "label": "沖縄本島に標高1000mの山を追加", "cfg": {"label": "山を追加", "terrain": [{"type": "add_hill", "lat": 26.5, "lon": 128.0, "height": 1000, "radius_km": 12}]}},
]

# 物理設定の選択肢（namelist 名: (表示名, 領域ごとか, {値: 説明})）。範囲外の値は拒否する
PHYS_OPTS = {
    "mp_physics": ("雲物理", True, {0: "なし", 1: "Kessler", 2: "Lin", 3: "WSM3", 4: "WSM5", 6: "WSM6（既定）", 8: "Thompson", 10: "Morrison", 14: "WDM5", 16: "WDM6"}),
    "cu_physics": ("積雲パラメタリゼーション", True, {0: "なし（陽に解像）", 1: "Kain-Fritsch（既定）", 2: "Betts-Miller-Janjic", 3: "Grell-Freitas", 5: "Grell-3", 6: "Tiedtke", 14: "新SAS", 16: "新Tiedtke"}),
    "bl_pbl_physics": ("境界層", True, {0: "なし（LES）", 1: "YSU（既定）", 2: "MYJ", 5: "MYNN2.5", 7: "ACM2", 8: "BouLac", 11: "Shin-Hong"}),
    "sf_sfclay_physics": ("接地層", True, {0: "なし", 1: "改良MM5（既定）", 2: "Eta（MYJ用）", 5: "MYNN"}),
    "sf_surface_physics": ("地表（陸面）", True, {0: "なし", 1: "熱拡散", 2: "Noah（既定）", 4: "Noah-MP"}),
    "ra_lw_physics": ("長波放射", True, {0: "なし", 1: "RRTM", 4: "RRTMG（既定）", 5: "Goddard"}),
    "ra_sw_physics": ("短波放射", True, {0: "なし", 1: "Dudhia", 4: "RRTMG（既定）", 5: "Goddard"}),
    "isfflx": ("地表フラックス（0=顕熱・潜熱を全て無効）", False, {0: "無効", 1: "有効（既定）"}),
    "diff_opt": ("拡散", True, {1: "水平拡散（既定）", 2: "3次元（TKE）"}),
    "km_opt": ("乱流混合", True, {2: "1.5次TKE", 4: "2D Smagorinsky（既定）"}),
    "radt": ("放射の呼び出し間隔[分]", True, None), "cudt": ("積雲の呼び出し間隔[分]", True, None),
    "qfx_scale": ("地表蒸発量（水蒸気フラックス）の係数 0〜1【改造版WRF】", True, None),
    "hfx_scale": ("地表顕熱フラックスの係数 0〜1【改造版WRF】", True, None),
    "e_vert": ("鉛直層数", False, None), "p_top_requested": ("上端気圧[Pa]", False, None),
}
PATCHED_KEYS = ("qfx_scale", "hfx_scale")

# 実測（2026-11-27・M5 Pro・6並列）: 121×101 の親領域 24時間（1600ステップ）= 417秒 → 1格子点・1ステップあたり約21マイクロ秒
US_PER_PT_STEP = 21.0


# ---------- 設定の検査と領域の配置 ----------
def load_cfg(path_or_dict):
    cfg = json.loads(Path(path_or_dict).read_text()) if not isinstance(path_or_dict, dict) else dict(path_or_dict)
    out = json.loads(json.dumps(DEFAULT_CFG))
    out.update({k: v for k, v in cfg.items() if v is not None})
    return out


def layout(cfg):
    """各領域の配置を計算する（ランベルト図法。d01の中心が基準点）。[{dx, n_we, n_sn, ratio, parent, ips, jps, x_w, y_s, dt}, ...]"""
    import pyproj
    doms = cfg["domains"]
    if not 1 <= len(doms) <= 3:
        raise ValueError("領域は1〜3つです（親＋ネスト2つまで）")
    lat0, lon0 = doms[0]["center"]
    proj = pyproj.Proj(proj="lcc", lat_1=lat0, lat_2=lat0, lat_0=lat0, lon_0=lon0, R=6370000)
    out = []
    ratios = 1
    for i, d in enumerate(doms):
        dx, nw, ns = float(d["dx"]), int(d["n_we"]), int(d["n_sn"])
        if not (500 <= dx <= 60000):
            raise ValueError(f"d{i+1}: 格子間隔は 500〜60000m です")
        if nw < 25 or ns < 25 or nw > 1500 or ns > 1500:
            raise ValueError(f"d{i+1}: 格子数は 25〜1500 です")
        if i == 0:
            out.append(dict(dx=dx, n_we=nw, n_sn=ns, ratio=1, parent=0, ips=1, jps=1,
                            x_w=-(nw - 1) * dx / 2, y_s=-(ns - 1) * dx / 2))
            continue
        par = out[i - 1]
        ratio = int(round(par["dx"] / dx))
        if ratio not in (3, 5) or abs(par["dx"] / ratio - dx) > 1:
            raise ValueError(f"d{i+1}: 親の格子間隔の 1/3 か 1/5 にしてください（親 {par['dx']:g}m → {par['dx']/3:g} か {par['dx']/5:g}）")
        if (nw - 1) % ratio or (ns - 1) % ratio:
            raise ValueError(f"d{i+1}: 格子数は 親の倍率({ratio})の倍数＋1 にしてください（例: {ratio * 50 + 1}）")
        la, lo = d["center"]
        xc, yc = proj(lo, la)
        ips = int(round((xc - (nw - 1) * dx / 2 - par["x_w"]) / par["dx"])) + 1
        jps = int(round((yc - (ns - 1) * dx / 2 - par["y_s"]) / par["dx"])) + 1
        kw, ks = (nw - 1) // ratio, (ns - 1) // ratio
        if ips < 6 or jps < 6 or ips + kw > par["n_we"] - 1 - 5 or jps + ks > par["n_sn"] - 1 - 5:
            raise ValueError(f"d{i+1}: 親領域の端に近すぎます（親の境界から5格子以上内側に置いてください）")
        out.append(dict(dx=dx, n_we=nw, n_sn=ns, ratio=ratio, parent=i, ips=ips, jps=jps,
                        x_w=par["x_w"] + (ips - 1) * par["dx"], y_s=par["y_s"] + (jps - 1) * par["dx"]))
        ratios *= ratio
    # 時間刻み: 親は 6×dx[km] 秒を、ネストの倍率の積の倍数に丸める（子は親の 1/倍率）
    dt0 = max(ratios, int(round(6 * out[0]["dx"] / 1000.0 / ratios)) * ratios)
    dt = dt0
    for i, d in enumerate(out):
        dt = dt0 if i == 0 else dt / d["ratio"]
        d["dt"] = dt
    return out, (lat0, lon0)


def validate_physics(cfg, nd):
    """物理設定の上書きを検査して、領域ごとの値のリストにして返す"""
    res = {}
    for k, v in (cfg.get("physics") or {}).items():
        if k not in PHYS_OPTS:
            raise ValueError(f"未対応の設定です: {k}（使えるもの: {', '.join(PHYS_OPTS)}）")
        name, per_dom, allowed = PHYS_OPTS[k]
        vals = v if isinstance(v, list) else [v]
        if len(vals) == 1 and per_dom:
            vals = vals * nd
        if len(vals) != (nd if per_dom else 1):
            raise ValueError(f"{k}: 値の数が領域数（{nd}）と合いません")
        for x in vals:
            if allowed is not None and x not in allowed:
                raise ValueError(f"{k}={x} は選べません（{', '.join(f'{a}:{b}' for a, b in allowed.items())}）")
            if k in ("qfx_scale", "hfx_scale") and not (0 <= float(x) <= 1):
                raise ValueError(f"{k} は 0〜1 です")
        res[k] = vals
    # 組み合わせの検査（主なもの）
    pbl = res.get("bl_pbl_physics", [1] * nd)
    sfc = res.get("sf_sfclay_physics", [1] * nd)
    for p, s in zip(pbl, sfc):
        if p == 2 and s != 2:
            raise ValueError("境界層 MYJ(2) は接地層 Eta(2) と組み合わせてください")
        if p == 5 and s != 5:
            raise ValueError("境界層 MYNN2.5(5) は接地層 MYNN(5) と組み合わせてください")
        if p == 0 and s not in (0, 1):
            raise ValueError("境界層なし(LES) は接地層 0 か 1 と組み合わせてください")
    return res


def uses_patched(cfg):
    ph = cfg.get("physics") or {}
    for k in PATCHED_KEYS:
        v = ph.get(k)
        if v is not None and any(float(x) != 1.0 for x in (v if isinstance(v, list) else [v])):
            return True
    return False


def estimate(cfg):
    """計算時間の見積り（分）とメモリ（GB）。実測 21µs/格子点/ステップ（6並列）から。小さい領域は並列効率が落ちるので余裕を見る"""
    dom, _ = layout(cfg)
    hours = float(cfg.get("run_hours", 24))
    total = 0.0
    pts = 0
    for d in dom:
        n = (d["n_we"] - 1) * (d["n_sn"] - 1)
        steps = hours * 3600.0 / d["dt"]
        eff = 1.0 if n > 10000 else 1.3                      # 実測の領域（約12,000点）で 21µs。これより小さいと並列効率が落ちる
        total += n * steps * US_PER_PT_STEP * 1e-6 * eff
        pts += n
    mem = pts * 45 * 500 / 1e9 * 3 + 1.0
    return {"minutes": round(total / 60.0, 1), "memory_gb": round(mem, 1), "domains": [
        {"dx": d["dx"], "n_we": d["n_we"], "n_sn": d["n_sn"], "dt": d["dt"], "ips": d["ips"], "jps": d["jps"]} for d in dom]}


# ---------- namelist の生成 ----------
def _arr(vals, fmt=None):
    f = fmt or (lambda x: str(x))
    return ", ".join(f(v) for v in vals)


def make_namelists(cfg, init, case_dir):
    dom, (lat0, lon0) = layout(cfg)
    nd = len(dom)
    phys = validate_physics(cfg, nd)
    t0 = datetime.strptime(init, "%Y%m%d%H")
    hours = int(cfg.get("run_hours", 24))
    if not 1 <= hours <= 72:
        raise ValueError("予報時間は 1〜72 時間です")
    t1 = t0 + timedelta(hours=hours)
    geog = PHYS / ".." / "data" / "geog_dl"
    high = (ROOT / "data" / "geog_dl" / "WPS_GEOG").exists()
    res = ["lowres" if d["dx"] >= 8000 else "default" for d in dom]
    if "default" in res and not high:
        raise ValueError("格子間隔 8km 未満の領域には、高解像度の地形データが必要です（physics/fetch_geog_high.py）")
    wps = f"""&share
 wrf_core = 'ARW',
 max_dom = {nd},
 start_date = {_arr([t0.strftime("'%Y-%m-%d_%H:00:00'")] * nd)},
 end_date   = {_arr([t1.strftime("'%Y-%m-%d_%H:00:00'")] * nd)},
 interval_seconds = 10800,
 io_form_geogrid = 2,
/

&geogrid
 parent_id         = {_arr([d["parent"] if i else 1 for i, d in enumerate(dom)])},
 parent_grid_ratio = {_arr([d["ratio"] for d in dom])},
 i_parent_start    = {_arr([d["ips"] for d in dom])},
 j_parent_start    = {_arr([d["jps"] for d in dom])},
 e_we              = {_arr([d["n_we"] for d in dom])},
 e_sn              = {_arr([d["n_sn"] for d in dom])},
 geog_data_res     = {_arr([repr(r) for r in res])},
 dx = {dom[0]["dx"]:g},
 dy = {dom[0]["dx"]:g},
 map_proj = 'lambert',
 ref_lat   = {lat0},
 ref_lon   = {lon0},
 truelat1  = {lat0},
 truelat2  = {lat0},
 stand_lon = {lon0},
 geog_data_path = '../../../data/geog_dl/{"WPS_GEOG" if high else "WPS_GEOG_LOW_RES"}',
/

&ungrib
 out_format = 'WPS',
 prefix = 'GFS',
/

&metgrid
 fg_name = 'GFS',
 io_form_metgrid = 2,
/
"""
    dx_km = [d["dx"] / 1000.0 for d in dom]
    default = {
        "mp_physics": [6] * nd, "ra_lw_physics": [4] * nd, "ra_sw_physics": [4] * nd,
        "radt": [max(1, int(round(x))) for x in dx_km], "sf_sfclay_physics": [1] * nd, "sf_surface_physics": [2] * nd,
        "bl_pbl_physics": [1] * nd, "cu_physics": [1 if d["dx"] >= 8000 else 0 for d in dom],
        "diff_opt": [1] * nd, "km_opt": [4] * nd,
    }
    default["cudt"] = [5 if c else 0 for c in default["cu_physics"]]
    P = {**default, **{k: v for k, v in phys.items()}}
    if "cu_physics" in phys and "cudt" not in phys:
        P["cudt"] = [5 if c else 0 for c in P["cu_physics"]]
    e_vert = int(P.get("e_vert", [45])[0]) if "e_vert" in P else 45
    ptop = int(P.get("p_top_requested", [5000])[0]) if "p_top_requested" in P else 5000
    hi = int(cfg.get("history_interval", 60))
    st = lambda f: _arr([f(t0)] * nd)  # noqa: E731
    inp = f"""&time_control
 run_days = 0,
 run_hours = {hours},
 run_minutes = 0,
 run_seconds = 0,
 start_year = {st(lambda t: t.year)}, start_month = {st(lambda t: t.month)}, start_day = {st(lambda t: t.day)}, start_hour = {st(lambda t: t.hour)}, start_minute = {_arr([0]*nd)}, start_second = {_arr([0]*nd)},
 end_year   = {_arr([t1.year]*nd)}, end_month   = {_arr([t1.month]*nd)}, end_day   = {_arr([t1.day]*nd)}, end_hour   = {_arr([t1.hour]*nd)}, end_minute   = {_arr([0]*nd)}, end_second   = {_arr([0]*nd)},
 interval_seconds = 10800,
 input_from_file = {_arr([".true."] * nd)},
 history_interval = {_arr([hi] * nd)},
 frames_per_outfile = {_arr([1000] * nd)},
 restart = .false.,
 restart_interval = 7200,
 io_form_history = 2,
 io_form_restart = 2,
 io_form_input = 2,
 io_form_boundary = 2,
/

&domains
 time_step = {int(dom[0]["dt"])},
 time_step_fract_num = 0,
 time_step_fract_den = 1,
 max_dom = {nd},
 e_we = {_arr([d["n_we"] for d in dom])},
 e_sn = {_arr([d["n_sn"] for d in dom])},
 e_vert = {_arr([e_vert] * nd)},
 p_top_requested = {ptop},
 num_metgrid_levels = 34,
 num_metgrid_soil_levels = 4,
 dx = {_arr([f"{d['dx']:g}" for d in dom])},
 dy = {_arr([f"{d['dx']:g}" for d in dom])},
 grid_id = {_arr(list(range(1, nd + 1)))},
 parent_id = {_arr([0] + [d["parent"] for d in dom[1:]])},
 i_parent_start = {_arr([d["ips"] for d in dom])},
 j_parent_start = {_arr([d["jps"] for d in dom])},
 parent_grid_ratio = {_arr([d["ratio"] for d in dom])},
 parent_time_step_ratio = {_arr([d["ratio"] for d in dom])},
 feedback = 0,
 smooth_option = 0,
/

&physics
 mp_physics = {_arr(P["mp_physics"])},
 ra_lw_physics = {_arr(P["ra_lw_physics"])},
 ra_sw_physics = {_arr(P["ra_sw_physics"])},
 radt = {_arr(P["radt"])},
 sf_sfclay_physics = {_arr(P["sf_sfclay_physics"])},
 sf_surface_physics = {_arr(P["sf_surface_physics"])},
 bl_pbl_physics = {_arr(P["bl_pbl_physics"])},
 bldt = {_arr([0] * nd)},
 cu_physics = {_arr(P["cu_physics"])},
 cudt = {_arr(P["cudt"])},
 num_soil_layers = 4,
 sf_urban_physics = {_arr([0] * nd)},
{" isfflx = " + str(P["isfflx"][0]) + "," + chr(10) if "isfflx" in P else ""}{" qfx_scale = " + _arr(P["qfx_scale"]) + "," + chr(10) if "qfx_scale" in P else ""}{" hfx_scale = " + _arr(P["hfx_scale"]) + "," + chr(10) if "hfx_scale" in P else ""}/

&dynamics
 hybrid_opt = 2,
 w_damping = 1,
 diff_opt = {_arr(P["diff_opt"])},
 km_opt = {_arr(P["km_opt"])},
 diff_6th_opt = {_arr([0] * nd)},
 diff_6th_factor = {_arr([0.12] * nd)},
 base_temp = 290.,
 damp_opt = 3,
 zdamp = {_arr(["5000."] * nd)},
 dampcoef = {_arr([0.2] * nd)},
 khdif = {_arr([0] * nd)},
 kvdif = {_arr([0] * nd)},
 non_hydrostatic = {_arr([".true."] * nd)},
 moist_adv_opt = {_arr([1] * nd)},
 scalar_adv_opt = {_arr([1] * nd)},
 gwd_opt = 1,
/

&bdy_control
 spec_bdy_width = 5,
 specified = {_arr([".true."] + [".false."] * (nd - 1))},
 nested = {_arr([".false."] + [".true."] * (nd - 1))},
/

&namelist_quilt
 nio_tasks_per_group = 0,
 nio_groups = 1,
/
"""
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "namelist.wps").write_text(wps)
    (case_dir / "namelist.input").write_text(inp)
    return dom


# ---------- 地形・陸海の編集（geogrid の出力 geo_em.dNN.nc を編集する） ----------
def edit_terrain(geo_path, ops, dom_no):
    import netCDF4 as nc
    import numpy as np

    d = nc.Dataset(geo_path, "a")
    lat, lon = d["XLAT_M"][0], d["XLONG_M"][0]
    h = d["HGT_M"][0].astype(float)
    land = d["LANDMASK"][0].astype(float)
    notes = []
    for op in ops:
        doms = op.get("domains")
        if doms and dom_no not in doms:
            continue
        m = np.ones(h.shape, bool)
        if op.get("bbox"):
            lo0, lo1, la0, la1 = op["bbox"]
            m = (lon >= lo0) & (lon <= lo1) & (lat >= la0) & (lat <= la1)
        t = op["type"]
        if t == "scale":
            h = np.where(m, h * float(op["factor"]), h)
        elif t == "set":
            h = np.where(m, float(op["value"]), h)
        elif t == "add_hill":
            r = np.hypot((lon - float(op["lon"])) * np.cos(np.deg2rad(float(op["lat"]))), lat - float(op["lat"])) * 111.0
            h = h + float(op["height"]) * np.exp(-0.5 * (r / float(op["radius_km"])) ** 2)
        elif t == "remove_land":                              # 陸を海にする（地形0m・陸海マスク0・土地利用=水）
            sel = m & (land > 0.5)
            h = np.where(sel, 0.0, h)
            land = np.where(sel, 0.0, land)
            if "LU_INDEX" in d.variables:
                lu = d["LU_INDEX"][0].copy(); lu[sel] = 17                              # 土地利用カテゴリ17（水）
                d["LU_INDEX"][0] = lu
            if "LANDUSEF" in d.variables:
                lf = d["LANDUSEF"][0].copy(); lf[:, sel] = 0.0; lf[16, sel] = 1.0       # カテゴリ17（水）
                d["LANDUSEF"][0] = lf
            for v, cat in (("SOILCTOP", 13), ("SOILCBOT", 13)):
                if v in d.variables:
                    s = d[v][0].copy(); s[:, sel] = 0.0; s[cat, sel] = 1.0; d[v][0] = s    # 土壌カテゴリ14（水）
            for v in ("SCT_DOM", "SCB_DOM"):
                if v in d.variables:
                    s = d[v][0].copy(); s[sel] = 14; d[v][0] = s
            for v in ("GREENFRAC", "LAI12M", "ALBEDO12M"):
                pass
            notes.append(f"d{dom_no:02d}: 陸→海 {int(sel.sum())}点")
        else:
            raise ValueError(f"未対応の地形編集です: {t}")
    d["HGT_M"][0] = h.astype("float32")
    d["LANDMASK"][0] = land.astype("float32")
    d.close()
    return notes


# ---------- 実行 ----------
def _run(cmd, cwd, log, env=None, note=""):
    log(f"$ {' '.join(map(str, cmd))}  {note}")
    e = dict(os.environ)
    e.update(env or {})
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=e, text=True)
    tail = []
    for line in p.stdout:
        tail = (tail + [line.rstrip()])[-20:]
    if p.wait() != 0:
        raise RuntimeError(f"{cmd[0]} が失敗しました（終了コード {p.returncode}）:\n" + "\n".join(tail[-8:]))


def run(cfg, init, procs=6, log=print):
    """設定どおりにWRFを実行して、出力ファイルを runs/<初期時刻>/wrfx/<ID>/ にリンクする"""
    cid = cfg["id"]
    case = PHYS / "cases" / f"x_{init}_{cid}"
    patched = uses_patched(cfg)
    exe_root = PHYS / ("WRF_exp" if patched else "WRF")
    if patched and not (exe_root / "main" / "wrf.exe").exists():
        raise RuntimeError("改造版WRF（蒸発・顕熱の係数）がビルドされていません（physics/WRF_exp）")
    dom = make_namelists(cfg, init, case)
    nd = len(dom)
    log(f"設定を作成: 領域 {nd}、{'改造版' if patched else '標準'}WRF → {case}")
    env = {"PATH": f"{ROOT}/.conda/bin:" + os.environ["PATH"], "NETCDF": str(ROOT / ".conda")}
    wps = PHYS / "WPS"
    for n in ("geogrid.exe", "ungrib.exe", "metgrid.exe"):
        (case / n).unlink(missing_ok=True); (case / n).symlink_to(wps / n)
    for n in ("real.exe", "wrf.exe"):
        (case / n).unlink(missing_ok=True); (case / n).symlink_to(exe_root / "main" / n)
    for f in (exe_root / "run").iterdir():                    # 表（LANDUSE.TBL など）。namelistと実行ファイルは除く
        if f.is_file() and not f.name.startswith("namelist") and f.suffix != ".exe" and f.name not in ("README.namelist",):
            (case / f.name).unlink(missing_ok=True); (case / f.name).symlink_to(f.resolve())
    for sub, tbl in (("geogrid", "GEOGRID.TBL.ARW"), ("metgrid", "METGRID.TBL.ARW")):
        (case / sub).mkdir(exist_ok=True)
        shutil.copy(wps / sub / tbl, case / sub / tbl.replace(".ARW", ""))
    (case / "Vtable").unlink(missing_ok=True); (case / "Vtable").symlink_to(wps / "ungrib" / "Variable_Tables" / "Vtable.GFS")
    for f in list(case.glob("GFS:*")) + list(case.glob("met_em*")) + list(case.glob("GRIBFILE.*")) + list(case.glob("wrfout*")) + list(case.glob("wrfinput*")) + [case / "wrfbdy_d01"]:
        f.unlink(missing_ok=True)
    _run(["./geogrid.exe"], case, log, env, "地形・土地利用")
    notes = []
    if cfg.get("terrain"):
        for i in range(1, nd + 1):
            notes += edit_terrain(case / f"geo_em.d{i:02d}.nc", cfg["terrain"], i)
        log("地形・陸海を編集: " + (" / ".join(notes) if notes else "（変更なし）"))
    gfs = sorted((ROOT / "data" / "gfs" / init).glob("gfs.t*z.pgrb2.0p25.f*"))
    need = int(cfg.get("run_hours", 24)) // 3 + 1
    if len(gfs) < need:
        raise RuntimeError(f"GFS が足りません（{len(gfs)}/{need}ファイル）。physics/fetch_gfs.py {init} {cfg.get('run_hours', 24)} 3 で取得してください")
    _run([str(wps / "link_grib.csh")] + [str(g) for g in gfs[:need]], case, log, env)
    _run(["./ungrib.exe"], case, log, env, "GFSの変換")
    _run(["./metgrid.exe"], case, log, env, "格子へ補間")
    _run(["mpirun", "-np", "1", "./real.exe"], case, log, env, "初期値・境界値の作成")
    est = estimate(cfg)
    log(f"WRF を実行（見積り {est['minutes']} 分）")
    _run(["mpirun", "-np", str(procs), "./wrf.exe"], case, log, env, "予報計算")
    out = ROOT / "runs" / init / "wrfx" / cid
    out.mkdir(parents=True, exist_ok=True)
    for i in range(1, nd + 1):
        src = next(case.glob(f"wrfout_d{i:02d}_*"), None)
        if src is None:
            raise RuntimeError(f"wrfout_d{i:02d} がありません")
        dst = out / f"wrfout_d{i:02d}"
        dst.unlink(missing_ok=True); dst.symlink_to(src.resolve())
    (out / "case.json").write_text(json.dumps({"cfg": cfg, "patched": patched, "finished": datetime.now().isoformat(timespec="seconds"),
                                                "estimate_min": est["minutes"], "terrain_notes": notes}, ensure_ascii=False, indent=1))
    log(f"完了: {out}")
    return out


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, cfg = sys.argv[1], load_cfg(sys.argv[2])
    if cmd == "estimate":
        print(json.dumps(estimate(cfg), ensure_ascii=False, indent=1))
    elif cmd == "check":
        d, _ = layout(cfg); validate_physics(cfg, len(d)); print("OK", json.dumps(d, ensure_ascii=False))
    elif cmd == "run":
        run(cfg, sys.argv[3], log=lambda s: print(s, flush=True))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
