# 日本語語尾イントネーション校正記録

**実施日:** 2026-07-30
**対象:** Issue #122 / `final_intonation`、末端 F0 interval、PASQA、AivisSpeech、Qwen3-TTS

## 結論

- 末端 F0 interval は 220 件の既存 eligible take に再適用できた。204 件で
  50 ms 以上の末端有声区間を測定でき、23 件が 2 semitone の上昇アンカーに
  到達した。日本語の gate としては未校正なので、引き続き report-only とする。
- AivisSpeech の `ノーマル` と `せつなめ` を同一 AudioQuery から各 5 回生成したが、
  `せつなめ` は下降終止を強めなかった。公開 API に seed はなく同一 style 内でも
  WAV hash が毎回変わるため、単発 A/B や style map の恒久変更は行わない。
- Qwen3-TTS の `surprised` 参照文を問句終止から2種類の陳述終止へ変えたが、
  下降幅の改善は確認できなかった。参照文を一括置換する根拠にはならない。
- PASQA は固定 checkpoint で実推論できた。ただし公式 runtime は
  Python 3.10 / torch 2.8 であり、既存 pipeline の Python 3.12 / torch 2.11
  へ直接統合できない。独立 runtime の承認後に ranking-only で接続する。

## `final_intonation` と測定定義

Schema の line に `final_intonation: fall | rise | free` を追加し、省略時だけ
Issue 指定どおり `fall` として解決する。標点や emotion から推測する別経路は
設けない。既存161行の明示ラベル付けは Issue #126 で行うため、以下の
`unexpected rise` は全行を既定 `fall` とした暫定集計である。

測定は最後の energy-active interval にある最後の連続有声区間を対象にする。
末尾最大 200 ms、最短 50 ms、F0 は全句 median を基準に ±6 semitone へ
クリップし、先頭2 frame と末尾2 frame の各 median 差を signed interval とする。
2 semitone / 50 ms は
[Peters & Pfitzinger (2008)](https://www.isca-archive.org/interspeech_2008/peters08_interspeech.html)
の知覚アンカーであり、日本語の hard threshold ではない。

## 既存 take の再解析

6 run の ledger、v2 QC report、current scenario SHA、eligible Opus SHA を検証し、
eligible 220 件だけを mono 16 kHz へ decode して共有 QC 関数で再解析した。
source runを#104 worktreeから主作業域へ移したため、QC reportの`source.ledger`
だけを実在する新pathへexact rebindした。6件の変更前後のQC report SHAとpathは
[再現条件 manifest](ja-intonation-calibration-provenance.json)に固定している。
F0 の全数値は `(model, scenario, character)` 内で算術平均と母標準偏差を用いて
z 正規化した。測定数が2未満、または標準偏差が0の場合は `null` とした。

- [完全な JSON レポート](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/reports/55489a568b3327d40e8478a41524a35f57973b55091ee4fe126ca51fc481880e/intonation-report.json)
  — SHA-256 `55489a568b3327d40e8478a41524a35f57973b55091ee4fe126ca51fc481880e`
- [model × gender Markdown レポート](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/reports/72c5afa573de6c6cf0da70ca6fa47068918621c9a2883bdf4ddeb9cf35d33853/intonation-report.md)
  — SHA-256 `72c5afa573de6c6cf0da70ca6fa47068918621c9a2883bdf4ddeb9cf35d33853`

| model | eligible n | interval measured | rise anchor | rate | clipped interval mean ± population std |
| --- | ---: | ---: | ---: | ---: | ---: |
| chatterbox-multilingual-v3 | 12 | 10 | 3 | 0.300 | 0.280 ± 2.743 st |
| cosyvoice3-0.5b-2512 | 12 | 11 | 1 | 0.091 | -1.650 ± 2.669 st |
| gpt-sovits-v2-pro-plus | 12 | 12 | 0 | 0.000 | -1.606 ± 1.672 st |
| qwen3-tts-12hz-1.7b | 160 | 151 | 16 | 0.106 | -2.026 ± 3.275 st |
| supertonic-3 | 12 | 10 | 1 | 0.100 | -0.015 ± 2.165 st |
| voxcpm2 | 12 | 10 | 2 | 0.200 | -0.065 ± 3.558 st |

モデル間の n が不均衡で、明示ラベルも未完了である。この結果から gate 閾値や
モデル順位を決めない。レポート生成コードと authority 検証を恒久化し、
Issue #126 完了後に同じ run で再集計する。

## PASQA 互換性 spike

固定した一次情報:

- code:
  [`bdbd3f84049b1ff3925e27888949831fc1977413`](https://github.com/lycorp-jp/PASQA/tree/bdbd3f84049b1ff3925e27888949831fc1977413)
- weights:
  [HF revision `7fe0bfc7dff16991599043bcafb886c7d597419a`](https://huggingface.co/ly-corporation/PASQA/tree/7fe0bfc7dff16991599043bcafb886c7d597419a)
- checkpoint LFS SHA-256:
  `03c9e8880a28f65fd9b8611f3fe3e179020b067d892cd6f6a4c311572b8a8bc7`

`uv run --no-project --python 3.10` の隔離環境で PASQA 0.1.0、
torch/torchaudio 2.8.0+cpu を解決した。固定 checkpoint と bundled config を
読み込み、外部で mono 16 kHz にした1秒音声と mora token `["ア"]` を入力すると、
`mos`、`frame_error_logits`、`frame_lengths` が返った。cache warm 後の実測は
load 6.867 秒、推論 0.116 秒だった。合成正弦波の MOS 値は品質根拠として使わない。

恒久化案は、明示コマンドでだけ起動する独立 Python 3.10 runtime とし、
同一 line の N take 内ランキングだけを出力する。pipeline 3.12 からの
silent fallback、gate 接続、絶対 score 閾値、mora 自動推定 fallback は設けない。

## AivisSpeech style 群比較

固定条件:

- AivisSpeech Engine 1.2.0 / コハク AIVMX 1.1.0
- text: `近頃は物騒でいかんなぁ。`
- [完全な再現条件 manifest](ja-intonation-calibration-provenance.json)と
  [実際の AudioQuery JSON](ja-intonation-aivis-audio-query.json)を固定した。
  AudioQuery JSON SHA-256:
  `4e860093a6e3a2dc3c3eaee813a5a3703bfef6dd6c093867afc4440feb7d4845`
- `intonationScale=1.0`、`tempoDynamicsScale=1.0`
- synthesis の style ID だけを `ノーマル` / `せつなめ` で切り替え、各5回

| style | n | clipped interval mean ± population std | 2st rise |
| --- | ---: | ---: | ---: |
| ノーマル | 5 | 0.330 ± 0.478 st | 0/5 |
| せつなめ | 5 | 0.850 ± 0.406 st | 0/5 |

全10 WAV の hash は異なった。`せつなめ` は平均で +0.52 st 上昇側へ動き、
Owner 仮説の「style を変えると下降終止が改善する」を支持しない。

- ノーマル:
  [1](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/aivis/normal/sample-01/0d906be4065897bb76d63ad930d2fee44af7c90bf369c49de6bcca29b666021d.opus) /
  [2](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/aivis/normal/sample-02/ed299fe47c26bfb58402e81843e52b5341f9bb253651c81d871197f4d0ccf623.opus) /
  [3](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/aivis/normal/sample-03/de9c389f01f5ea47a12da0bbb8bd26798ff1c0ad03b62de9e0cd4277be4c6351.opus) /
  [4](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/aivis/normal/sample-04/efb45050b0936e8e743ad1685d4223d3e215648b9834ab4a25ef5ad45ea26749.opus) /
  [5](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/aivis/normal/sample-05/7abd50a9a79d82c7d2168fcd32a7fece1672c2386380db43c245b0cd448d6f83.opus)
- せつなめ:
  [1](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/aivis/setsuname/sample-01/660458bf39ee70f2609d40a0c3eb2a89b8e10b4478529923e61408a430b8707e.opus) /
  [2](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/aivis/setsuname/sample-02/c1843b3a7c721a83560f24a0bae5e6fbb5b912e6088d56744d01132cc63fa24d.opus) /
  [3](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/aivis/setsuname/sample-03/e22950df4851436fabbc1c0a834cd09684c333dbe4796b9d01a5a92952337a2a.opus) /
  [4](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/aivis/setsuname/sample-04/0c9bf93fe443ae675454af64d00697681eab75255e2acf9ed1cf196acbcd2e3e.opus) /
  [5](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/aivis/setsuname/sample-05/5d929545e3b6d2857406acd0d8a86b7614b055530f0ec17dd8e68d06f3d2295c.opus)

恒久化判断は「style map を変更しない」。style は感情カテゴリとして使い、
下降終止の制御器として扱わない。

## Qwen3-TTS reference text A/B

`castle-gate / merchant / surprised / intensity-2` の固定 VoiceDesign revision、
同一 instruct、seed 0、sampling を使用した。exact revision、instruct、sampling、
3条件のreference textとWAV/Opus SHAは
[再現条件 manifest](ja-intonation-calibration-provenance.json)に固定した。
現在文の再生成 WAV SHA-256
`1fe58ec4c52a94aca1daeed67962ddf9411926670fdb85edb1b8dfe06cb36732`
は既存 reference と一致し、条件の再現を確認した。

| 条件 | reference text | clipped interval | 判定 |
| --- | --- | ---: | --- |
| current | `えっ、本当に？そんなことがあるなんて！` | 0.000 st（raw -3.800） | rise=false |
| declarative | `まさか、こんなことが本当に起きたとは思わなかった。` | null | 末端有声区間不足 |
| voiced terminal | `まさか、こんなことが本当に起きたんだな。` | 0.000 st | rise=false |

- [current](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/qwen/current-question-terminal/20ad72bfaf53e5bcfab116718eef169c2b9fdd2ce3e636ba50fa6422b6ed258b.opus)
- [declarative](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/qwen/falling-declarative-terminal/9d7d8206ec7e678a8068e632da30b7242db79b46cc30d2fb8216e523931494c0.opus)
- [voiced terminal](https://audio.gaya-bench.hitsuki.space/experiments/issue-122/qwen/falling-voiced-terminal/2a272fdc96f8d46f87c23519ca0610384a7da1b79b3913e9c2427b3281c6e4d6.opus)

この1ケースでは現在文が既に上昇アンカーを回避し、2つの陳述文にも改善がない。
恒久化判断は「参照文 map を変更しない」。将来再試験する場合は複数 emotion、
複数 character を対象にし、文末が測定可能な有声音かも実験設計に含める。
