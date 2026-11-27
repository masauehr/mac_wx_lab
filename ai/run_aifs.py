"""AIFS Single v1.0 を Mac で最小実行するスクリプト（Phase 1）。

ECMWF オープンデータから初期値を取得し、anemoi-inference で予報する。
使い方: .venv/bin/python ai/run_aifs.py --lead-time 24   # CPU・fp32（既定）
"""
import argparse
import datetime
import resource
import time
from collections import defaultdict
from pathlib import Path

import os
import sys

# mps 未対応の演算（scatter_reduce）をCPUへ逃がす。torch を import する前に設定
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
sys.path.insert(0, str(Path(__file__).parent))
import flash_attn_stub  # noqa: E402

flash_attn_stub.install()  # anemoi を import する前に差し込む

import contextlib

import torch

torch.set_grad_enabled(os.environ.get("AIFS_GRAD", "0") == "1")  # 推論のみ。勾配グラフでメモリが膨らむのを防ぐ

# torch 2.4 は mps の autocast 未対応のため、mps では何もしない（float32で実行）
_orig_autocast = torch.autocast
torch.autocast = lambda device_type, *a, **k: (
    contextlib.nullcontext() if device_type == "mps" else _orig_autocast(device_type, *a, **k)
)

import earthkit.data as ekd
import earthkit.regrid as ekr
import numpy as np
from anemoi.inference.runners.simple import SimpleRunner
from ecmwf.opendata import Client as OpendataClient

PARAM_SFC = ["10u", "10v", "2d", "2t", "msl", "skt", "sp", "tcw", "lsm", "z", "slor", "sdor"]
PARAM_SOIL = ["vsw", "sot"]
PARAM_PL = ["gh", "t", "u", "v", "w", "q"]
LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]
SOIL_LEVELS = [1, 2]
LEVELS_PL = LEVELS
OUT_FIELDS = ["2t", "msl", "10u", "10v", "tp"]  # 保存する出力変数


def get_open_data(date, param, levtype, levelist=()):
    """初期値（t-6h と t）を取得して N320 格子へ補間する"""
    fields = defaultdict(list)
    for d in [date - datetime.timedelta(hours=6), date]:
        data = ekd.from_source("ecmwf-open-data", date=d, param=param, levtype=levtype, levelist=list(levelist))
        for f in data:
            # オープンデータは経度 -180〜180 のため 0〜360 へずらす
            values = np.roll(f.to_numpy(), -f.shape[1] // 2, axis=1)
            values = ekr.interpolate(values, {"grid": (0.25, 0.25)}, {"grid": "N320"})
            name = f"{f.metadata('param')}_{f.metadata('levelist')}" if levelist else f.metadata("param")
            fields[name].append(values)
    return {k: np.stack(v) for k, v in fields.items()}


def patch_mps_indexing():
    """mps では roll＋インデックス代入で次ステップの入力が壊れる（2ステップ目以降が平滑化する）。
    入力テンソルの組み立てだけCPUで行い、結果をデバイスへ戻す。"""
    from anemoi.inference.runner import Runner

    def wrap(fn):
        def f(self, tensor, *args):
            args = [x.cpu() if torch.is_tensor(x) else x for x in args]
            dev = self.device
            self.device = "cpu"  # 関数内の .to(self.device) もCPUにする
            try:
                out = fn(self, tensor.cpu(), *args)
            finally:
                self.device = dev
            return out.to(dev)
        return f

    for name in ("copy_prognostic_fields_to_input_tensor", "add_dynamic_forcings_to_input_tensor",
                 "add_boundary_forcings_to_input_tensor"):
        setattr(Runner, name, wrap(getattr(Runner, name)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cpu", choices=["cpu", "mps"],
                    help="既定cpu。mpsは torch 2.4 で結果が実行ごとに変わり不安定（docs/phase1_log.md）")
    ap.add_argument("--precision", default="32", help="32 等（未指定はチェックポイント既定=16）")
    ap.add_argument("--lead-time", type=int, default=12)
    ap.add_argument("--out", default="outputs/aifs")
    a = ap.parse_args()

    t0 = time.time()
    date = datetime.datetime.strptime(os.environ["AIFS_DATE"], "%Y%m%d%H") if os.environ.get("AIFS_DATE") else OpendataClient().latest()
    print("初期時刻:", date)
    cache = Path("data") / f"aifs_input_{date:%Y%m%d%H}.npz"
    if cache.exists():  # 取得済みの初期値を再利用（取得に約4分かかるため）
        fields = dict(np.load(cache))
    else:
        fields = {}
        fields.update(get_open_data(date, PARAM_SFC, "sfc"))
        mapping = {"sot_1": "stl1", "sot_2": "stl2", "vsw_1": "swvl1", "vsw_2": "swvl2"}
        for k, v in get_open_data(date, PARAM_SOIL, "sol", SOIL_LEVELS).items():
            fields[mapping[k]] = v
        fields.update(get_open_data(date, PARAM_PL, "pl", LEVELS))
        for lv in LEVELS:  # 等圧面高度 → ジオポテンシャル
            fields[f"z_{lv}"] = fields.pop(f"gh_{lv}") * 9.80665
        cache.parent.mkdir(exist_ok=True)
        np.savez(cache, **fields)
    t1 = time.time()
    print(f"データ取得・補間: {t1 - t0:.1f}s")

    if os.environ.get("AIFS_NOPATCH") != "1":
        patch_mps_indexing()
    runner = SimpleRunner({"huggingface": "ecmwf/aifs-single-1.0"}, device=a.device, **({"precision": a.precision} if a.precision else {}))
    t2 = time.time()
    print(f"モデル読込: {t2 - t1:.1f}s")

    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    import earthkit.regrid as _ekr

    full_names = ["msl", "sp", "2t", "10u", "10v", "tp", "tcc"] + [f"{v}_{lv}" for lv in LEVELS_PL for v in ("z", "t", "u", "v", "w", "q")]

    def save_full(state, step):
        """天気図用に、必要な全変数を 0.25度（東アジア広域）へ補間して保存する（float32）"""
        lat = np.linspace(90, -90, 721); lon = np.arange(0, 360, 0.25)
        ii = np.where((lat >= -10) & (lat <= 80))[0]; jj = np.where((lon >= 50) & (lon <= 190))[0]
        arrs = {}
        for k in full_names:
            if k in state["fields"]:
                r = _ekr.interpolate(state["fields"][k], {"grid": "N320"}, {"grid": (0.25, 0.25)})
                arrs[k] = r[np.ix_(ii, jj)].astype("float32")
        np.savez(out / f"aifsfull_{date:%Y%m%d%H}_{step:%Y%m%d%H}.npz", lat=lat[ii], lon=lon[jj], **arrs)

    for state in runner.run(input_state=dict(date=date, fields=fields), lead_time=a.lead_time):
        step = state["date"]
        print("ステップ:", step, f"経過 {time.time() - t2:.1f}s")
        save_full(state, step)
        np.savez_compressed(
            out / f"aifs_{date:%Y%m%d%H}_{step:%Y%m%d%H}.npz",
            lat=state["latitudes"], lon=state["longitudes"],
            **{k: state["fields"][k] for k in OUT_FIELDS if k in state["fields"]},
        )
    t3 = time.time()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**3  # macOSはバイト単位
    print(f"推論 {a.lead_time}h: {t3 - t2:.1f}s / 最大メモリ {peak:.1f}GB / device={a.device}")


if __name__ == "__main__":
    main()
