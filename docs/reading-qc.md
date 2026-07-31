# 読み・韻律 QC

`gaya qc --run-id` は generation run の ledger、sidecar、WAV/Opus を結合し、
各 take を Gate 1 mechanical、Gate 2 content、Gate 3 report-only の順に
評価する。公開 manifest v3 は入力にも出力にも使わない。

## 実行境界

```console
uv sync --project pipeline --locked --extra qc
uv run --project pipeline --locked --extra qc gaya qc --run-id <run-id>
```

`run-id` は `artifacts/takes` 配下の単一 path segment でなければならない。
対象集合は `ledger.source.groups` と `ledger.attempts` で固定され、model、
scenario、line による部分実行は行わない。

TTS と QC は同一 process にロードしない。`gaya gen` の process が終了した後に
独立した `gaya qc` process を起動し、そこで Kana Whisper をロードする。
adapter の `unload` や CPU runtime への切替は設けない。

run-local の出力は次の二つである。

- `artifacts/takes/<run-id>/qc-report.json`: format v2 の gate 理由、ASR、
  未校正の韻律 feature と hard reject / review の集計
- `artifacts/takes/<run-id>/manifest-v4.json`: terminal run の eligible take だけを
  投影した local snapshot

ledger は attempt ごとに原子的に checkpoint する。`planned`、`generated`、
`blocked` のいずれかが残る run では snapshot を書かず、終了 code 1 を返す。
全 attempt が terminal なら、eligible がない group を `no_eligible_take` として
記録し、snapshot 全体を検証してから原子的に確定する。

## Ledger join

model load 前に run-id、ledger、scenario source hash、sidecar identity、
generation input hash、take recipe、requested parameters、toolchain、
WAV/Opus path と SHA-256 を照合する。欠損や provenance の不一致は音質不良とは
みなさず `blocked` とする。入力が検査中に変化した場合も `blocked` である。

terminal attempt は再分類しない。再実行時は既存の format v2 report を厳密に
検証し、terminal attempt の ASR、Kana-CER、mismatch、review reason をそのまま
再利用する。report が欠損・破損している場合や gate policy が異なる場合は
fail-fast し、新しい generation run を要求する。`blocked` だけを同じ provenance
で再評価し、まだ解消していなければ ledger を書き換えず `blocked` のまま残す。
`generation_failed` は generation phase の terminal 結果として Gate 対象外とする。

## Gate 1: mechanical

最終 Opus を独立に decode/probe/measure し、sidecar の記録を判定値として信用しない。
次を hard reject とする。

- decode 不可、空音声、非有限 sample、48 kHz mono Opus でない
- Integrated Loudness が -18 ±1.5 LUFS の範囲外
- True Peak が -0.9 dBTP を上回る
- active speech が 0 秒

mechanical reject は `mechanical=reject, content=not_run` と記録する。
provenance、tool、runtime の都合で検査を完了できない場合は
`mechanical=blocked, content=not_run` とし、reject に変換しない。

## Gate 2: content

期待 reading は `line.reading` を最優先する。明示 reading と Kana ASR が一致すれば
`pass`、不一致なら `review_required` とする。不一致は
`review_reason=explicit_reading_mismatch`、normalized ASR transcript、
Kana-CER、`reading_mismatch=true` とともに report へ残すが、candidate から除外せず
人間の内容確認へ送る。空 ASR、runtime error、検査中の artifact 変更は `blocked`
であり、誤読として reject しない。

`line.reading` がない場合は既存の日本語 reading 解決を report に残すが、G2P
推定や多読み語を正解とはみなさず、
`review_reason=non_authoritative_expected_reading` の `review_required` とする。
`review_required` は eligible であり、pass へ変換しない。

ASR は次の単一路径に固定する。

- model: `sbintuitions/kana-whisper`
- revision: `88ecb3d79c5846cb4fcf76f4107b84c8fa2acd82`
- runtime: `torch==2.11.0` / `transformers==5.3.0`
- device: Windows native `cuda:0`
- dtype: FP16
- license: model MIT

固定 revision をローカル snapshot に取得してから load し、依存、CUDA、FP16
対応 GPU、ffmpeg のいずれかが欠ける場合は `blocked` とする。CPU、別 model、
別 revision へ切り替えない。

## Gate 3: report-only

`librosa==0.11.0` で duration、active speech、mora speed、pause、F0、energy を
記録する。active speech 0 だけは Gate 1 の無効音声判定に使う。それ以外は
校正済み scorer、順位、合否閾値を持たず、ledger では
`features.status=unscored` のままとする。

QC は音声を上書き・削除しない。hard reject と generation failure の artifact は
run に監査用として残るが、manifest v4 candidates には決して含めない。

明示 reading mismatch を review signal とする根拠は
[#103 N=3 pilot](research/n3-pilot/report/pilot-report.md) に固定した。
Kana Whisper の公式 model card は片仮名 transcript と Kana-CER の評価用途を説明するが、
単一 take の production hard gate は保証していない。
[Sarashina2.2-TTS](https://arxiv.org/html/2606.25369) も強い話し方では
人が理解できる音声を Kana-ASR が誤認識し得ると報告している。このため ASR evidence
は順位 score にも使用せず、人間の `content_correct` 判定を置き換えない。

## Chatterbox 人工誤読による再校正

[#159 の固定7検体](research/kana-asr-calibration/README.md)では、人手証拠のバッチ集計が
語彙誤読7件・自動検出2件・差分5件を記録する一方、normalized exact match が pass に
した5件の Kana-CER は0だった。現行判定は数値 threshold を持たないため、この
漏検は距離調整では解消しない。production policy は `review_required` の soft signal の
まま維持し、hard reject、別 ASR、silent fallback を追加しない。

Chatterbox の上流 G2P が `退がれ` を `たいがれ` にする生成前の決定的差分は、
期待 `サガレ` と別に fixture へ残す。上流入力監査、生成後 ASR、人手の語彙判断、
同じ Kana 列では検出できない pitch accent 判断を混同しない。
