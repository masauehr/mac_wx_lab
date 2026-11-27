"""ローカルWebアプリのサーバー（標準ライブラリのみ・127.0.0.1専用）。

使い方: .venv/bin/python webapp/server.py [ポート]   → http://127.0.0.1:8795/
画面（web/）・図（output/）・API（計算の実行と状態）を配る。
"""
import json
import mimetypes
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
    _cand.update(t=time.time(), v={"latest": latest.strftime("%Y%m%d%H"), "list": out})
    return _cand["v"]


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
                return self._send(200, jobs.start(str(body.get("init", "")), list(body.get("models", []))))
            if path == "/api/render":
                return self._send(200, jobs.start_render(str(body.get("init", ""))))
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
