# 8 adapter の参照入力要件

最終確認日: 2026-07-31
ローカル基準: `main` / `19a435ba2b675022a9f52c5fecf59340fbf642ea`
#177/#174 契約根拠: [監査中間結果](https://github.com/Hitsuki-Ban/gaya-bench/issues/177#issuecomment-5136919267)、
[Director 承認](https://github.com/Hitsuki-Ban/gaya-bench/issues/177#issuecomment-5137204851)、
[role anchor 統合報告](https://github.com/Hitsuki-Ban/gaya-bench/issues/177#issuecomment-5139423342)

## 読み方

- **Hard**: 現 adapter が検証または固定している条件、または上流 API の必須引数。
- **Recommendation**: 公式 README / 実装例の品質推奨。API の受理条件とは限らない。
- **Current**: 上記 commit の repository 実装。
- **Accepted next contract**: #177/#174 で承認・実装中の release 後の正本。main の
  事実と混ぜない。

## 一覧

| adapter / current code | 参照入力の Hard | 公式 Recommendation / API 事実 | Current / accepted next contract | 入出力 sample rate |
| --- | --- | --- | --- | --- |
| AivisSpeech コハク (`pipeline/src/gaya_pipeline/adapters/aivisspeech.py`) | per-clip 参照なし。固定 model UUID、speaker UUID、style ID を `/audio_query` と `/synthesis` に渡す | 公式 Engine は speaker/style を API で選ぶ。[`/speakers`、`/audio_query`、`/synthesis` の説明](https://github.com/Aivis-Project/AivisSpeech-Engine#%E9%9F%B3%E5%A3%B0%E5%90%88%E6%88%90-api-%E3%82%92%E4%BD%BF%E3%81%86)。公式 API の既定出力 rate と current adapter の設定値は同一とは限らない | bundle 非消費。全 role を同じコハクへ固定し、line emotion から 4 style を選ぶ | current adapter が `outputSamplingRate` を明示する realized output は **44.1 kHz**, mono PCM16 |
| Supertonic 3 (`pipeline/src/gaya_pipeline/adapters/supertonic3.py`) | open-weight runtime の固定 voice style JSON。per-clip 参照なし | 公式 open-weight repo は固定声で、公式 clone pipeline は含まない。[Voice Cloning 節](https://github.com/supertone-inc/supertonic#voice-cloning) | bundle 非消費。58 role の明示 preset assignment。Voice Builder / hosted clone は使わない | output **44.1 kHz**, mono PCM16（[公式 SDK 出力](https://github.com/supertone-inc/supertonic#python)） |
| Chatterbox Multilingual V3 (`pipeline/src/gaya_pipeline/adapters/chatterbox.py`) | current adapter は **48 kHz / mono / PCM16 / 10 秒以上**を必須にし、音声だけを `audio_prompt_path` へ渡す。transcript 引数なし | 公式例は `your_10s_ref_clip.wav`。[README usage](https://github.com/resemble-ai/chatterbox#usage)。Multilingual 実装は encoder conditioning を **6 秒**、decoder reference を **10 秒**に固定する（[公式 `mtl_tts.py`](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/mtl_tts.py)）。これは「10 秒未満を上流 API が拒否する」という意味ではない | 現 5 master（10–20 秒）をそのまま使用。transcript は bundle 監査用に保持するが adapter へ渡さない | reference catalog **48 kHz**、runtime 内部 resample、output **24 kHz** |
| CosyVoice 3 (`pipeline/src/gaya_pipeline/adapters/cosyvoice3.py`) | current adapter は **48 kHz / mono / PCM16 / 0 秒超 30 秒以下**。`inference_instruct2(tts_text, instruction, prompt_wav)` を使い、参照 transcript を消費しない | 公式 zero-shot 例は `prompt_speech_16k` と対応する `prompt_text` の組を渡す（[公式 README](https://github.com/FunAudioLLM/CosyVoice#quick-start)）。一方 Instruct2 は instruction + prompt audio の別 API。zero-shot 推奨を Instruct2 の hard requirement とみなさない | bundle の general clip を Instruct2 へ渡す。transcript は provenance 用。公式 zero-shot 経路へ暗黙切替しない | source catalog **48 kHz**、上流 frontend が処理、output **24 kHz** |
| GPT-SoVITS v2ProPlus (`pipeline/src/gaya_pipeline/adapters/gpt_sovits.py`) | master は **48 kHz / mono / PCM16**。bundle ごとの固定 start frame から **厳密に 5.000 秒**を切り出す。参照音声は渡すが、`prompt_text=""`、`prompt_lang=all_ja` として参照 transcript を省略する | 公式実装は参照音声を **3–10 秒**に制限し、通常モードでは prompt text を音素化する（[公式 `inference_webui.py`](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/GPT_SoVITS/inference_webui.py)）。公式 API は空 `prompt_text` も表現できる（[公式 `api_v2.py`](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/api_v2.py)） | current は seed と 5 秒 window を固定し、同じ byte を決定的に再生成。accepted bundle contract は独立した `clips.short_clone` を厳密 5.000 秒で保持する | reference **48 kHz**、native output **32 kHz**, mono PCM16 |
| Irodori-TTS 600M v3 VoiceDesign (`pipeline/src/gaya_pipeline/adapters/irodori_tts.py`) | `ref_wav` と `no_ref` は排他的。参照音声 API に transcript 引数はない。current adapter が受ける明示音声は **48 kHz / mono / PCM16 / 30 秒以下** | 公式 CLI は `--ref-wav`、または VoiceDesign の `--caption --no-ref`。再利用 speaker embedding も別入力で、transcript は要求しない（[公式 Inference](https://github.com/Aratako/Irodori-TTS#inference)） | main は scenario 明示 reference があれば音声、なければ line ごとの no-ref caption。accepted next contract は明示 reference 優先、残りは **scenario + character ごとに人評選択した単一 anchor** と完全 role caption。anchor は bundle ではない | reference / output **48 kHz**, mono PCM16 |
| Qwen3-TTS 12Hz 1.7B (`pipeline/src/gaya_pipeline/adapters/qwen3_tts.py`) | Base clone の通常 mode は `ref_audio` **と正確な `ref_text`** を組で要求する。`x_vector_only_mode` は transcript 不要だが品質低下が公式に明記される | 公式 Base は約 3 秒 clone、同一 prompt は `create_voice_clone_prompt` で一度作って再利用する。[公式 Voice Clone](https://github.com/QwenLM/Qwen3-TTS#voice-clone) | main は `scenario + character + emotion + intensity` ごとに VoiceDesign → Base clone を行い、scenario 明示 reference を消費しない。accepted #174 contract は (a) 明示 bundle audio + transcript、または (b) **scenario + character ごとに 1 件だけ人評選択した anchor audio + 生成時の正確な transcript**。旧 emotion bank cache は再利用しない。anchor は bundle ではない | model return を検証し、現 realized output は **24 kHz**。bundle master は 48 kHz、materialize 時に audio+text pair を固定 |
| VoxCPM2 (`pipeline/src/gaya_pipeline/adapters/voxcpm2.py`) | clone API は `reference_wav_path` を受け transcript を要求しない。current adapter は明示 reference を **48 kHz / mono / PCM16 / 10–20 秒**、output を **48 kHz / mono / PCM16**で検証 | 公式は reference なしの Voice Design と reference audio を使う cloning を別 mode として提示（[公式 repo](https://github.com/OpenBMB/VoxCPM)、[公式 app の 3 mode 説明](https://github.com/OpenBMB/VoxCPM/blob/main/app.py)）。VoxCPM2 の audio VAE は 16 kHz で参照を encode し、48 kHz を出力する（[公式 VoxCPM2 docs](https://voxcpm.readthedocs.io/en/latest/models/voxcpm2.html)） | scenario 明示 bundle があればそれを使用。なければ scenario + character の完全 role instruction から design reference を 1 件作り、同役の全 line に使う。design reference は runtime cache であり bundle ではない | catalog reference / output **48 kHz**, mono PCM16。model 内部 reference encode は **16 kHz** |

## Phase 2 materialization 規則

以下は承認後に実装する Phase 2 gate であり、Phase 1 の実装済み機能ではない。
canonical bundle clip はすべて PCM16 / 48 kHz / mono とし、`general` は
10–20 秒、`short_clone` は厳密に 5.000 秒とする。assignment は adapter
別ではないため、両 clip を全 production bundle の必須入力とする。

| adapter | bundle からの派生 |
| --- | --- |
| AivisSpeech / Supertonic 3 | 生成しない。bundle assignment が来た場合は未消費を明示し、参照ありと記録しない |
| Chatterbox | `general` clip。48 kHz mono PCM16、10 秒以上。transcript は receipt に hash だけ残し API へ渡さない |
| CosyVoice3 | `general` clip。48 kHz mono PCM16、30 秒以下。Instruct2 に transcript を渡さない |
| GPT-SoVITS | 独立した `short_clone` を exact 5.000 秒で使用する。`general` から任意位置を推測・切り出ししない |
| Irodori | 明示 bundle では `general` clip。transcript は API 非消費だが catalog に必須 |
| Qwen | `general` clip と完全一致 transcript を pair にして clone prompt を作る。`x_vector_only_mode` は使わない |
| VoxCPM2 | 明示 bundle では `general` clip。明示がなければ adapter の design reference を使う |

## 収録尺の帰結

単一 clip で全 adapter の hard condition を満たそうとしない。current Chatterbox は
10 秒以上、GPT-SoVITS 用 clip は厳密 5 秒であり、安全な同一尺がない。したがって
契約収録は `general` と `short_clone` を別 take / 別 transcript として納品する。
10 秒素材の先頭 5 秒を無言区間や語中で機械切断して代用しない。具体例は
[収録依頼例](recording-request-example.md)を参照する。
