# Phase 1 作業ログ — AIFS Single v1.0 を Mac で動かす

2026-11-27。環境: Apple M5 Pro / 64GB / macOS。**結果: 動作・公式AIFSとの一致を確認（CPU・fp32）**。

## 結論

- **CPU（fp32）で正しく動く**。24h予報が約224秒（1ステップ約55秒）。公式AIFS（オープンデータ）と+24hで一致（海面気圧RMSE 0.42hPa・2m気温0.65K）
- **MPS（GPU）は torch 2.4 で結果が実行ごとに変わり不安定**。同一入力・同一コードで +6h の海面気圧RMSEが 26Pa / 330Pa / 690Pa とばらついた。さらに2ステップ目以降は平滑化し、メモリも膨らむ（24hで75GB超→OOM）。CPUのほうが速く（MPS約90〜150秒/ステップ）決定的
- 最大メモリは約55GB（64GBに収まるが余裕は少ない。他の重い作業は止める）

## 実行方法

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python "anemoi-inference[huggingface]==0.4.9" \
  anemoi-models==0.3.1 torch==2.4.0 earthkit-regrid==0.4.0 ecmwf-opendata matplotlib "numpy<2"
.venv/bin/python ai/run_aifs.py --lead-time 24                 # 最新初期時刻・CPU・fp32
AIFS_DATE=2026112618 .venv/bin/python ai/run_aifs.py --lead-time 24   # 初期時刻を指定
.venv/bin/python ai/compare_official.py 2026112618 24 outputs/aifs/aifs_2026112618_2026112718.npz
.venv/bin/python ai/plot_aifs.py <npz> <png>
```

重みは Hugging Face `ecmwf/aifs-single-1.0`（CC BY 4.0・約1.2GB）、初期値は ECMWF オープンデータ（CC BY 4.0）。初期値の取得・補間は初回約4分（`data/` にキャッシュ）。

## 公式AIFSとの比較（初期時刻 2026-11-26 18UTC・CPU fp32）

| 予報時間 | 海面気圧 RMSE | 2m気温 RMSE |
|---|---|---|
| +6h | 0.26 hPa | 0.47 K |
| +12h | 0.31 hPa | 0.55 K |
| +18h | 0.38 hPa | 0.60 K |
| +24h | 0.42 hPa | 0.65 K |

出力範囲（+24h）: 海面気圧 950〜1038hPa、2m気温 -44.6〜40.2℃。公式の予報と同等の範囲。

## つまずきと対処

| 現象 | 原因 | 対処 |
|---|---|---|
| `ai-models-ecmwf` が見つからない | パッケージ名が違う | `anemoi-inference` を直接使う |
| `Field z has the wrong shape` | 地表の `z` 取得時に等圧面も混ざる | 取得に `levtype`（sfc/sol/pl）を指定 |
| `torch_geometric...inspector` が無い | 重み(2024年)と新しいライブラリの不一致 | 公式ノートブック指定の版（Python 3.11・anemoi-inference 0.4.9・anemoi-models 0.3.1・torch 2.4.0）に固定 |
| `No module named flash_attn` | 重みが CUDA 専用 flash_attn を参照 | `ai/flash_attn_stub.py`（窓付きアテンションをPyTorchで実装）を import 前に差し込む。素のSDPAでは窓なしの全対全になり約54万点で破綻する。素朴実装との差は 1e-7 級で確認済み |
| CPUでNaN | SDPAの真偽値マスクが内部で `-inf` になり、全面マスクのブロックでNaN | 有限の大きな負値の加算マスクにする（スタブ内） |
| `autocast ... 'mps'` 未対応 | torch 2.4 | mps では autocast を無効化 |
| `scatter_reduce` が mps 未実装 | torch_geometric | `PYTORCH_ENABLE_MPS_FALLBACK=1` |
| CPUのfp16が極端に遅い・NaN | CPU autocast の float16 | `--precision 32`（既定） |
| MPSの結果が不安定 | torch 2.4 の MPS（原因の特定は未了。スタブの形・同期・勾配・インデックス代入のCPU化・マスク方式では変わらなかった） | **CPUを使う**。torch新版でのMPS再検証は今後の課題 |

## 未了・今後

- MPS が使えれば省メモリ・省電力の可能性。torch の新しい版＋anemoi-models の新しい版でチェックポイントが読めるか再検証する（`.venv_old` は Python 3.12・新版で読込に失敗した環境の残り）
- メモリ約55GB。float16/bfloat16 のCPU実行は未検証
