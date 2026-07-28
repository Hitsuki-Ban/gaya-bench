# SilentCipher 最終 Opus 残存率

## 結論

Irodori-TTS が出力した透かし入り PCM16 と、後処理 v4 の loudnorm 後 WAV
では、固定 payload `IRDTS` を12/12件で完全に復号できた。最終配信用の
64kbps Opus では通常 decode が8/12件、phase-shift decodeでも9/12件の完全一致に
留まった。今回の条件では loudnorm ではなく Opus encode が残存率低下の境界である。

`silentcipher_watermark_stage_executed` の仕様は次のとおりとする。

- 固定 SilentCipher snapshot により payload `IRDTS` の埋め込み stage が実行された
  事実だけを表す。
- 最終 Opus に watermark が残存すること、または decoder で検出できることを
  表さない。
- UIや文書では「透かし埋め込み処理済み（最終Opusでの検出保証なし）」と表現する。
- 将来、最終成果物からの検出を必須要件にする場合は、最終 Opus を全件 decode し、
  `IRDTS` 完全一致だけを成功とする。payload不一致と未検出を成功扱いせず、
  生成を明示的に失敗させる。

現状の短いガヤ音声ではこの gate を12/12で通過できないため、最終検出を生成要件には
しない。残存率を上げるための後処理後再埋め込みや codec/bitrate 変更も、ベンチの
配信仕様と音質を変える別要件なので本調査には含めない。

## 測定条件

- 日時: 2026-07-28
- 環境: Windows 11、NVIDIA GeForce RTX 4070 Ti 12GB
- 対象: `tavern-night`、`market-day` の全12行
- Irodori-TTS: `0.1.0` /
  `Aratako/Irodori-TTS@eaf74d6a19138f743acb5b71a445fd25a57db987`
- SilentCipher: `1.0.5` /
  `SesameAILabs/silentcipher@d46d7d0893a583d8968ab3a6626e2289faec9152`
- SilentCipher model:
  `sony/silentcipher@a1c4d021905e0dc5b24be5f68db5fc4dba410ee1`
- PyTorch: `2.10.0+cu128`
- FFmpeg: `8.1.1`、`libopus`、64kbps VBR、48kHz mono
- Irodori生成: BF16、40 steps、seed 0、payload `IRDTS`
- 後処理: algorithm v4、-18 LUFS、-1 dBTP

各行を同じ生成結果から次の3段階へ進め、それぞれ通常 decode と
`phase_shift_decoding=True` の両方で検査した。

1. Irodori adapter が書き出した透かし入り48kHz mono PCM16
2. loudnorm と品質 gate を通過した48kHz mono PCM16
3. 配信用64kbps VBR Opus

全12件の normalization type は `linear` で、limiter correction は発生しなかった。
`exact` は5バイトが `[73, 82, 68, 84, 83]` と完全一致、`mismatch` は透かしを
検出したがpayloadが不一致、`undetected` はdecoderの `status=false` を表す。

再測定コマンド:

```console
uv sync --project pipeline --locked --extra irodori
uv run --project pipeline --locked --extra irodori python pipeline/tools/measure_silentcipher_survival.py --scenarios-dir scenarios --voices-dir <ローカル参照音声ディレクトリ> --output-dir artifacts/research/silentcipher-survival
```

参照音声は権利制約によりリポジトリへ含めない。コマンドは不足時に失敗し、
別音声やno-ref生成へ切り替えない。音声と機械可読な `report.json` は
`artifacts/` 配下へ出力し、コミットしない。

## 集計

| 段階 | 通常 exact | 通常 mismatch | 通常 undetected | phase-shift exact | phase-shift mismatch | phase-shift undetected |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| source PCM16 | 12 | 0 | 0 | 12 | 0 | 0 |
| normalized WAV | 12 | 0 | 0 | 12 | 0 | 0 |
| final Opus | 8 | 1 | 3 | 9 | 1 | 2 |

## 最終 Opus の全件結果

| scenario / line | 尺（秒） | 通常 | 通常 confidence | phase-shift | phase-shift confidence |
| --- | ---: | --- | ---: | --- | ---: |
| `tavern-night/barmaid-001` | 3.9265 | undetected | — | exact | 0.952381 |
| `tavern-night/barmaid-002` | 4.2065 | exact | 0.928571 | exact | 0.940476 |
| `tavern-night/drunkard-001` | 5.4465 | undetected | — | undetected | — |
| `tavern-night/drunkard-002` | 4.1265 | undetected | — | undetected | — |
| `tavern-night/old-regular-001` | 4.0065 | exact | 0.964286 | exact | 0.988095 |
| `tavern-night/old-regular-002` | 4.3265 | exact | 0.916667 | exact | 0.928571 |
| `market-day/fruit-vendor-001` | 5.6465 | exact | 0.761905 | exact | 0.771429 |
| `market-day/fruit-vendor-002` | 3.6865 | exact | 0.888889 | exact | 0.936508 |
| `market-day/shopper-001` | 4.0065 | exact | 0.928571 | exact | 0.952381 |
| `market-day/shopper-002` | 3.7665 | exact | 0.968254 | exact | 0.968254 |
| `market-day/street-kid-001` | 2.8865 | exact | 0.888889 | exact | 0.888889 |
| `market-day/street-kid-002` | 4.9265 | mismatch | 0.647619 | mismatch | 0.685714 |

`street-kid-002` の通常 decode は `[76, 82, 64, 84, 19]`、phase-shift decode は
`[65, 83, 64, 84, 83]` であり、どちらも `IRDTS` と一致しない。

## 一次情報との比較

SilentCipherの固定コードは44.1kHzモデルを使用し、5個の8-bit文字（40 bit）を
payloadとして扱う。公式READMEは phase-shift decode を audio crop への対策として
推奨する一方、decode時間が大幅に増えると説明している。

- [SilentCipher固定revision README](https://github.com/SesameAILabs/silentcipher/tree/d46d7d0893a583d8968ab3a6626e2289faec9152#-usage)
- [SilentCipher論文](https://arxiv.org/abs/2406.03822)

論文は圧縮への耐性を高める学習を説明しているが、評価条件は6、12、24秒の音声と
MP3、OGG、AACの64/128/256kbpsである。本測定の2.8865〜5.6465秒の短い
FFmpeg/libopus成果物と同一条件ではない。phase-shift処理も crop の位相探索であり、
Opus圧縮後のpayload完全一致を保証する機能ではない。したがって論文の高い平均精度を、
本プロジェクトの全クリップに対する検出保証とは解釈しない。
