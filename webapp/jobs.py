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


def wrf_out_path(init):
    return ROOT / "runs" / init / "wrf" / "wrfout"


def done_flags(init):
    """各ステージが既に済んでいるか（済みならスキップ）"""
    r = ROOT / "runs" / init
    return {
        "refs": (ROOT / "data" / f"ref_ifs_{init}.grib2").exists() and (ROOT / "data" / f"ref_aifs-single_{init}.grib2").exists(),
        "aifs": len(list((r / "aifs").glob("aifs_*.npz"))) >= 4,
        "wrf": wrf_out_path(init).exists(),
    }


def make_case(init):
    """テンプレートから、その初期時刻のWRF実験ディレクトリを作る"""
    t0 = datetime.strptime(init, "%Y%m%d%H")
    t1 = t0 + timedelta(hours=24)
    case = ROOT / "physics" / "cases" / f"okinawa_{init}"
    case.mkdir(parents=True, exist_ok=True)
    sub = {"@START_WPS@": t0.strftime("%Y-%m-%d_%H:00:00"), "@END_WPS@": t1.strftime("%Y-%m-%d_%H:00:00"),
           "@SY@": str(t0.year), "@SM@": str(t0.month), "@SD@": str(t0.day), "@SH@": str(t0.hour),
           "@EY@": str(t1.year), "@EM@": str(t1.month), "@ED@": str(t1.day), "@EH@": str(t1.hour)}
    for name in ("namelist.wps", "namelist.input"):
        s = (ROOT / "physics" / "cases" / "template" / name).read_text()
        for k, v in sub.items():
            s = s.replace(k, v)
        (case / name).write_text(s)
    return case


class Job:
    def __init__(self, init, models):
        self.init, self.models = init, models
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
    init, models = job.init, job.models
    flags = done_flags(init)
    plan = [("refs", "参照データ取得（IFS・公式AIFS）")]
    if "wrf" in models:
        plan.append(("gfs", "GFS初期値の取得（約35分・130MB×9）"))
    if "aifs" in models:
        plan.append(("aifs", "AIFS予報（CPU・約4分＋初期値取得）"))
    if "wrf" in models:
        plan.append(("wrf", "WRF（WPS→real→wrf・約10分）"))
    plan.append(("render", "作図・検証"))
    job.stages = [{"key": k, "name": n, "status": "waiting"} for k, n in plan]
    st = {s["key"]: s for s in job.stages}
    job.status = "running"
    try:
        for key, _ in plan:
            if job.cancel:
                raise RuntimeError("キャンセルされました")
            s = st[key]
            skip = (key in flags and flags[key]) or (key == "gfs" and flags["wrf"])
            if skip:
                s["status"] = "skipped"
                job.logline(f"--- {s['name']}: 済みのためスキップ")
                continue
            s["status"] = "running"
            job.logline(f"--- {s['name']}")
            if key == "refs":
                _run(job, [PY, "compare/fetch_reference.py", init])
            elif key == "gfs":
                _run(job, [PY, "physics/fetch_gfs.py", init, "24", "3"])
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
                _run(job, [PY, "webapp/render.py", init])
            s["status"] = "done"
        job.status = "done"
    except Exception as e:  # noqa: BLE001
        for s in job.stages:
            if s["status"] == "running":
                s["status"] = "error"
        job.logline(f"エラー: {e}")
        job.status = "cancelled" if job.cancel else "error"
    job.ended = time.time()


def start(init, models):
    global JOB
    if not INIT_RE.match(init):
        raise ValueError("初期時刻の形式が不正です（YYYYMMDDHH）")
    models = [m for m in models if m in ("aifs", "wrf")]
    if not models:
        raise ValueError("実行するモデルを選んでください")
    with _lock:
        if JOB is not None and JOB.status in ("queued", "running"):
            raise RuntimeError("別の計算が実行中です")
        LOGS.mkdir(parents=True, exist_ok=True)
        (LOGS / f"{init}.log").write_text("")
        JOB = Job(init, models)
        threading.Thread(target=_pipeline, args=(JOB,), daemon=True).start()
    return JOB.to_dict()


def render_only(init):
    return start_render(init)


def start_render(init):
    global JOB
    if not INIT_RE.match(init):
        raise ValueError("初期時刻の形式が不正です")
    with _lock:
        if JOB is not None and JOB.status in ("queued", "running"):
            raise RuntimeError("別の計算が実行中です")
        LOGS.mkdir(parents=True, exist_ok=True)
        (LOGS / f"{init}.log").write_text("")
        job = Job(init, [])
        JOB = job

        def run():
            job.stages = [{"key": "render", "name": "作図・検証", "status": "running"}]
            job.status = "running"
            try:
                _run(job, [PY, "webapp/render.py", init])
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
