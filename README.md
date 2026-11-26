# mac_wx_lab — MacBookで気象物理モデル・AIモデルを動かす

詳しくは [mac-wx-lab.md](mac-wx-lab.md) を参照。

MacBook（Apple M5 Pro・メモリ64GB）上で、**気象の物理モデル（数値予報モデル）** と **気象AIモデル** を実際に動かし、
動作可否・所要時間・メモリ・結果の違いを記録する検証プロジェクト。

- **公開範囲**: public（モデルの重み・入力データ・出力はコミットしない）
- **状態**: 2026-11-27 新規作成・Phase 0（候補整理）のみ・未実行

## 目的

1. 手元のMacで「動く／動かない」を実機で確かめ、手順を残す
2. 物理モデルとAIモデルを同じ事例（例: 台風・沖縄の大雨）で比べる
3. 計算コスト（時間・メモリ・電力）の感覚をつかむ

## ディレクトリ構成

| パス | 内容 |
|---|---|
| [mac-wx-lab.md](mac-wx-lab.md) | プロジェクト全体の計画・Phase・判断メモ |
| [docs/candidates.md](docs/candidates.md) | 候補モデルの一覧と評価軸 |
| `physics/` | 物理モデルの作業置き場（作成時に追加） |
| `ai/` | AIモデルの作業置き場（作成時に追加） |
| `data/` `models/` `outputs/` | 大容量・再取得可能。git除外 |

## 環境メモ

- Apple Silicon（arm64）・macOS・GPUはMetal（PyTorchはMPS、JAXはCPU/実験的Metal）
- Python環境はプロジェクト内 `.venv`（システムのpython3は使わない。brew更新で切り替わる事故があったため）
- APIキー・認証情報は `.env`（コミット禁止）
