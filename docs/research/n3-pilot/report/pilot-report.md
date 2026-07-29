# N3 pilot 校正レポート

- pilot set SHA-256: `ef588224df5e46495d4bcc5841c2bcdf5eb69b166a38b750af7dfe8035fc6cac`
- decision SHA-256: `98421e2875fe27a87407f24feb10bd2f63bedfefa20d9ffd519b4075afe76dcb`
- 適用範囲: 24-line exploratory
- production scorer: **no-go without independent confirmation**
- ASR は feature ranking に使用していない。
- N5 方針:
  - `qwen3-tts-12hz-1.7b`: maintain_n3 — no paired take4/5 data
  - `irodori-tts-600m-v3-voicedesign`: maintain_n3 — no paired take4/5 data
  - `voxcpm2`: maintain_n3 — no paired take4/5 data

## 自動 gate × 人評 adoptable の raw 集計

| 自動 gate | 人評 adoptable | 人評 not adoptable |
| --- | ---: | ---: |
| eligible | 48 | 36 |
| rejected | 49 | 83 |

## 自動 gate × content correct の raw 集計

| 自動 gate | content correct | content incorrect |
| --- | ---: | ---: |
| eligible | 82 | 2 |
| rejected | 77 | 55 |

## 人評の選択結果

- group: 72 (skip 22)
- 選択: 50 (adoptable 47)
- adoptable candidate: 97
- gate が失った winner: 28 / 50 (0.560)

## rule 別 false reject

| rule | reject | content FRR | content 比率 | adoptable FRR | adoptable 比率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| mechanical_audio | 0 | 0 (0.000) | n/a | 0 (0.000) | n/a |
| active_speech_nonpositive | 0 | 0 (0.000) | n/a | 0 (0.000) | n/a |
| explicit_reading_mismatch | 132 | 77 (0.484) | 0.583 | 49 (0.505) | 0.371 |

## model × rule reject

| model | rule | reject | content FRR | content 比率 | adoptable FRR | adoptable 比率 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| qwen3-tts-12hz-1.7b | mechanical_audio | 0 | 0 (0.000) | n/a | 0 (0.000) | n/a |
| qwen3-tts-12hz-1.7b | active_speech_nonpositive | 0 | 0 (0.000) | n/a | 0 (0.000) | n/a |
| qwen3-tts-12hz-1.7b | explicit_reading_mismatch | 37 | 20 (0.370) | 0.541 | 20 (0.417) | 0.541 |
| irodori-tts-600m-v3-voicedesign | mechanical_audio | 0 | 0 (0.000) | n/a | 0 (0.000) | n/a |
| irodori-tts-600m-v3-voicedesign | active_speech_nonpositive | 0 | 0 (0.000) | n/a | 0 (0.000) | n/a |
| irodori-tts-600m-v3-voicedesign | explicit_reading_mismatch | 44 | 30 (0.526) | 0.682 | 18 (0.581) | 0.409 |
| voxcpm2 | mechanical_audio | 0 | 0 (0.000) | n/a | 0 (0.000) | n/a |
| voxcpm2 | active_speech_nonpositive | 0 | 0 (0.000) | n/a | 0 (0.000) | n/a |
| voxcpm2 | explicit_reading_mismatch | 51 | 27 (0.562) | 0.529 | 11 (0.611) | 0.216 |

## eligible-only 単一 feature LOLO

| feature | Hit@1 | random@1 | Hit@2 | random@2 | group |
| --- | ---: | ---: | ---: | ---: | ---: |
| duration_sec | 0.182 | 0.386 | 0.773 | 0.773 | 22 |
| mora_per_second | 0.273 | 0.386 | 0.727 | 0.773 | 22 |
| pause_sec | 0.455 | 0.386 | 0.727 | 0.773 | 22 |
| voiced_ratio | 0.500 | 0.386 | 0.864 | 0.773 | 22 |
| f0_semitone_std | 0.429 | 0.389 | 0.857 | 0.778 | 21 |
| energy_median_dbfs | 0.500 | 0.386 | 0.864 | 0.773 | 22 |

方向は各 leave-one-line-out fold の training lines だけで選択した。
同率時は ascending を事前規定の tie-break とした。
