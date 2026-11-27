# mac_wx_lab — MacBookで気象物理モデル・AIモデルを動かす

詳しくは [mac-wx-lab.md](mac-wx-lab.md) を参照。

MacBook（Apple M5 Pro・メモリ64GB）上で、**気象の物理モデル（数値予報モデル）** と **気象AIモデル** を実際に動かし、
動作可否・所要時間・メモリ・結果の違いを記録する検証プロジェクト。

- **公開範囲**: public（モデルの重み・入力データ・出力はコミットしない）
- **状態**: 2026-11-27 新規作成・Phase 0〜1完了（AIFSをMacのCPUで24h予報・公式と一致）、Phase 2完了（WRFの理想実験と沖縄周辺の実事例24h予報）

## 目的

1. 手元のMacで「動く／動かない」を実機で確かめ、手順を残す
2. 物理モデルとAIモデルを同じ事例（例: 台風・沖縄の大雨）で比べる
3. 計算コスト（時間・メモリ・電力）の感覚をつかむ

## ディレクトリ構成

| パス | 内容 |
|---|---|
| [mac-wx-lab.md](mac-wx-lab.md) | プロジェクト全体の計画・Phase・判断メモ |
| [docs/candidates.md](docs/candidates.md) | 候補モデルの一覧と評価軸 |
| `physics/` | WRFのビルド（`build_wrf.sh`）・理想実験の図（`plot_qss.py`）。WRF本体は git除外 |
| [docs/phase2_log.md](docs/phase2_log.md) | Phase 2の作業ログ（WRFビルド・スーパーセル） |
| `ai/` | AIFS実行（`run_aifs.py`）・公式との比較（`compare_official.py`）・プロット・flash_attn代替スタブ |
| [docs/phase1_log.md](docs/phase1_log.md) | Phase 1の作業ログ・結果・つまずき |
| `data/` `models/` `outputs/` | 大容量・再取得可能。git除外 |

## 環境メモ

- Apple Silicon（arm64）・macOS・GPUはMetal（PyTorchはMPS、JAXはCPU/実験的Metal）
- Python環境はプロジェクト内 `.venv`（システムのpython3は使わない。brew更新で切り替わる事故があったため）
- APIキー・認証情報は `.env`（コミット禁止）
