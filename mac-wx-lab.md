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
| 3 | 同一事例で比較（時間・メモリ・結果の違い）＋ローカルWebアプリ | 完了（AIFSとWRFをIFS比で比較。WRFの降水が過大。計算の実行と表示ができるWebアプリ。[docs/phase3_log.md](docs/phase3_log.md)） |
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

## Phase 1〜3 の結果（2026-11-27）

- **Phase 1（AIFS）**: AIFS Single v1.0 を Mac の **CPU・fp32** で実行。24h予報 約224秒・最大メモリ約55GB・公式AIFSと一致（海面気圧RMSE 0.42hPa）。MPS(GPU)は torch 2.4 で結果が不安定なため不採用。[mac-wx-lab-phase1.md](mac-wx-lab-phase1.md)
- **Phase 2（WRF）**: WRF v4.7.1 を Mac でビルド（conda-forge に隔離）。理想実験（スーパーセル）2h約10秒、実事例（WPS＋GFS初期値・沖縄周辺9km）24h約417秒。[mac-wx-lab-phase2.md](mac-wx-lab-phase2.md)
- **Phase 3（比較とWebアプリ）**: AIFS・公式AIFS・WRF を IFS 比で比較。**WRFの降水が過大**（領域平均 IFS比+35%・最大2倍。スピンアップの疑い）。計算の実行と表示ができるローカルWebアプリ（`bash webapp/start.sh` → http://127.0.0.1:8795/）と、閲覧専用の公開サイト（GitHub Pages: https://masauehr.github.io/mac_wx_lab/）。MITライセンス。データの規約確認（ECMWF=CC BY 4.0、NOAA GFS、WRF=パブリックドメイン、WPS_GEOGは条件の記載なし）は [mac-wx-lab-phase3.md](mac-wx-lab-phase3.md) と NOTICE.md。

