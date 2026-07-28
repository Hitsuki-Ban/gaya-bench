# 読み・韻律 QC

`gaya qc` は公開 manifest の最終 Opus を再解析し、読みの一致と未校正の
韻律 feature を独立 JSON report に記録する。dry 音声、sidecar、
`data/manifest.json` は変更しない。

## 対象と出力

対象集合は `data/manifest.json` format v3 の `clips` だけである。
ローカルに残った未公開 artifact は配布対象の真値ではないため自動探索しない。
各 Opus は解析前に manifest の SHA-256 と照合する。
全対象の存在、artifacts 内への収まり、SHA-256、出力先との衝突を
model load 前に preflight する。実行中に manifest または音声が変わった
場合も report を確定せず失敗する。

出力は既定で `artifacts/qc/report.json` format v1 とする。report は
ASR model/revision、依存 version、manifest SHA-256、各 clip の
`(model, scenario, line, variant, audio_sha256)` を含むため、#76 の
take gate は公開 manifest を変更せずに同一音声へ結合できる。
絞り込み実行では `source.clip_set` を `manifest.clips.selection` とし、
`source.selection` に selector と `filtered` coverage を記録する。

```console
uv sync --project pipeline --locked --extra qc
uv run --project pipeline --locked --extra qc gaya qc
```

対象を絞る場合:

```console
uv run --project pipeline --locked --extra qc gaya qc \
  --model qwen3-tts-12hz-1.7b \
  --scenario chinatown-street \
  --line shokudo-oyaji-002
```

明示 reading と Kana ASR が不一致の場合、`mismatch` として report を
書いたうえで終了 code 1 を返す。個別解析エラーも `analysis_error` として
report に残して終了 code 1 とする。
韻律 feature と、期待 reading 自体を確定できない状態は本 Issue では
品質閾値に使わない。

## 読み判定

期待 reading は `line.reading` を最優先する。省略時は既存の
`pyopenjtalk.g2p(text, kana=True)` を使うが、`辛い / 行った / 人気 /
大分` のような多読み語を含む行では自動 G2P を正解とみなさず
`needs_reading` にする。`gaya validate` はその場で warning を出し、
`line.reading` の明記を求める。

明示 reading のある行だけを `pass / mismatch` の hard 判定対象にする。
多読み語を含むが reading がない行は `needs_reading`、それ以外の
PyOpenJTalk 由来 reading は比較値と Kana-CER を残しつつ
`review_required` とする。自動 G2P を唯一の正解として終了 code 1 の
根拠にはしない。

ASR は次の単一路径に固定する。

- model: `sbintuitions/kana-whisper`
- revision: `88ecb3d79c5846cb4fcf76f4107b84c8fa2acd82`
- runtime: `torch==2.11.0` / `transformers==5.3.0`
- device: Windows native `cuda:0`
- dtype: FP16
- license: model MIT

Kana Whisper は日本語音声を片仮名列へ直接転写するため、通常の ASR が
同形表記 `辛い` に戻して `からい / つらい` の音声差を失う問題を避ける。
model は固定 revision を明示 download した後、ローカル snapshot だけを
ロードする。依存、CUDA 13.0、FP16 対応 GPU、ffmpeg のいずれかが欠ける
場合は準備段階で失敗し、CPU や別 model へ切り替えない。

`chinatown-street/shokudo-oyaji-002` の正解は
`ウチノマーボーワカライヨ、カクゴシナ！` と scenario に明記する。
旧 Qwen dry の `ツライ` は保持し、QC mismatch の回帰対象とする。

## 韻律 feature

`librosa==0.11.0` で次を clip ごとに記録する。

- 全体時間、非静音時間、推定 mora 数、全体/発音中 mora 毎秒
- 80 ms 以上の内部 pause 数・合計・最長、先頭/末尾静音
- pYIN の F0 median / p10 / p90 / semitone 標準偏差 / voiced ratio
- RMS energy の median / p95 dBFS

F0 を得られない場合は `null` とし、0 Hz を混ぜない。pause、F0、
energy、話速は report-only の未校正 feature であり、#74 の統合結論に
従って本 Issue では reject 閾値や自動補正を設けない。

## 修正版の扱い

QC は `suggested_reading` を提示するだけで dry 音声を上書きしない。
reading 対応 model の再生成は明示的な別操作とし、将来 corrected variant
を導入する場合も dry と同一 key に置き換えない。
