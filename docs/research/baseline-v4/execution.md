# 公開 baseline v4 実行記録

## 固定入力

- 実行日: 2026-07-29
- source v3 manifest SHA-256:
  `c68742f5e0a49a7f0644df7cb5a194c5836434b34849243cef53f4828ceb4a16`
- baseline plan SHA-256:
  `2442095c32ae1ebf660ebaa56fc45939b528f29b18fed5883c239e91d12eb60f`
- plan group: 381
- model: 7
- excluded historical failure: 1
- take: N=1
- seed base: 104

初回 plan SHA-256 は
`2fb344673efcc1389aac425f497bb5bd035dae3f118141ff7f78b4dd6c04eaca`
だったが、旧 v3 manifest の model metadata を生成時の authority としていたため廃止した。
修正後は旧 v3 の raw SHA、381 group、legacy path / SHA、scenario SHA、除外 failure
を維持し、7 model の metadata だけを current adapter profile に固定する。既存 run は
各 ledger / manifest-v4 が同じ exact group、scenario、N=1 recipe、current model
profile を固定しており、plan 自体の SHA を生成入力に含めないため再生成せず再利用した。

## generation run

| model | run id | group | generated | failure |
| --- | --- | ---: | ---: | ---: |
| Dummy | `20260729T101012162646Z-dummy-n1` | 161 | 161 | 0 |
| Chatterbox Multilingual V3 | `20260729T101318586626Z-chatterbox-multilingual-v3-n1` | 12 | 12 | 0 |
| CosyVoice 3 0.5B 2512 | `20260729T140956320980Z-cosyvoice3-0.5b-2512-n1` | 12 | 12 | 0 |
| GPT-SoVITS v2ProPlus | `20260729T102548151791Z-gpt-sovits-v2-pro-plus-n1` | 12 | 12 | 0 |
| Supertonic 3 | `20260729T102712740366Z-supertonic-3-n1` | 12 | 12 | 0 |
| VoxCPM2 | `20260729T103145027400Z-voxcpm2-n1` | 12 | 12 | 0 |
| Qwen3-TTS 12Hz 1.7B | `20260729T113009679952Z-qwen3-tts-12hz-1.7b-n1` | 160 | 160 | 0 |

合計 381 group を生成し、generation failure は 0。

旧 CosyVoice run
`20260729T101833764905Z-cosyvoice3-0.5b-2512-n1` は、実策展で12件中
11件に自由記述 `delivery` 由来の前置発話が確認されたため廃止した。
Issue #120 / PR #121 で model prompt を `fixed-emotion-template-v1` の短い固定
emotion template に限定し、同じ plan / seed base から12 groupを再生成した。
新 run は12件すべて機械 gateを通り、Kana ASRと代表3件の人手 spot checkで
指示文の朗読がないことを確認した。通常の読み・アクセント判断は引き続き
人評に委ねる。

## QC

| model | eligible | hard reject | blocked | generation failure | content review required |
| --- | ---: | ---: | ---: | ---: | ---: |
| Dummy | 161 | 0 | 0 | 0 | 161 |
| Chatterbox Multilingual V3 | 12 | 0 | 0 | 0 | 12 |
| CosyVoice 3 0.5B 2512 | 12 | 0 | 0 | 0 | 12 |
| GPT-SoVITS v2ProPlus | 12 | 0 | 0 | 0 | 12 |
| Supertonic 3 | 12 | 0 | 0 | 0 | 12 |
| VoxCPM2 | 12 | 0 | 0 | 0 | 12 |
| Qwen3-TTS 12Hz 1.7B | 160 | 0 | 0 | 0 | 148 |
| **total** | **381** | **0** | **0** | **0** | **369** |

`content review required` は soft flag であり、人評結果を代替しない。

## assemble

- candidate set SHA-256:
  `7895681eef7da4efda006b3e832271fbc151584d7acfccdcb62012fd8df34c10`
- baseline reference SHA-256:
  `03d637065f436cd670ee95c1fc6e5d83dc73074ad78b6c5b68aca5ced9e932bd`
- bundle inventory SHA-256:
  `ee6e7da1b2e57d0225f28bd3173bc44497cd6e69010fcbd90794ca88e8e33dfe`
- group: 381
- candidate: 381
- candidate zero: 0

漏洩修正前の bundle は candidate set
`74b32a994f10a8738d1c2d3547f409522fb97573ccccfebaf17ba8095677dfde`
として監査用に退避した。15 group分の旧 draft（selected 8 / skipped 7）も
SHA-256
`1f3ed1b7181084eaeb9c93274a3559f4661c9a38be27f721e538b0666140c363`
で保存したが、新 candidate / reference SHA へは移行しない。策展は新 bundle
に対して先頭から再開する。

全 381 group の人評と finalize 結果は完了後に追記する。
