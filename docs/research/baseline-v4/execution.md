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
  `2f55a3a8145bf87c375dd70384464a2aecca922a47b55eeac99a2504bcfd4f7a`
- baseline reference SHA-256:
  `e0f5f7560dd8d8bf051e9e0d65c8201236548862fb98d829f040433eee5e6823`
- bundle inventory SHA-256:
  `29a01d73971008f26383fb934e442d8f8cd0f59787df27e7b911a3a4ed695319`
- group: 381
- candidate: 220
- candidate zero: 161

Issue #124でDummy Beepが1.2秒のtest-only adapter出力であり、TTS策展対象では
ないことを明文化した。7 source runの完全性検証後、Dummy 161 groupはaggregate
candidateから`reason=test_only_adapter` failureへ投影する。Dummy source runの
161 eligible candidate、ledger、QC、sidecar、WAV、Opusは
`source-runs/dummy/**`へ証拠として保持し、top level candidate audioには複製しない。
全Dummy groupのeligible source candidateを投影条件とし、欠落またはsource failureは
policy exclusionへ読み替えずassembleを失敗させる。新bundleは
`artifacts/baseline-v4/curation-no-dummy`に保存した。

漏洩修正前の bundle は candidate set
`74b32a994f10a8738d1c2d3547f409522fb97573ccccfebaf17ba8095677dfde`
として監査用に退避した。15 group分の旧 draft（selected 8 / skipped 7）も
SHA-256
`1f3ed1b7181084eaeb9c93274a3559f4661c9a38be27f721e538b0666140c363`
で保存したが、新 candidate / reference SHA へは移行しない。策展は新 bundle
に対して先頭から再開する。

Dummy除外前のbrowser draftは25 group判断済みであり、独立backup
`artifacts/baseline-v4/draft-backups/baseline-curation-partial-25-groups.json`
をSHA-256
`7b68c459f1ddae6e29105a0a801ccd8d898afd0c52da016a2b13797dd69445a8`
で保存した。旧・新candidate set間で全220 real candidateのcanonical recordが
byte一致し、判断済み24 real groupのgroup tuple、take ID、path、audio SHA、
rubric、decisionが一致することを検証して明示的にrebindした。Dummyのskip 1件は
策展対象外なので移行しない。復元draft
`baseline-curation-real-24-restored.json`のSHA-256は
`6d1a999290a2e83518cc14e7905b7431aea4d6fad24025154803a65156aaf2e4`
で、selected 20 / skipped 4 / undecided 196である。

## 最終策展と release candidate

Dummyを除く220 candidate groupの人評とskip復聴を完了した。最終decisionは
selected 166、skipped 54、uncurated 0である。skip復聴では、初回に
`content_correct=true && adoptable=false`だった29 groupを独立queueで再確認し、
5 groupをselectedへ変更、6 groupを厳密な内容・日本語音調の誤りとして
`content_correct=false`へ変更した。残る18 groupは内容が正しくても総合品質が
採用水準に届かないためskipを維持した。

`baseline finalize`は381 groupを次のexact countで確定した。

- candidate zero: 161
- selected: 166
- skipped: 54
- uncurated: 0

固定したrelease metadataは
[`release/`](release/)に置く。音声binary、source-run evidence、bundle inventoryは
含めず、Ticket GのR2 uploadと`data/manifest.json`切替もまだ行わない。

| artifact | SHA-256 |
| --- | --- |
| baseline plan | `2442095c32ae1ebf660ebaa56fc45939b528f29b18fed5883c239e91d12eb60f` |
| candidate set | `2f55a3a8145bf87c375dd70384464a2aecca922a47b55eeac99a2504bcfd4f7a` |
| baseline reference | `e0f5f7560dd8d8bf051e9e0d65c8201236548862fb98d829f040433eee5e6823` |
| decision | `43e32494051436a47130e8055e5e4d216bbf2f907a620cca5f830bbebcbeedd4` |
| release manifest v4 | `c98d1666dc00fc10ef2e6fb0a8a5750234739ce6827bc50ea16f99954e2de985` |
| audit | `587592610e0f8ed64d12c1fad097a914801a55e09d382998c830bdb5de60e644` |
| provenance | `962635b1110dd724b1a417858df7a2832285efeb7f247daa7a8d3dd5ef6aea6e` |

ローカルreleaseのcanonical inventoryは1792 fileを閉包し、全fileのSHA-256を
再計算して不一致0件を確認した。
