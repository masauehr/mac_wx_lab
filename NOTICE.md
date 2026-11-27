# NOTICE — データ・モデル・ソフトウェアの出典とライセンス

このリポジトリの**自作コードと文書は MIT License**（[LICENSE](LICENSE)）です。
以下の外部のデータ・モデル・ソフトウェアは**このリポジトリに含めていません**（取得手順のスクリプトだけを置いています）。
公開サイトの画像は、これらを加工して作図したものです。各提供元の条件は 2026-11-27 時点で確認しました。規約は変わり得るので、公開を続ける間は定期的に確認してください。

| 対象 | 使い方 | ライセンス・条件 | 必須の表示 |
|---|---|---|---|
| ECMWF オープンデータ（IFS・AIFS の予報） | 比較の参照、および図 | CC BY 4.0 と ECMWF 利用規約（<https://apps.ecmwf.int/datasets/licences/general/>）。再配布・改変・商用可 | © ECMWF、出典 www.ecmwf.int、ライセンス表示、**改変の明示**、免責 |
| AIFS Single v1.0 の重み（Hugging Face `ecmwf/aifs-single-1.0`） | 手元で予報を実行 | CC BY 4.0（重み）。ノートブック類は Apache-2.0 | 論文の引用: Lang et al. (2024), arXiv:2406.01465 |
| NOAA/NCEP GFS 0.25°（AWS Open Data） | WRFの初期値・境界値 | NOAA/NWS の条件（合法的な目的で無償利用可） | 出典の表示。NOAA/NWS の支持・提携を示唆しない。改変物を原データと称さない |
| WRF v4.7.1・WPS v4.7.0（NCAR/UCAR） | 物理モデルの実行 | パブリックドメイン（WRF の LICENSE.txt） | UCAR は注意書き（LICENSE.txt）を付けるよう要請。WRF は UCAR の登録商標 |
| WPS_GEOG（地形・土地利用などの静的データ） | WRFの前処理 | NCAR のダウンロードページに**利用条件の記載なし**。元データは MODIS・GMTED2010 など | データ自体は再配布しない（図のみ公開）。出典として NCAR WPS_GEOG・MODIS・USGS/GMTED2010 を表記 |
| anemoi-inference・anemoi-models | AIFS の実行 | Apache-2.0（ECMWF 他） | — |
| conda-forge の gfortran・netCDF・MPICH 等 | WRFのビルド | 各パッケージのライセンス（主に GPL・BSD系）。再配布はしていない | — |

## 図についての注意

- 図は、上記の元データ・モデル出力を**加工して本アプリが作図した参考図**です。ECMWF・NOAA・UCAR の公式な図ではなく、いかなる国・機関の公式予報でもありません。
- ECMWF は、データの誤り・欠落・利用による損害に責任を負いません。NOAA/NWS・UCAR・ECMWF による支持・提携はありません。
- 予報は不確実性を含みます。**防災・安全に関わる判断には、気象庁など公的機関の公式情報を使ってください。**
- **WRF（9km）の降水は過大の疑いがあります**（予報開始後6時間のスピンアップ。詳細は [docs/phase3_log.md](docs/phase3_log.md)）。
- 比較の参照に使った IFS は**真値ではありません**（モデル間の差を見る目安です）。
