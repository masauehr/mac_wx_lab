"""天気図の背景地図（地形）。

- 国土地理院タイル（淡色地図 pale・色別標高図 relief・陰影起伏 hillshademap・標準地図 std）。mobile_app と同じ配信元。
  日本周辺だけ詳細（relief・hillshademap は日本の外はタイルなし）。タイルは data/tiles/ にキャッシュする
- 日本の外・タイルが無い所は、WPS_GEOG の標高（GMTED2010 5分≒9km）から作る陰影起伏＋標高の色（terrain）で補う
- 地図の投影（Lambert・正距円筒など）に合わせて、図の画素ごとに緯度経度→タイルの画素を引いて作る（再投影の遅さを避ける）

出典（必須）: 国土地理院（地理院タイル）。https://maps.gsi.go.jp/development/ichiran.html
"""
import io
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates

ROOT = Path(__file__).resolve().parent.parent
TILE_DIR = ROOT / "data" / "tiles"
TOPO_DIR = ROOT / "data" / "geog_dl" / "WPS_GEOG_LOW_RES" / "topo_gmted2010_5m"
URL = "https://cyberjapandata.gsi.go.jp/xyz/{kind}/{z}/{x}/{y}.png"
KINDS = {"pale": "淡色地図", "relief": "色別標高図", "hillshade": "陰影起伏", "std": "標準地図", "terrain": "標高（全球・簡易）", "none": "なし（線のみ）"}
GSI_NAME = {"pale": "pale", "relief": "relief", "hillshade": "hillshademap", "std": "std"}
MAXZ = {"pale": 14, "relief": 14, "hillshade": 14, "std": 14}
OCEAN = np.array([219, 233, 244], np.float32) / 255
_lock = threading.Lock()
_topo = {}
_cache = {}


# ---------- 標高（GMTED2010 5分）から作る簡易の地形 ----------
def load_topo():
    """標高 [m]（lat 昇順 -89.96〜、lon 0.04〜359.96）。WPSの形式（int16・ビッグエンディアン・タイル600＋縁3）"""
    if "e" in _topo:
        return _topo["lat"], _topo["lon"], _topo["e"]
    T, B = 600, 3
    e = np.zeros((4 * T, 8 * T), np.float32)           # xは8タイル・yは4タイル（端はパディング）
    for yi in range(0, 4 * T, T):
        for xi in range(0, 8 * T, T):
            f = TOPO_DIR / f"{xi + 1:05d}-{xi + T:05d}.{yi + 1:05d}-{yi + T:05d}"   # 「x範囲.y範囲」
            e[yi:yi + T, xi:xi + T] = np.fromfile(f, dtype=">i2").reshape(T + 2 * B, T + 2 * B)[B:-B, B:-B]
    e = e[:2160, :4320]
    ny, nx = e.shape
    lat = -89.9583 + 0.0833333 * np.arange(ny)
    lon = 0.04166667 + 0.0833333 * np.arange(nx)
    _topo.update(lat=lat, lon=lon, e=e)
    return lat, lon, e


def terrain_global():
    """全球の地形画像（RGB 0-1）。標高の色＋陰影起伏。キャッシュ"""
    if "rgb" in _topo:
        return _topo["lat"], _topo["lon"], _topo["rgb"]
    lat, lon, e = load_topo()
    dx = 111320.0 * np.cos(np.deg2rad(lat))[:, None] * np.deg2rad(0.0833333) / np.deg2rad(1)
    dy = 111320.0 * 0.0833333
    zx = np.gradient(e, axis=1) / dx
    zy = np.gradient(e, axis=0) / dy
    ve = 6.0                                                   # 縦方向の強調
    slope = np.arctan(ve * np.hypot(zx, zy))
    aspect = np.arctan2(zy, -zx)
    az, alt = np.deg2rad(315), np.deg2rad(40)
    hs = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    hs = np.clip(hs, 0, 1)
    # 標高の色（低地=黄緑 → 山=茶 → 高山=白灰）
    stops = [(0, (0.78, 0.86, 0.66)), (300, (0.86, 0.88, 0.68)), (1000, (0.90, 0.82, 0.62)),
             (2000, (0.80, 0.68, 0.52)), (4000, (0.72, 0.62, 0.56)), (6000, (0.95, 0.95, 0.95))]
    xs = [s[0] for s in stops]
    col = np.stack([np.interp(e, xs, [s[1][i] for s in stops]) for i in range(3)], axis=-1)
    rgb = col * (0.55 + 0.6 * hs[..., None])
    rgb = np.where((e <= 0)[..., None], OCEAN, rgb)
    rgb = np.clip(rgb, 0, 1).astype(np.float32)
    _topo["rgb"] = rgb
    return lat, lon, rgb


# ---------- 国土地理院タイル ----------
def _merc_px(lon, lat, z):
    n = 256 * 2 ** z
    x = (np.asarray(lon, float) + 180.0) / 360.0 * n
    lat = np.clip(lat, -85.05, 85.05)
    y = (1 - np.arcsinh(np.tan(np.deg2rad(lat))) / np.pi) / 2 * n
    return x, y


def _fetch_tile(kind, z, x, y):
    """1枚取得してキャッシュ。無い（404）は印を残して再取得しない。戻り値は RGBA の配列か None"""
    p = TILE_DIR / GSI_NAME[kind] / str(z) / str(x) / f"{y}.png"
    miss = p.with_suffix(".none")
    if p.exists():
        return np.asarray(Image.open(p).convert("RGBA"))
    if miss.exists():
        return None
    import urllib.error
    import urllib.request

    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            data = urllib.request.urlopen(URL.format(kind=GSI_NAME[kind], z=z, x=x, y=y), timeout=30).read()
            p.write_bytes(data)
            return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                miss.write_bytes(b"")
                return None
        except Exception:  # noqa: BLE001
            pass
    return None


def gsi_mosaic(kind, lon0, lon1, lat0, lat1, z):
    """範囲を覆うタイルを並べた RGBA の配列と、その左上の画素座標 (px0, py0) を返す"""
    x0, y1 = _merc_px(lon0, lat0, z)
    x1, y0 = _merc_px(lon1, lat1, z)
    tx0, tx1 = int(x0 // 256), int(x1 // 256)
    ty0, ty1 = int(y0 // 256), int(y1 // 256)
    n = 2 ** z
    tiles = [(tx, ty) for ty in range(max(0, ty0), min(n - 1, ty1) + 1) for tx in range(max(0, tx0), min(n - 1, tx1) + 1)]
    with ThreadPoolExecutor(8) as ex:
        res = dict(zip(tiles, ex.map(lambda t: _fetch_tile(kind, z, t[0], t[1]), tiles)))
    W, H = (tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256
    mos = np.zeros((H, W, 4), np.uint8)
    for (tx, ty), a in res.items():
        if a is not None:
            mos[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = a
    return mos, tx0 * 256, ty0 * 256


def _auto_zoom(lonspan, width_px, kind):
    z = math.ceil(math.log2(max(1.0, width_px * 1.4 * 360.0 / (256 * max(lonspan, 0.01)))))
    return int(min(max(z, 3), MAXZ.get(kind, 12)))


def sample(kind, lon, lat, width_px):
    """画素ごとの経度・緯度（同形の2次元配列）に対する背景色 (H, W, 3) を返す。範囲外・タイルなしは地形（terrain）で補う"""
    H, W = lon.shape
    la_t, lo_t, rgb = terrain_global()
    lo = np.mod(lon, 360.0)
    iy = (lat - la_t[0]) / 0.0833333
    ix = (lo - lo_t[0]) / 0.0833333
    base = np.stack([map_coordinates(rgb[..., c], [iy, ix], order=1, mode="nearest") for c in range(3)], axis=-1)
    if kind == "none":
        return np.ones((H, W, 3), np.float32)
    if kind == "terrain":
        return base
    lon180 = np.where(lon > 180, lon - 360, lon)
    z = _auto_zoom(float(np.ptp(lon180)), width_px, kind)
    mos, px0, py0 = gsi_mosaic(kind, float(lon180.min()), float(lon180.max()), float(lat.min()), float(lat.max()), z)
    px, py = _merc_px(lon180, lat, z)
    cy, cx = py - py0 - 0.5, px - px0 - 0.5
    ch = [map_coordinates(mos[..., c].astype(np.float32) / 255, [cy, cx], order=1, mode="constant", cval=0) for c in range(4)]
    a = ch[3][..., None]
    tile = np.stack(ch[:3], axis=-1)
    if kind == "pale":      # 淡色地図は日本の外が空白。陸の地形をうっすら出す
        base = 0.35 + 0.65 * base
    elif kind == "std":
        base = np.ones_like(base)
    return tile * a + base * (1 - a)


def for_axes(kind, proj, extent, region_key, W=946, H=716):
    """地図の枠（投影座標の範囲 extent）に合わせた背景画像 (H, W, 3) を返す。結果はメモリにキャッシュ"""
    key = (kind, region_key, round(extent[0]), round(extent[2]), W, H)
    if key in _cache:
        return _cache[key]
    import cartopy.crs as ccrs

    xs = np.linspace(extent[0], extent[1], W)
    ys = np.linspace(extent[3], extent[2], H)           # 上が北
    X, Y = np.meshgrid(xs, ys)
    ll = ccrs.PlateCarree().transform_points(proj, X, Y)
    with _lock:
        img = sample(kind, ll[..., 0], ll[..., 1], W)
    _cache[key] = img
    return img


def credit(kind):
    """図に入れる出典・加工の表示（国土地理院コンテンツ利用規約: 出典の記載と、加工した旨の記載）"""
    return {
        "pale": "出典: 国土地理院（淡色地図の地理院タイルを加工して作成。VMAP0・海上保安庁等を含む）／標高: GMTED2010",
        "std": "出典: 国土地理院（標準地図の地理院タイルを加工して作成。VMAP0・海上保安庁等を含む）",
        "relief": "出典: 国土地理院（色別標高図の地理院タイルを加工して作成。海域部は海上保安庁海洋情報部の資料による）／標高: GMTED2010",
        "hillshade": "出典: 国土地理院（陰影起伏図の地理院タイルを加工して作成）／標高: GMTED2010",
        "terrain": "標高: GMTED2010（USGS/NGA。NCAR WPS_GEOG 経由）を加工して作成",
    }.get(kind, "")
