"""公開サイト（GitHub Pages・閲覧専用）を作り、gh-pages ブランチへ公開する。

公開するのは作図済みのPNGと索引だけ（元データ・モデルの重み・計算は含まない）。
使い方:
  python webapp/publish_static.py --build-only         # webapp/_site を作るだけ（公開しない）
  python webapp/publish_static.py [--keep 3] [初期時刻...]   # 作って gh-pages へ強制pushする
gh-pages はデプロイ専用ブランチなので、毎回履歴を作り直して push する（画像で履歴が膨らむのを防ぐ）。
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
WEB, OUT, SITE = ROOT / "webapp" / "web", ROOT / "webapp" / "output", ROOT / "webapp" / "_site"


def quantize(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(src).convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=256)
    im.save(dst, optimize=True)


BIG_REGIONS = ("japan", "okinawa", "asia")      # 広域の地図。感度実験（領域が狭い）は、容量のため WRF領域と各実験の領域だけ公開する


def build(inits, maps=("pale",), gallery=True):
    if SITE.exists():
        shutil.rmtree(SITE)  # 生成物（_site）だけを消す
    SITE.mkdir(parents=True)
    for f in WEB.iterdir():
        if f.is_file() and not f.name.startswith(("custom.", "wrf.")):       # 任意作図・WRF実験設定はローカル専用（公開しない）
            shutil.copy(f, SITE / f.name)
    shutil.copytree(WEB / "board", SITE / "board")                 # 画用紙版は公開サイトにも載せる
    (SITE / "config.js").write_text("window.STATIC_SITE = true;\n")
    for f in ("LICENSE", "NOTICE.md"):
        shutil.copy(ROOT / f, SITE / f)
    (SITE / ".nojekyll").write_text("")
    n = 0
    for init in inits:
        (SITE / "data" / init).mkdir(parents=True, exist_ok=True)
        idx = json.loads((OUT / init / "index.json").read_text())
        idx["maps"] = [m for m in idx.get("maps", []) if m["id"] in maps]      # 公開する背景地図だけ
        for r in BIG_REGIONS:                                                  # 感度実験は広域の図を載せない（索引からも外す）
            if r in idx["regions"]:
                idx["regions"][r]["models"] = {m: v for m, v in idx["regions"][r]["models"].items() if not m.startswith("x_")}
        (SITE / "data" / init / "index.json").write_text(json.dumps(idx, ensure_ascii=False))
        for sub in list(maps) + ["common"]:
            for p in (OUT / init / sub).rglob("*.png"):
                rel = p.relative_to(OUT / init)
                if sub != "common" and rel.parts[1] in BIG_REGIONS and rel.parts[2].startswith("x_"):
                    continue
                dst = SITE / "data" / init / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(p, dst)                                           # 作図時に256色へ減色済み
                n += 1
    (SITE / "data").mkdir(exist_ok=True)
    (SITE / "data" / "runs.json").write_text(json.dumps({"runs": inits}))
    # 任意作図のギャラリー（ローカルで「公開に載せる」を選んだ図のみ。国土地理院タイルの背景は載せない）
    items = []
    pub = ROOT / "webapp" / "published_custom.json"
    if gallery and pub.exists():
        for it in json.loads(pub.read_text()):
            src = ROOT / "webapp" / "custom_out" / f"{it['key']}.png"
            meta = ROOT / "webapp" / "custom_out" / f"{it['key']}.json"
            if not src.exists() or not meta.exists():
                continue
            prm = json.loads(meta.read_text())["params"]
            if (prm.get("kind") or "map") == "map" and prm.get("basemap", "pale") in ("pale", "relief", "hillshade", "std"):
                print(f"  ギャラリー: {it['key']} は国土地理院タイルの背景のため除外")
                continue
            quantize(src, SITE / "data" / "gallery" / f"{it['key']}.png")
            items.append({"file": f"data/gallery/{it['key']}.png", "caption": it["caption"], "note": it.get("note", "")})
    (SITE / "data" / "gallery.json").write_text(json.dumps(items, ensure_ascii=False))
    print(f"ギャラリー: {len(items)}枚")
    size = sum(f.stat().st_size for f in SITE.rglob("*") if f.is_file()) / 1e6
    print(f"サイト生成: {len(inits)}初期時刻・PNG {n}枚・{size:.0f}MB → {SITE}")
    return size


def deploy():
    remote = subprocess.check_output(["git", "-C", str(ROOT), "remote", "get-url", "origin"], text=True).strip()
    run = lambda *a: subprocess.run(["git", "-C", str(SITE), *a], check=True)  # noqa: E731
    run("init", "-q", "-b", "gh-pages")
    run("add", "-A")
    run("commit", "-q", "-m", "公開サイトを更新（作図済みの画像と索引のみ）")
    run("push", "-q", "--force", remote, "gh-pages")
    print("gh-pages へ公開しました")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inits", nargs="*")
    ap.add_argument("--keep", type=int, default=3, help="指定がないとき、新しい順に何件載せるか")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--no-gallery", action="store_true", help="任意作図のギャラリーを載せない")
    ap.add_argument("--maps", default="terrain", help="公開する背景地図（カンマ区切り）。容量に注意")
    ap.add_argument("--allow-gsi", action="store_true",
                    help="国土地理院タイルの背景を公開する。規約上の可否が未確認のため、既定では公開しない（NOTICE.md 参照）")
    a = ap.parse_args()
    inits = a.inits or sorted([p.parent.name for p in OUT.glob("*/index.json")], reverse=True)[: a.keep]
    if not inits:
        sys.exit("作図済みの結果がありません")
    maps = tuple(a.maps.split(","))
    gsi = [m for m in maps if m in ("pale", "relief", "hillshade", "std")]
    if gsi and not a.allow_gsi:
        sys.exit(f"国土地理院タイルの背景（{gsi}）は公開しません（加工画像の再配布の可否が未確認。NOTICE.md）。--allow-gsi で上書きできます")
    size = build(inits, maps, gallery=not a.no_gallery)
    if size > 900:
        sys.exit("サイトが900MBを超えました（Pagesは1GBが目安）。--keep を減らしてください")
    if not a.build_only:
        deploy()


if __name__ == "__main__":
    main()
