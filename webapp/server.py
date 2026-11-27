"""ローカルWebアプリのサーバー（標準ライブラリのみ・127.0.0.1専用）。

使い方: .venv/bin/python webapp/server.py [ポート]   → http://127.0.0.1:8795/
画面（web/）・図（output/）・API（計算の実行と状態）を配る。
"""
import json
import mimetypes
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs  # noqa: E402

HERE = Path(__file__).resolve().parent
WEB, OUT = HERE / "web", HERE / "output"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8795
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

_cand = {"t": 0, "v": None}


def candidates():
    """計算できる初期時刻の候補（ECMWFオープンデータの最新から6時間ごとに約4日分）。10分キャッシュ"""
    if time.time() - _cand["t"] < 600 and _cand["v"]:
        return _cand["v"]
    latest = None
    try:
        import truststore
        from ecmwf.opendata import Client
        truststore.inject_into_ssl()
        latest = Client(source="ecmwf", model="ifs", resol="0p25").latest(type="fc", step=24, param="msl")
    except Exception as e:  # noqa: BLE001
        print("[警告] 最新の初期時刻を取得できません:", e, flush=True)
    if latest is None:
        latest = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0) - timedelta(hours=18)
        latest = latest.replace(hour=latest.hour // 6 * 6)
    out = [(latest - timedelta(hours=6 * i)).strftime("%Y%m%d%H") for i in range(16)]
    _cand.update(t=time.time(), v={"latest": latest.strftime("%Y%m%d%H"), "list": out, "min": jobs.MIN_DATE})
    return _cand["v"]


PUB = {"proc": None, "started": None, "inits": []}
PUB_LOG = HERE / "logs" / "publish.log"


def publish_info():
    """公開の確認画面用: 初期時刻ごとの公開予定の容量（背景は標高の陰影）・ギャラリーの件数"""
    import os
    sys.path.insert(0, str(HERE))
    import customchart
    runs = []
    for d in sorted(OUT.glob("*/index.json"), reverse=True):
        init = d.parent.name
        size = 0
        for sub in ("terrain", "common"):
            for root, _, files in os.walk(OUT / init / sub):
                parts = Path(root).relative_to(OUT / init).parts
                if sub != "common" and len(parts) >= 3 and parts[1] in ("japan", "okinawa", "asia") and parts[2].startswith("x_"):
                    continue                                           # 感度実験の広域の図は公開しない（publish_static.py と同じ）
                for f in files:
                    size += os.path.getsize(os.path.join(root, f))
        idx = json.loads(d.read_text())
        has_terrain = (OUT / init / "terrain").exists()
        exps = [e["label"] for e in idx.get("experiments", [])]
        # 感度実験は広域の図を載せない分、小さくなる（概算: 実験モデルの広域を除く）
        runs.append({"init": init, "mb": round(size / 1e6), "terrain": has_terrain, "experiments": exps})
    pub = [x for x in customchart.load_published() if (HERE / "custom_out" / f"{x['key']}.png").exists()]
    return {"runs": runs, "gallery": len(pub), "limit_mb": 900}


def publish_start(inits):
    if PUB["proc"] is not None and PUB["proc"].poll() is None:
        raise RuntimeError("公開の処理が実行中です")
    inits = [i for i in inits if jobs.INIT_RE.match(i) and (OUT / i / "index.json").exists()]
    if not inits:
        raise ValueError("公開する初期時刻を選んでください")
    PUB_LOG.parent.mkdir(exist_ok=True)
    lf = open(PUB_LOG, "w")
    PUB["proc"] = subprocess.Popen([jobs.PY, "webapp/publish_static.py", *inits, "--maps", "terrain"], cwd=HERE.parent,
                                   stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
    PUB["started"], PUB["inits"] = time.time(), inits


def publish_status():
    p = PUB["proc"]
    st = "none" if p is None else ("running" if p.poll() is None else ("done" if p.returncode == 0 else "error"))
    log = PUB_LOG.read_text(errors="replace").splitlines()[-30:] if PUB_LOG.exists() else []
    return {"status": st, "log": log, "inits": PUB["inits"]}


def wrf_options():
    """WRF実験の設定画面用: 物理設定の選択肢、プリセット、地形データ・改造版の有無"""
    sys.path.insert(0, str(HERE.parent / "physics"))
    import wrfcase
    root = HERE.parent
    return {
        "phys": {k: {"label": v[0], "per_domain": v[1], "choices": ({str(a): b for a, b in v[2].items()} if v[2] else None)} for k, v in wrfcase.PHYS_OPTS.items()},
        "presets": wrfcase.PRESETS, "default": wrfcase.DEFAULT_CFG,
        "high_geog": (root / "data" / "geog_dl" / "WPS_GEOG").exists(),
        "patched_build": (root / "physics" / "WRF_exp" / "main" / "wrf.exe").exists(),
    }


def runs_list():
    res = []
    for d in sorted(OUT.glob("*/index.json"), reverse=True):
        try:
            res.append(json.loads(d.read_text())["init"])
        except Exception:  # noqa: BLE001
            pass
    return res


class H(BaseHTTPRequestHandler):
    server_version = "MacWxLab/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if ctype.startswith("application/json") else "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        # DNSリバインディング対策: Host が自分宛てのときだけ受け付ける
        return self.headers.get("Host", "") in ALLOWED_HOSTS

    def _file(self, base, rel):
        p = (base / rel).resolve()
        if p.is_dir() and (p / "index.html").is_file():          # /board/ などディレクトリは index.html を返す
            p = p / "index.html"
        if base.resolve() not in p.parents and p != base.resolve():
            return self._send(403, {"error": "forbidden"})
        if not p.is_file():
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, p.read_bytes(), ctype, {"Cache-Control": "public, max-age=3600"} if p.suffix == ".png" else None)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "bad host"})
        path = unquote(urlparse(self.path).path)
        if path == "/api/runs":
            return self._send(200, {"runs": runs_list()})
        if path == "/api/candidates":
            return self._send(200, candidates())
        if path.startswith("/api/index/"):
            init = path.rsplit("/", 1)[1]
            if not jobs.INIT_RE.match(init) or not (OUT / init / "index.json").exists():
                return self._send(404, {"error": "未作図の初期時刻です"})
            return self._send(200, (OUT / init / "index.json").read_text())
        if path == "/api/job":
            return self._send(200, jobs.status() or {"status": "none"})
        if path == "/api/flags":
            init = self.path.split("init=")[-1] if "init=" in self.path else ""
            return self._send(200, jobs.done_flags(init) if jobs.INIT_RE.match(init) else {})
        if path == "/api/publish_info":
            return self._send(200, publish_info())
        if path == "/api/publish_status":
            return self._send(200, publish_status())
        if path == "/api/gallery":
            import customchart
            return self._send(200, [{"file": f"/custom_out/{x['key']}.png", "caption": x["caption"], "note": x.get("note", "")}
                                    for x in customchart.load_published() if (HERE / "custom_out" / f"{x['key']}.png").exists()])
        if path == "/api/wrf_options":
            return self._send(200, wrf_options())
        if path == "/api/custom_options":
            import customchart
            return self._send(200, customchart.options())
        if path == "/api/custom_history":
            import customchart
            return self._send(200, {"items": customchart.history()})
        if path.startswith("/custom_out/"):
            return self._file(HERE / "custom_out", path[len("/custom_out/"):])
        if path.startswith("/img/"):
            return self._file(OUT, path[len("/img/"):])
        if path in ("/", "/index.html"):
            return self._file(WEB, "index.html")
        return self._file(WEB, path.lstrip("/"))

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, {"error": "bad host"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "JSON不正"})
        path = urlparse(self.path).path
        try:
            if path == "/api/run":
                return self._send(200, jobs.start(str(body.get("init", "")), list(body.get("models", [])), list(body.get("maps", [])), body.get("wrf_cfg")))
            if path == "/api/render":
                return self._send(200, jobs.start_render(str(body.get("init", "")), list(body.get("maps", []))))
            if path == "/api/custom_publish":
                import customchart
                try:
                    customchart.set_published(str(body.get("key", "")), bool(body.get("on")))
                    return self._send(200, {"ok": True})
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
            if path == "/api/publish":
                try:
                    publish_start(list(body.get("inits", [])))
                    return self._send(200, publish_status())
                except (ValueError, RuntimeError) as e:
                    return self._send(409, {"error": str(e)})
            if path == "/api/wrf_estimate":
                try:
                    sys.path.insert(0, str(HERE.parent / "physics"))
                    import wrfcase
                    cfg = wrfcase.load_cfg(body)
                    dom, _ = wrfcase.layout(cfg)
                    wrfcase.validate_physics(cfg, len(dom))
                    return self._send(200, dict(wrfcase.estimate(cfg), patched=wrfcase.uses_patched(cfg),
                                                needs_high_geog=any(float(d["dx"]) < 8000 for d in cfg["domains"])))
                except (ValueError, KeyError, TypeError) as e:
                    return self._send(400, {"error": str(e)})
            if path == "/api/custom":
                import customchart
                try:
                    out, note = customchart.render(body)
                except (ValueError, KeyError) as e:
                    return self._send(400, {"error": str(e) if isinstance(e, ValueError) else f"指定が足りません: {e}"})
                except Exception as e:  # noqa: BLE001  描画の失敗は画面にメッセージで返す
                    return self._send(500, {"error": f"作図に失敗しました: {type(e).__name__}: {e}"})
                return self._send(200, {"url": f"/custom_out/{out.name}", "note": note})
            if path == "/api/cancel":
                return self._send(200, {"cancelled": jobs.cancel()})
        except (ValueError, RuntimeError) as e:
            return self._send(409, {"error": str(e)})
        self._send(404, {"error": "not found"})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=candidates, daemon=True).start()
    print(f"http://127.0.0.1:{PORT}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
