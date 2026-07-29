# N=3 pilot 実行記録

## 固定条件

- 実行日: 2026-07-29
- line: `battlefield-camp` 12 行 + `dungeon-entrance` 12 行
- model: Qwen3-TTS 1.7B / Irodori-TTS 600M v3 VoiceDesign / VoxCPM2
- take: model × line ごとに N=3、合計 72 group / 216 candidate
- seed base: `103`
- pilot set SHA-256:
  `ef588224df5e46495d4bcc5841c2bcdf5eb69b166a38b750af7dfe8035fc6cac`
- raw decision SHA-256:
  `98421e2875fe27a87407f24feb10bd2f63bedfefa20d9ffd519b4075afe76dcb`

## 実行時間と gate 結果

wall time は実行時の PowerShell 計測値、generation internal は 216 sidecar の
`generation_seconds` 合計である。

| model | scenario | run id | generation wall | QC wall | eligible | hard reject |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Qwen3-TTS 1.7B | battlefield-camp | `20260729T024612669791Z-qwen3-tts-12hz-1.7b-n3` | 1009.861 s | 187.979 s | 26 | 10 |
| Qwen3-TTS 1.7B | dungeon-entrance | `20260729T030349609652Z-qwen3-tts-12hz-1.7b-n3` | 941.141 s | 84.993 s | 9 | 27 |
| Irodori-TTS 600M v3 | battlefield-camp | `20260729T031520295238Z-irodori-tts-600m-v3-voicedesign-n3` | 252.968 s | 264.279 s | 19 | 17 |
| Irodori-TTS 600M v3 | dungeon-entrance | `20260729T031838559062Z-irodori-tts-600m-v3-voicedesign-n3` | 164.391 s | 109.113 s | 9 | 27 |
| VoxCPM2 | battlefield-camp | `20260729T032511564969Z-voxcpm2-n3` | 767.510 s | 115.250 s | 13 | 23 |
| VoxCPM2 | dungeon-entrance | `20260729T033625485860Z-voxcpm2-n3` | 612.233 s | 99.938 s | 8 | 28 |
| **合計** | | | **3748.104 s** | **861.552 s** | **84** | **132** |

- generation internal 合計: 2505.116 s
- blind bundle build wall: 6.429 s
- `gaya pilot analyze` wall: 2.3 s
- generation failure / blocked attempt: 0

## 容量と試聴量

| 対象 | file 数 | byte |
| --- | ---: | ---: |
| 6 run directory の全 local artifact | 684 | 85,907,393 |
| blind bundle 全体 | 217 | 6,720,045 |
| eligible Opus の公開容量見積もり | 84 | 2,350,576 |

- 216 candidate の音声尺合計: 782.881 s（13.048 分）
- 人評結果: 72 group 中 50 selected / 22 skipped、216 rubric 完了
- pilot UI v1 は再生 click 数と実時間を保存しないため、実際の再試聴回数は推測しない。
  比較可能な workload として candidate 数と音声尺を記録する。
- `selected` は group 内の相対的な winner で、絶対的な合格を意味しない。
  `content_correct` は厳密な日本語の音調・アクセントを含み、`adoptable` は感情、
  役としての自然さ、音質などの総合的な利用可能性として独立に評価した。
  このため `content_correct=false && adoptable=true` や非 adoptable winner は
  owner の意図した有効な評価であり、raw decision と集計を変更しない。

## 校正判断

1. `explicit_reading_mismatch` を production hard reject に維持しない。
   content-correct FRR は 77/159（0.484）、adoptable FRR は
   49/97（0.505）、人評 winner の喪失は 28/50（0.560）だった。
2. 同 rule は bad-content recall 55/57（0.965）だが reject precision は
   55/132（0.417）であり、hard reject ではなく review / soft signal
   として扱う変更を #110 に分離する。
3. production scorer は導入しない。eligible-only LOLO の最良 Hit@1 は
   `voiced_ratio` と `energy_median_dbfs` の 0.500（random 0.386）だが、
   対象は 22 group の探索的 owner calibration で、独立確認 batch がない。
4. Qwen3-TTS / Irodori-TTS / VoxCPM2 はすべて N=3 を維持する。
   paired take 4/5 がないため N=5 の追加生成を正当化しない。

raw decision と機械可読な全 fold / 2×2 / rule 別結果は
[`pilot-decision.json`](pilot-decision.json) と
[`report/pilot-report.json`](report/pilot-report.json) を正とする。
