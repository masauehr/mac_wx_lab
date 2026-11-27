"""計算の実行管理: 参照データ取得 → GFS取得 → AIFS予報 → WRF → 作図 を順に走らせる。

同時に走らせるのは1ジョブだけ（AIFSがメモリ約55GBを使うため）。
状態は画面からポーリングで取得する。ログは webapp/logs/<初期時刻>.log。
"""
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "webapp" / "logs"
PY = str(ROOT / ".venv" / "bin" / "python")
INIT_RE = re.compile(r"^\d{10}$")

_lock = threading.Lock()
JOB = None  # 現在または直近のジョブ


# 取得できる期間（2026-11-27 に確認）。ECMWFは本家が直近約3日、AWSミラーがIFS 2024-02-29〜・AIFS公式 2025-02-26〜。GFSは2021-03-23〜
MIN_DATE = {"ifs": "20240229", "aifs_off": "20250226", "gfs": "20210323", "aifs_local": "20241112"}   # aifs_local: AIFSの入力に土壌の変数（vsw・sot）が要るため


def check_range(init, models):
    """初期時刻が取得できる期間か検査して、だめなら ValueError（画面に理由を出す）"""
    if init[8:] not in ("00", "06", "12", "18"):
        raise ValueError("初期時刻は 00・06・12・18 UTC のいずれかです")
    from datetime import datetime as _dt
    if _dt.strptime(init, "%Y%m%d%H") > _dt.utcnow():
        raise ValueError("未来の初期時刻は指定できません")
    ymd = init[:8]
    if "aifs" in models and ymd < MIN_DATE["aifs_local"]:
        raise ValueError(f"AIFS（手元）の初期値には土壌の変数（IFSオープンデータ）が要り、{MIN_DATE['aifs_local']} 以降のみです")
    if "wrf" in models and ymd < MIN_DATE["gfs"]:
        raise ValueError(f"WRF の初期値（GFS）は {MIN_DATE['gfs']} 以降のみです")


def wrf_out_path(init):
    return ROOT / "runs" / init / "wrf" / "wrfout"


def done_flags(init):
    """各ステージが既に済んでいるか（済みならスキップ）"""
    r = ROOT / "runs" / init
    return {
        "refs": all((ROOT / "data" / f"ref_{m}_{init}{sfx}.grib2").exists()
                    for m in ("ifs", "aifs-single") if init[:8] >= MIN_DATE["ifs" if m == "ifs" else "aifs_off"]
                    for sfx in ("", "_pl")) and ((ROOT / "data" / f"ref_ifs_{init}_sfc3.grib2").exists())   # IFSの3時間間隔（RR3用）
                    or init[:8] < MIN_DATE["ifs"],
        "aifs": len(list((r / "aifs").glob("aifsfull_*.npz"))) >= 4,           # 天気図用の全変数まで保存済みか
        "wrf": wrf_out_path(init).exists(),
    }


# 3kmのネスト領域（親は9km・121×101）。中心と大きさ（格子数）。計算時間は 2026-11-27 の見積り（実測で更新する）
NESTS = {
    "s": {"label": "3kmネスト（沖縄本島周辺 約450km四方）", "n": 151, "center": (26.4, 127.9), "est_min": 50},
    "l": {"label": "3kmネスト（沖縄〜先島 約720km四方）", "n": 241, "center": (26.0, 127.5), "est_min": 110},
}


def nest_params(nest):
    """ネスト領域の、親領域での位置（i_parent_start・j_parent_start）を、ランベルト図法の座標から求める"""
    import pyproj
    n = NESTS[nest]["n"]
    k = (n - 1) // 3                                          # 子領域が覆う親の格子数
    lat, lon = NESTS[nest]["center"]
    proj = pyproj.Proj(proj="lcc", lat_1=26, lat_2=26, lat_0=26, lon_0=127.5, R=6370000)   # WRFは半径6370kmの球
    x, y = proj(lon, lat)
    ips = int(round(x / 9000.0 + 60 - k / 2.0)) + 1           # 親は 120×100 の質量点、中心は (60, 50)
    jps = int(round(y / 9000.0 + 50 - k / 2.0)) + 1
    if not (6 <= ips and ips + k <= 115 and 6 <= jps and jps + k <= 95):
        raise ValueError("ネスト領域が親領域の境界に近すぎます")
    return ips, jps, n


def make_case(init, nest=None):
    """テンプレートから、その初期時刻のWRF実験ディレクトリを作る。nest='s'|'l' なら 3km ネスト付き"""
    t0 = datetime.strptime(init, "%Y%m%d%H")
    t1 = t0 + timedelta(hours=24)
    case = ROOT / "physics" / "cases" / (f"okinawa_{init}" + (f"_nest{nest}" if nest else ""))
    case.mkdir(parents=True, exist_ok=True)
    sub = {"@START_WPS@": t0.strftime("%Y-%m-%d_%H:00:00"), "@END_WPS@": t1.strftime("%Y-%m-%d_%H:00:00"),
           "@SY@": str(t0.year), "@SM@": str(t0.month), "@SD@": str(t0.day), "@SH@": str(t0.hour),
           "@EY@": str(t1.year), "@EM@": str(t1.month), "@ED@": str(t1.day), "@EH@": str(t1.hour)}
    if nest:
        ips, jps, n = nest_params(nest)
        sub.update({"@IPS@": str(ips), "@JPS@": str(jps), "@NE@": str(n)})
    tdir = ROOT / "physics" / "cases" / ("template_nest" if nest else "template")
    for name in ("namelist.wps", "namelist.input"):
        s = (tdir / name).read_text()
        for k, v in sub.items():
            s = s.replace(k, v)
        (case / name).write_text(s)
    return case


class Job:
    def __init__(self, init, models, maps=None, wrf_cfg=None):
        self.init, self.models = init, models
        self.wrf_cfg = wrf_cfg
        self.maps = maps or ["pale"]
        self.status = "queued"           # queued / running / done / error / cancelled
        self.stages = []                 # [{name, key, status}]
        self.log = LOGS / f"{init}.log"
        self.started = time.time()
        self.ended = None
        self.proc = None
        self.bg = None
        self.cancel = False

    def logline(self, s):
        with open(self.log, "a") as f:
            f.write(s.rstrip("\n") + "\n")

    def to_dict(self):
        tail = []
        if self.log.exists():
            tail = self.log.read_text(errors="replace").splitlines()[-80:]
        return {"init": self.init, "models": self.models, "status": self.status, "stages": self.stages,
                "log": tail, "started": self.started, "ended": self.ended}


def _run(job, cmd, env=None, key=None):
    """1コマンドを実行してログへ。失敗したら例外"""
    e = dict(os.environ)
    e.update(env or {})
    job.logline(f"$ {' '.join(cmd)}")
    with open(job.log, "a") as lf:
        job.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=lf, stderr=subprocess.STDOUT, env=e, start_new_session=True)
        rc = job.proc.wait()
    job.proc = None
    if job.cancel:
        raise RuntimeError("キャンセルされました")
    if rc != 0:
        raise RuntimeError(f"終了コード {rc}")


def _pipeline(job):
    init, models, xcfg = job.init, job.models, job.wrf_cfg
    flags = done_flags(init)
    flags["wrfx"] = bool(xcfg) and (ROOT / "runs" / init / "wrfx" / xcfg["id"] / "case.json").exists()
    need_hours = max([24] + ([int(xcfg.get("run_hours", 24))] if xcfg else []))
    plan = [("refs", "参照データ取得（IFS・公式AIFS）")]
    if "wrf" in models or xcfg:
        plan.append(("gfs", f"GFS初期値の取得（約35分・130MB×{need_hours // 3 + 1}）"))
    if xcfg and _needs_high_geog(xcfg):
        plan.append(("geog", "地形データ（高解像度・約2.8GB）の取得"))
    if "aifs" in models:
        plan.append(("aifs", "AIFS予報（CPU・約4分＋初期値取得）"))
    if "wrf" in models:
        plan.append(("wrf", "WRF（WPS→real→wrf・約10分）"))
    if xcfg:
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "physics"))
        import wrfcase
        est = wrfcase.estimate(xcfg)
        plan.append(("wrfx", f"WRF実験「{xcfg.get('label', xcfg['id'])}」（見積り約{est['minutes']:.0f}分）"))
    plan.append(("render", "作図・検証"))
    job.stages = [{"key": k, "name": n, "status": "waiting"} for k, n in plan]
    st = {s["key"]: s for s in job.stages}
    job.status = "running"
    try:
        for key, _ in plan:
            if job.cancel:
                raise RuntimeError("キャンセルされました")
            s = st[key]
            skip = (key in flags and flags[key]) or (key == "gfs" and flags["wrf"] and not xcfg) or \
                (key == "gfs" and len(list((ROOT / "data" / "gfs" / init).glob("gfs.t*z.pgrb2.0p25.f*"))) >= need_hours // 3 + 1) or \
                (key == "geog" and (ROOT / "data" / "geog_dl" / "WPS_GEOG").exists())
            if skip:
                s["status"] = "skipped"
                job.logline(f"--- {s['name']}: 済みのためスキップ")
                continue
            s["status"] = "running"
            job.logline(f"--- {s['name']}")
            if key == "refs":
                if init[:8] < MIN_DATE["ifs"]:
                    job.logline("IFS・公式AIFSのオープンデータは 2024-02-29 以降のため、参照データは取得しません（比較はWRFのみ）")
                else:
                    _run(job, [PY, "compare/fetch_reference.py", init])
            elif key == "gfs":
                _run(job, [PY, "physics/fetch_gfs.py", init, str(need_hours), "3"])
            elif key == "geog":
                _run(job, [PY, "physics/fetch_geog_high.py"])
            elif key == "wrfx":
                cfgp = ROOT / "runs" / init / f"wrfx_{xcfg['id']}.json"
                cfgp.parent.mkdir(parents=True, exist_ok=True)
                import json as _json
                cfgp.write_text(_json.dumps(xcfg, ensure_ascii=False, indent=1))
                _run(job, [PY, "physics/wrfcase.py", "run", str(cfgp), init])
            elif key == "aifs":
                (ROOT / "runs" / init / "aifs").mkdir(parents=True, exist_ok=True)
                _run(job, [PY, "ai/run_aifs.py", "--lead-time", "24", "--out", f"runs/{init}/aifs"], env={"AIFS_DATE": init})
            elif key == "wrf":
                case = make_case(init)
                _run(job, ["bash", "physics/run_case.sh", f"physics/cases/{case.name}", init])
                outs = sorted(case.glob("wrfout_d01_*"))
                if not outs:
                    raise RuntimeError("wrfout が見つかりません")
                dst = wrf_out_path(init)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.is_symlink() or dst.exists():
                    dst.unlink()
                dst.symlink_to(outs[0].resolve())
            elif key == "render":
                _run(job, [PY, "webapp/render.py", init, "--maps", ",".join(job.maps)])
            s["status"] = "done"
        job.status = "done"
    except Exception as e:  # noqa: BLE001
        for s in job.stages:
            if s["status"] == "running":
                s["status"] = "error"
        job.logline(f"エラー: {e}")
        job.status = "cancelled" if job.cancel else "error"
    job.ended = time.time()


def _needs_high_geog(cfg):
    return any(float(d['dx']) < 8000 for d in cfg['domains'])


def start(init, models, maps=None, wrf_cfg=None):
    global JOB
    if not INIT_RE.match(init):
        raise ValueError("初期時刻の形式が不正です（YYYYMMDDHH）")
    models = [m for m in models if m in ("aifs", "wrf")]
    xcfg = None
    if wrf_cfg:
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "physics"))
        import wrfcase
        xcfg = wrfcase.load_cfg(wrf_cfg)
        if not re.match(r"^[A-Za-z0-9]{1,16}$", str(xcfg.get("id", ""))):
            raise ValueError("実験IDは英数字1〜16文字です（例: nocu）")
        if xcfg["id"] == "ctl":
            raise ValueError("実験ID ctl は対照実験用に予約されています")
        dom, _ = wrfcase.layout(xcfg)                      # 領域・物理設定を検査（不正なら ValueError）
        wrfcase.validate_physics(xcfg, len(dom))
    if not models and not xcfg:
        raise ValueError("実行するモデルを選んでください")
    check_range(init, models + (["wrf"] if xcfg else []))
    with _lock:
        if JOB is not None and JOB.status in ("queued", "running"):
            raise RuntimeError("別の計算が実行中です")
        LOGS.mkdir(parents=True, exist_ok=True)
        (LOGS / f"{init}.log").write_text("")
        maps = [m for m in (maps or ["pale"]) if m in ("pale", "relief", "hillshade", "std", "terrain")] or ["pale"]
        JOB = Job(init, models, maps, xcfg)
        threading.Thread(target=_pipeline, args=(JOB,), daemon=True).start()
    return JOB.to_dict()


def start_render(init, maps=None):
    global JOB
    if not INIT_RE.match(init):
        raise ValueError("初期時刻の形式が不正です")
    with _lock:
        if JOB is not None and JOB.status in ("queued", "running"):
            raise RuntimeError("別の計算が実行中です")
        LOGS.mkdir(parents=True, exist_ok=True)
        (LOGS / f"{init}.log").write_text("")
        job = Job(init, [], [m for m in (maps or ["pale"]) if m in ("pale", "relief", "hillshade", "std", "terrain")] or ["pale"])
        JOB = job

        def run():
            job.stages = [{"key": "render", "name": "作図・検証", "status": "running"}]
            job.status = "running"
            try:
                _run(job, [PY, "webapp/render.py", init, "--maps", ",".join(job.maps)])
                job.stages[0]["status"] = "done"
                job.status = "done"
            except Exception as e:  # noqa: BLE001
                job.stages[0]["status"] = "error"
                job.logline(f"エラー: {e}")
                job.status = "error"
            job.ended = time.time()

        threading.Thread(target=run, daemon=True).start()
    return job.to_dict()


def status():
    return JOB.to_dict() if JOB else None


def cancel():
    if JOB is None or JOB.status != "running":
        return False
    JOB.cancel = True
    if JOB.proc is not None:
        try:
            os.killpg(os.getpgid(JOB.proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    return True
