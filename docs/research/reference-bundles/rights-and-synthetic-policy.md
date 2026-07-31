# 権利・実録・合成参照ポリシー

最終確認日: 2026-07-31

## 権利を分離する

bundle の音声権利は `rights.permissions` に次の実フィールド名で記録する。
値はすべて `permitted` / `prohibited` である。

| field | 意味 |
| --- | --- |
| `tts_reference_inference` | TTS / voice clone の推論参照入力に使えるか |
| `training_or_finetuning` | 学習、fine-tuning、speaker inversion 等に使えるか |
| `commercial_generated_output` | 生成出力を商用公開できるか |
| `audio_redistribution` | 元音声・加工音声を再配布できるか |

許諾期間は `rights.term.type: perpetual` または `fixed` で記録する。
`fixed` は `starts_on`、`expires_on`、`renewal_review_on` を持つ。

clip の `transcript.rights` は音声 rights と独立し、`redistribution`、
`credit`、`evidence` を必須にする。例えば、つくよみちゃんの音声許諾と
CC BY-SA 4.0 の台本許諾は別である。音声の再配布可否から台本文字の再配布可否を
推定しない。

## origin

`origin.type` は次の三つだけである。

- `public_corpus`
- `commissioned_recording`
- `synthetic`

それぞれ `rights.evidence.type` は `public_license`、`contract`、
`model_terms` と一致させる。契約収録では origin と evidence の
`contract_reference_id` を一致させる。

## 現 5 素材の移行判断

| bundle ID | permissions の判断 | term / credit / transcript |
| --- | --- | --- |
| `amitaro-countdown` | `tts_reference_inference: permitted`、`commercial_generated_output: permitted`。`audio_redistribution` は project では `prohibited` | 規約再確認日を `perpetual.reviewed_on` に記録し、指定 credit を保持。[公式 FAQ](https://amitaro.net/voice/faq/) / [規約](https://amitaro.net/voice/voice_rule/) |
| `hadou-emotion-11` | AI 学習・TTS の明示許可に基づき個別判断。原音・加工原音公開は禁止 | 禁止用途と credit を保持。[公式 dataset card](https://huggingface.co/datasets/hadou1225/Hadou-Voice-Dataset) |
| `lux-emotion-76` | CC BY 4.0。project 方針で `audio_redistribution: prohibited` | attribution を保持。[公式 dataset card](https://huggingface.co/datasets/Lami/Lux-Japanese-Speech-Corpus) / [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `sayoko-emotion-75` | CC BY 4.0。project 方針で `audio_redistribution: prohibited` | 収録時 81 歳という provenance と attribution を保持。[公式 dataset card](https://huggingface.co/datasets/bandad/sayoko-tts-corpus) |
| `tsukuyomi-corpus-94` | 規約に従い参照推論と商用出力を個別記録。音声再配布は禁止 | 音声 credit と、台本 CC BY-SA 4.0 の `transcript.rights` を別に保持。[公式コーパス](https://tyc.rei-yumesaki.net/material/corpus/) / [規約一覧](https://tyc.rei-yumesaki.net/about/terms/list/) |

具体値は Phase 2 で現 `assets/voices/metadata.yaml` の evidence と一次規約を
再照合して移す。`tts_reference_inference: permitted` から
`training_or_finetuning: permitted` を推定しない。

## coverage gap の調達

| gap | Phase 2 優先 | 将来 option |
| --- | --- | --- |
| elderly male | 許諾を一次情報で確定できる public corpus を再調査 | 契約実録 |
| child / teen male、neutral child | whitelist synthetic 候補 | 成人 actor の child persona |
| machine / creature / spirit 12 役 | whitelist synthetic 候補 | 成人 actor の nonhuman persona |

児童本人の収録を前提にしない。公開 URL や研究利用実績だけでは
`tts_reference_inference` と `commercial_generated_output` の根拠にならない。

## synthetic whitelist v1

`synthetic-sources-v1` で `approved` にできる v1 経路は一つだけである。

```text
Qwen3-TTS VoiceDesign
  → generated reference audio
  → exact generated transcript
  → same official model line の Qwen3-TTS Base clone
```

policy は `type: qwen_voice_design_to_base_clone`、
`voice_design_output_use: reference_audio_only`、
`base_clone_input: generated_reference_audio_and_transcript` を固定する。
VoiceDesign / Base の repository ID と 40 桁 revision、公式 documentation URL、
terms review 日を記録する。公式 API は Base clone で reference audio と transcript
を組にし、reusable prompt を作れる
（[Qwen3-TTS 公式 README](https://github.com/QwenLM/Qwen3-TTS/blob/main/README.md)）。

実在人物・声優・既存 character 名を prompt に使わず、生成 input / generation /
selection receipts を SHA-256 で origin に固定する。whitelist は第三者の人格権を
保証しないため、人評で類似性と persona 適合を確認する。

他モデル出力は v1 whitelist に含めない。#174 の Qwen / Irodori run-scoped anchor
も bundle にしない。

## 契約実録

契約収録は `origin.type: commissioned_recording` とし、公開 YAML には
provider の表示名、URL、不透明な contract reference ID、収録日、source hash、
permissions、term、credit、evidence、publication を記録する。

法的氏名、連絡先、契約書本文は公開 YAML、private asset root、site projection に
入れない。契約管理系で contract reference ID から引く。録音 WAV は Phase 2 の
`--reference-assets` root に置き、`publication.audio_access: private` と
`storage.type: private_object` を一致させる。

収録依頼の `requested_rights` は同じ四つの音声権利に
`transcript_redistribution` を加え、値は `requested` / `not_requested` とする。
実際の bundle へ移すときは契約結果を `permitted` / `prohibited` に確定する。
