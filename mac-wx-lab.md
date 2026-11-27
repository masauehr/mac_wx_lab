# mac_wx_lab 計画

## 方針

- 動かすこと自体が目的。精度競争はしない
- 1モデルずつ「導入→最小実行→記録」で進め、失敗も記録する
- 大容量データ・重みはコミットしない。取得手順だけ残す
- 各リポジトリは単独で動く自己完結を優先（他プロジェクトの部品に依存しない）

## 実機

| 項目 | 値 |
|---|---|
| チップ | Apple M5 Pro |
| メモリ | 64GB（統合メモリ） |
| 備考 | 空き容量は事前に確認（モデル重み・再解析データが大きい） |

## Phase

| Phase | 内容 | 状態 |
|---|---|---|
| 0 | 候補整理・評価軸の決定（[docs/candidates.md](docs/candidates.md)） | 完了 |
| 1 | AIモデル1つを最小実行（AIFS Single v1.0・全球24h予報） | 完了（CPU・fp32で約224秒・公式と一致。[docs/phase1_log.md](docs/phase1_log.md)） |
| 2 | 物理モデル1つを最小実行（理想実験→実事例） | 完了（WRF v4.7.1・スーパーセル2hが約10秒、実事例=沖縄周辺9km・GFS初期値の24h予報が約417秒。降水の過大は要検証。[docs/phase2_log.md](docs/phase2_log.md)） |
| 3 | 同一事例で比較（時間・メモリ・結果の違い） | 未着手 |
| 4 | 最終レポート（動いた/動かなかった・手順・所要時間） | 未着手 |

## 評価軸

1. Apple Silicon（arm64）で動くか（ビルド・依存関係）
2. メモリ64GBに収まるか
3. 初期値・境界値データが無料・認証容易で取れるか
4. ライセンス（非商用・研究用など）が公開リポジトリと両立するか
5. 所要時間が現実的か（1回の実行が数時間以内）

## 注意

- 重みのライセンスは配布元で必ず確認する（再配布しない）
- 長時間計算中はスリープ・電源に注意（`caffeinate` の利用を検討）
- 結果は研究・学習用。予報として使わない

## Phase 1 の結果（2026-11-27）

AIFS Single v1.0（ECMWF・CC BY 4.0）を Mac の **CPU・fp32** で実行。24h予報 約224秒・最大メモリ約55GB・公式AIFSと+24hで一致（海面気圧RMSE 0.42hPa、2m気温0.65K）。MPS（GPU）は torch 2.4 で結果が実行ごとに変わり不安定なため不採用。詳細・つまずき・実行手順は、プロジェクト内の `docs/phase1_log.md`（GitHub: masauehr/mac_wx_lab）を参照。

## Phase 1 の結果（2026-11-27）

AIFS Single v1.0（ECMWF・CC BY 4.0）を Mac の **CPU・fp32** で実行。24h予報 約224秒・最大メモリ約55GB・公式AIFSと+24hで一致（海面気圧RMSE 0.42hPa、2m気温0.65K）。MPS（GPU）は torch 2.4 で結果が実行ごとに変わり不安定なため不採用。詳細は [mac-wx-lab-phase1.md](mac-wx-lab-phase1.md)（プロジェクト内は `docs/phase1_log.md`）。

## Phase 2 の結果（2026-11-27）

WRF v4.7.1 を Mac（arm64）でビルド（gfortran/clang・dmpar。依存は Homebrew を使わず conda-forge に隔離）。理想実験 em_quarter_ss（スーパーセル）の2時間積分が約10秒（MPI 4並列）。最大上昇流43m/s・積算降水79mm。実事例（WPS・初期値）は未着手。ビルド手順・つまずきは [mac-wx-lab-phase2.md](mac-wx-lab-phase2.md)（プロジェクト内は `docs/phase2_log.md`、`physics/build_wrf.sh`）。

## Phase 1 の結果（2026-11-27）

AIFS Single v1.0（ECMWF・CC BY 4.0）を Mac の **CPU・fp32** で実行。24h予報 約224秒・最大メモリ約55GB・公式AIFSと+24hで一致（海面気圧RMSE 0.42hPa、2m気温0.65K）。MPS（GPU）は torch 2.4 で結果が実行ごとに変わり不安定なため不採用。詳細は [mac-wx-lab-phase1.md](mac-wx-lab-phase1.md)（プロジェクト内は `docs/phase1_log.md`）。

## Phase 2 の結果（2026-11-27）

WRF v4.7.1 を Mac（arm64）でビルド（gfortran/clang・dmpar。依存は Homebrew を使わず conda-forge に隔離）。理想実験 em_quarter_ss（スーパーセル）の2時間積分が約10秒。実事例として、WPS＋GFS 0.25°初期値（2026-11-26 18UTC）で沖縄周辺9km（121×101×45層）の24時間予報が約417秒（6プロセス）で完走。降水が+6hで約200mmと過大の疑いがあり、Phase 3で検証する。手順・つまずきは [mac-wx-lab-phase2.md](mac-wx-lab-phase2.md)（プロジェクト内は `docs/phase2_log.md`、`physics/*.sh`・`physics/*.py`）。
