# Opus配信用True Peakのエンコード前シーリング実測

## 結論

後処理 algorithm v7 の `pre_encode_true_peak_target_dbtp` は **-1.75 dBTP**、
最終配信物の `distribution_true_peak_max_dbtp` は **-0.9 dBTP** とする。

- -1.5 dBTPでは最終Opusに1件のTrue Peak違反が残る。
- -1.75 dBTPと-2 dBTPはいずれも381件すべてが最終True Peak gateを通る。
- -2 dBTPではIntegrated Loudnessの `shortfall` が3件から6件へ増える。
- よって、今回比較した候補のうち、固定ツールチェーンで違反0件となる最小の
  追加ヘッドルーム
  -1.75 dBTPを選ぶ。

エンコード前目標は最終成果物の品質保証を代替しない。公開Opusをデコードして
Integrated LoudnessとTrue Peakを再測定し、-18 ±1.5 LUFSまたは
-0.9 dBTPの分配上限を外れた場合は引き続きfail-fastで生成を失敗させる。
自動再エンコードや別シーリングへのfallbackは行わない。

## 測定条件

- 測定日: 2026-07-29
- FFmpeg: 8.1.1
- 対象: 既存の正規化済みWAV 381件
- 入力形式: 48kHz mono PCM WAV
- encoder: FFmpeg `libopus`
- encoder設定: 64kbps、VBR、`application=audio`
- encode回数: 各候補・各WAVにつき1回
- 最終測定: エンコード済みOpusをデコードし、FFmpeg `loudnorm` で
  Integrated LoudnessとTrue Peakを測定
- 最終gate: Integrated Loudness -18 ±1.5 LUFS、True Peak最大 -0.9 dBTP
- `shortfall`: Integrated Loudnessが -18 ±0.2 LUFSを外れるが、
  硬いgateの範囲内にあるもの

各候補は同じWAV集合から独立に作り、OpusからOpusへの再エンコードはしていない。
したがって、候補間の差はエンコード前True Peak目標だけで比較できる。

## 結果

| エンコード前True Peak目標 | 最終TP違反 | 最悪True Peak | shortfall | 最終Integrated Loudness範囲 |
| ---: | ---: | ---: | ---: | ---: |
| -1.5 dBTP | 1 | -0.86 dBTP | 3 | -18.40〜-17.81 LUFS |
| **-1.75 dBTP** | **0** | **-1.07 dBTP** | **3** | **-18.56〜-17.80 LUFS** |
| -2 dBTP | 0 | -1.16 dBTP | 6 | -18.63〜-17.79 LUFS |

-1.5 dBTPは最終OpusのTrue Peak上限を満たさないため不採用とする。
-2 dBTPはTrue Peakに余裕がある一方、-1.75 dBTPよりIntegrated Loudnessへの
下方向の変動が大きく、shortfallも倍増する。-1.75 dBTPは今回の381件で
最終True Peak違反を0件にしつつ、不要なLUFS変動を最小化する候補である。

## 既存失敗クリップの復旧

algorithm v6 の最終Opus gateで失敗した31件は、保存済みの正規化WAVを入力に
algorithm v7で再正規化し、各WAVを1回だけOpusへエンコードして復旧した。
adapter推論は再実行せず、既存Opusからの再エンコードも行っていない。

| モデル | 復旧件数 |
| --- | ---: |
| Chatterbox Multilingual V3 | 3 |
| CosyVoice3 | 3 |
| GPT-SoVITS v2ProPlus | 1 |
| Qwen3-TTS | 22 |
| Supertonic 3 | 2 |
| **合計** | **31** |

復旧後の31件は Integrated Loudness -18.56〜-17.80 LUFS、最大True Peak
-1.07 dBTPで、hard gate違反は0件だった。`shortfall` は1件。manifestは
381 clips / 1 failureとなり、以前から生成自体に失敗している
`qwen3-tts-12hz-1.7b/spirit-forest/spring-sprite-002` だけをfailureに残した。

復旧sidecarは `gen_params.realized.postprocess_recovery` に復旧mode、元の
`input_hash`、元WAVのSHA-256を記録する。`input_hash` はこの復旧recipeと
algorithm v7 profileから別途算出し、通常のadapter生成hashを装わない。
したがって、将来の通常生成は復旧物をcache hitとして扱わず、adapter PCMから
algorithm v7を適用して再生成する。

## 一次情報との関係

[EBU Tech 3344 v2.1](https://tech.ebu.ch/files/live/sites/tech/files/shared/tech/tech3344.pdf)
は、codecのデコード後overshootを考慮し、エンコード前のend-stage limiterを
-2 dBTPに設定する実務上の出発点を示している。同時に、低ビットレートでは
さらに下げ、高ビットレートでは上げられる場合があるとしており、codecと
ビットレートに応じた実測を求めている。これは放送の複数の有損codecを対象にした
一般ガイドラインであり、Opus固有の-2 dBTP規定ではない。

[FFmpeg `loudnorm` filter](https://ffmpeg.org/ffmpeg-filters.html#loudnorm) は
Integrated Loudness、Loudness Range、最大True Peakを目標にでき、dynamic modeでは
True Peak検出のため192kHzへアップサンプリングする。本測定と生成pipelineは同じ
FFmpeg測定境界を使い、候補WAVと最終Opusを評価する。

[Opus encoder API](https://opus-codec.org/docs/opus_api-1.6/group__opus__encoderctls.html)
はbitrateを `OPUS_SET_BITRATE`、VBRを `OPUS_SET_VBR` で制御する。本測定では
プロジェクトの固定配信条件である64kbps VBR / application audioを変えず、
エンコード前True Peak目標だけを比較した。

## 契約への反映

- `PostprocessProfile.algorithm_version`: 7
- `pre_encode_true_peak_target_dbtp`: -1.75
- `distribution_true_peak_max_dbtp`: -0.9
- sidecar: format v2を維持
- manifest: format v3を維持
- `manifest.clips[].loudness`: 最終Opusの測定値だけを公開

algorithm versionを更新することで既存cacheを新しい後処理profileと区別する。
sidecarの `loudness.normalized_wav` / `loudness.encoded_opus` とmanifestの
`source: encoded_opus` の構造・意味は変わらないため、各format versionは維持する。
