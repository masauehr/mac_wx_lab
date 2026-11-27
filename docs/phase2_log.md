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

---

# 実事例: 沖縄周辺 9km・GFS初期値・24時間予報（2026-11-27）

## 結論

**WPS → real → WRF の一連が Mac で通り、24時間予報が約417秒（6プロセス）で完走**。領域は沖縄周辺（121×101格子・9km・45層、北緯22〜30度・東経122〜133度）、初期値は GFS 0.25° の 2026-11-26 18UTC。ただし**降水が予報開始後6時間で最大約200mm出ており、スピンアップ（初期の過大）の疑いがある**。Phase 3 で GFS・AIFS と比較して確かめる。

## 手順（スクリプト）

| ステップ | ファイル |
|---|---|
| WRF ビルド | [physics/build_wrf.sh](../physics/build_wrf.sh)（理想実験用）。実事例は `./compile -j 8 em_real` も必要（`./clean` してから） |
| 静的地形データ（低解像度・約150MB） | [physics/fetch_geog.py](../physics/fetch_geog.py) |
| GFS の取得（必要な変数だけ・約130MB×9ファイル、約35分） | [physics/fetch_gfs.py](../physics/fetch_gfs.py) |
| 前処理〜実行 | [physics/run_case.sh](../physics/run_case.sh)、設定は `physics/cases/okinawa_20261126/namelist.{wps,input}` |
| 図 | [physics/plot_okinawa.py](../physics/plot_okinawa.py) |

主な設定: 物理は WSM6（雲物理）・RRTMG（放射）・YSU（境界層）・Kain-Fritsch（積雲）・Noah（地表）。`geog_data_res='lowres'`（5分≒9km）。時間刻み54秒、出力1時間間隔。

## つまずきと対処

| 現象 | 原因 | 対処 |
|---|---|---|
| WPS configure の選択肢 | arm64 の項目なし | 「Darwin Intel gfortran/clang (serial)」（21番）でよい。`echo 0 \|` で空回りするので番号を直接渡す |
| geogrid 等のリンク失敗 `_nf_*` | netCDF-Fortran 未指定 | `configure.wps` の `-lnetcdf` を `-lnetcdff -lnetcdf` に |
| ungrib のリンク失敗 `_jpc_decode` | 同梱のGRIB2ライブラリが古いJasPer API を呼ぶ | JPEG2000 を使わない GFS 用に、空の代替関数（`physics/WPS/jpc_stub.c`）をリンク。`-Wl,-U` だと起動時にdyldが落ちるので不可 |
| `real.exe` が無い | 理想実験用にしかビルドしていなかった | `./clean` → `./compile -j 8 em_real`（`configure.wrf` は残る）。理想実験の exe は `main/*_qss.exe` に退避 |
| real: `not enough info for a p sfc computation` | Vtable.GFS は海面更正気圧に **MSLET**（PRMSLではない）を使う | 取得対象に `:MSLET:mean sea level:` を追加 |
| `urllib` で SSL 証明書エラー | uv版Pythonは証明書が空 | `truststore.inject_into_ssl()`（macOSの証明書ストア） |
| `.part`→再取得 | 取得スクリプトは完了済みファイルをスキップ | 変数を追加したときは、ファイルを消して再取得するか追記する |

## 結果（暫定）

| 項目 | 値 |
|---|---|
| WPS（geogrid〜metgrid） | 各数秒〜数十秒 |
| real.exe | 数秒 |
| wrf.exe 24h | 約417秒（6プロセス）。CPU使用は sys 時間が大きく MPI の待ちが多い。プロセス数の最適化は未検証 |
| 10m風 最大 | 約21 m/s（+6h）。気温 11〜28℃ |
| 降水 | +6hまでに最大約200mm → **要検証（スピンアップの疑い）** |

## 今後（Phase 3）

- 同じ事例（初期 2026-11-26 18UTC）で AIFS（Phase 1の出力）と WRF を並べ、海面気圧・気温・風・降水を GFS/ECMWFの解析・実況と比較
- 降水のスピンアップが本当に過大か（GFS降水・アメダスと比較。積雲スキームの切り替え・初期時刻の見直し）
