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
| CosyVoice 3 0.5B 2512 | `20260729T101833764905Z-cosyvoice3-0.5b-2512-n1` | 12 | 12 | 0 |
| GPT-SoVITS v2ProPlus | `20260729T102548151791Z-gpt-sovits-v2-pro-plus-n1` | 12 | 12 | 0 |
| Supertonic 3 | `20260729T102712740366Z-supertonic-3-n1` | 12 | 12 | 0 |
| VoxCPM2 | `20260729T103145027400Z-voxcpm2-n1` | 12 | 12 | 0 |
| Qwen3-TTS 12Hz 1.7B | `20260729T113009679952Z-qwen3-tts-12hz-1.7b-n1` | 160 | 160 | 0 |

合計 381 group を生成し、generation failure は 0。

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
  `74b32a994f10a8738d1c2d3547f409522fb97573ccccfebaf17ba8095677dfde`
- baseline reference SHA-256:
  `7b019148b5431e9cce9337e774e6f8a0e7d9a2931e213005d0e2e8af25cdaf71`
- group: 381
- candidate: 381
- candidate zero: 0

全 381 group の人評と finalize 結果は完了後に追記する。
