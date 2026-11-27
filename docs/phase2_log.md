# Phase 2 作業ログ — WRF を Mac で動かす（理想実験）

2026-11-27。環境: Apple M5 Pro（P6コア+E12コア）/ 64GB / macOS。**結果: ビルド成功・理想実験（スーパーセル）完走**。

## 結論

- **WRF v4.7.1 が Mac（arm64）でビルド・実行できる**。依存は Homebrew を使わず conda-forge（プロジェクト内 `.conda`）に隔離した
- 理想実験 `em_quarter_ss`（スーパーセル・水平2km・42×42×40層）を 4並列（MPI）で **2時間分の積分が約10秒**
- 積乱雲が発達（最大上昇流 約43m/s、積算降水の最大 79mm、降水域が右へ分裂する典型的なスーパーセルの形）
- 実事例（Phase 3の比較用）は WPS・初期値データが必要。次の課題

## 手順

[physics/build_wrf.sh](../physics/build_wrf.sh) に全手順を記載（コンパイル約30〜60分）。実行は:

```bash
export PATH=$PWD/.conda/bin:$PATH
cd physics/WRF/test/em_quarter_ss
./ideal.exe && mpirun -np 4 ./wrf.exe
.venv/bin/python physics/plot_qss.py wrfout_d01_0001-01-01_00:00:00 outputs/qss.png   # 図
```

namelist は 実行時間 2時間・出力間隔10分に変更（既定は60分・30分間隔）。

## 設定とつまずき

| 項目 | 内容 |
|---|---|
| configure | 21番 GNU (gfortran/clang) **dmpar**、ネストは基本(1)。`mpicc -cc=clang` で問題なし |
| コンパイラ | conda-forge の gfortran 16.2。新しい gfortran 向けに `-fallow-argument-mismatch -fallow-invalid-boz` を `configure.wrf` に追加 |
| サブモジュール | 浅い clone では `phys/noahmp`・`phys/MYNN-EDMF` が空。`./compile` が自動で clone する（ネットワーク必要） |
| 監視の誤検知 | ログの `Error 1 (ignored)`（diffwrf。無視される想定）で完了判定しないこと。成功は `Executables successfully built` |
| NetCDF4/HDF5 | 圧縮つきNetCDF4でビルドされた（configureの表示）。出力は netCDF4 ライブラリで読める |

## 結果

| 項目 | 値 |
|---|---|
| ビルド | 約30〜60分（`-j 8`） |
| 実行 | 2h積分（600ステップ・dt=12s）約10秒・MPI 4プロセス |
| 最大上昇流 | 約43 m/s（t=40〜60分） |
| 積算降水 | 最大 79 mm（2時間） |
| 出力 | `wrfout`（13時刻・10分間隔）。図は outputs/qss.png（git除外） |

## 今後（Phase 2の残り・Phase 3へ）

- 実事例: WPS のビルド、初期値・境界値（GFS 0.25° など無料）の取得、沖縄周辺の小領域で台風・大雨を数値予報
- Phase 3 で同じ事例のAIFS予報と比較する
