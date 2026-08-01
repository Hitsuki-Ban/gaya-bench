# 役柄 conditioning / 連続生成監査

監査日: 2026-07-31
対象 Issue: [#177](https://github.com/Hitsuki-Ban/gaya-bench/issues/177)

## 結論

受付嬢が男性声になる主因はシナリオの誤記ではない。`guild-hall/receptionist`
は `female / young_adult / 受付` と定義され、`lux-emotion-76` も同じ
`female / young_adult` の実音声である。

役柄 conditioning で確認できた実装上の欠陥は次の3点である。

1. Qwen3-TTS は `reference_voice` を捨て、gender / age / archetype / name を含まない
   VoiceDesign を感情・強度ごとに作っていた。既存 #142 blind canary でも、gender / age
   を渡さない若い女性役は3/3で男性と判定され、感情ごとの reference は同一話者性も
   失っていた。
2. Irodori-TTS の no-reference caption は voice / emotion / delivery だけで、
   gender / age / archetype / name / personality / scene を渡していなかった。
   さらに台詞ごとに no-reference 生成していたため、同一役の話者を固定する入力がなかった。
3. Chatterbox / CosyVoice / GPT-SoVITS 共通の clone assignment で、男性4役
   12行が女性 teen reference へ割り当てられていた。

このため Qwen は「シナリオの明示 reference、なければ人評で確定した役別
VoiceDesign anchor」、Irodori は「明示 reference、なければ人評で確定した役別
no-reference anchor」へ直し、frozen completion plan に拘束された同じ anchor を
同一役の全台詞で再利用する。clone assignment の4件は、現在の素材に男性 child /
teen がないため、年齢近似より binary gender の一致を優先して成人男性
`hadou-emotion-11` に直す。これは年齢まで exact になったという意味ではない。

同じ手法で日本語 reading も全量監査した結果、AivisSpeech、GPT-SoVITS、
Irodori-TTS、Supertonic 3、VoxCPM2 の5実装がモデル固有の入力仕様と一致して
いなかった。詳細と再生成境界は後述する。

## Source truth の全量監査

[`source-audit.json`](source-audit.json) は schema 検証済みの全15シナリオを展開し、
scenario file SHA、58役の全 role field、161行の所属、5参照素材の SHA、および
clone reference の対応を記録する。さらに8モデル×161行=1,288件を逐条展開し、
各行の role truth、adapter が実際に使う speaker / preset / reference / prompt、
伝達可能・unsupported な field、モデルへ渡る原文と reading、入力 identity、
adapter source SHA を記録する。
公開中 `data/manifest.json` の1,282 candidate / 6 failure についても、sidecar 由来の
`generation_input_sha256`、take/audio SHA、requested/realized params SHA と conditioning
evidence を同じ行で対照する。生成コマンドは次のとおり。

音声バイナリを持たない CI でも production adapter の `prepare()` と
`generation_input()` を通せるよう、canonical metadata と同じ長さ・形式の決定的な
一時 WAV を5素材分だけ生成する。receipt の `reference.sha256` /
`reference.source_sha256` は公開物と比較する canonical SHA、
`reference.prepare_state_sha256` は adapter が実際に受け取った clip / anchor SHA、
`reference.audit_fixture_source_sha256` は一時 WAV の source SHA である。fixture は
監査終了時に削除され、公開素材や生成物として扱わない。

Qwen / Irodori の `reference_voice=null` 53役については、repository の
`docs/research/full-baseline-completion/plan.json` を pre-hearing の固定 v1 source plan
として専用 loader で検証し、2モデル×53役の deterministic PCM anchor、canonical
`role-anchor-selection-v1`、隣接 SHA marker を一時領域へ構築する。synthetic selection
の実 SHA が確定した後だけ、正式 builder / loader で audit-only v2 completion plan を
同じ一時領域へ作る。repository の正式 plan は書き換えず、owner decision の
export / finalize 前に production v2 authority が存在するとは記録しない。各選択は正式
`resolve_selected_anchor()` で source plan SHA、model revision、完全 role identity、
review/selected role epoch、decision/audio SHA を再検証してから production adapter の
`prepare()` へ渡す。
各 receipt の `reference.selected_anchor` は `generation_input()` が返した実値であり、
selection / marker / WAV / epoch のいずれを改変しても監査は fail-fast する。

```powershell
uv run --project pipeline --locked --no-sync python -m gaya_pipeline.role_conditioning_audit `
  --output docs/research/role-conditioning-audit/source-audit.json
```

監査結果:

| 項目 | 件数 |
| --- | ---: |
| scenario | 15 |
| character | 58 |
| line | 161 |
| scenario が明示する reference | 5役 |
| adapter assignment が必要な役 | 53役 |
| 全58参照: 非 neutral で gender が一致 | 51役 |
| 全58参照: neutral のため binary reference では表現不能 | 7役 |
| 全58参照: age が一致 / 近似 | 21役 / 37役 |
| 全58参照: gender と age がともに一致 | 18役 |
| assignment 53参照: 非 neutral で gender が一致 | 46役 |
| assignment 53参照: neutral のため表現不能 | 7役 |
| assignment 53参照: age が一致 / 近似 | 16役 / 37役 |
| assignment 53参照: gender と age がともに一致 | 13役 |
| conditioning receipt | 1,288件 |
| reading receipt | 1,288件 |
| 実`adapter.generate()` runtime transport probe | 1,288件（8モデル×161行） |
| `line.reading` 明示行 | 25行 |
| 明示 reading receipt: 適用 / 非対応 | 50件 / 150件 |
| 未指定行: 原文入力 / CosyVoice 用自動かな変換 | 952件 / 136件 |
| 公開 candidate / failure | 1,282件 / 6件 |
| 公開 conditioning evidence が現行 source と一致 | 780件 |
| 再生成前のため現行 source と不一致 | 357件 |
| VoxCPM2 role-design identity が公開 sidecar では検証不能 | 145件 |
| source field / reference の破損 | 0 |

5件の scenario 明示 reference は gender / age ともすべて exact である。
公開不一致357件は、Irodori 161件、Qwen candidate 160件、clone assignment を修正した
3モデル×12件である。Qwen の残る1件は公開 failure であり candidate provenance がない。
VoxCPM2 の145件は source 側では role-design identity を検証できるが、公開 manifest に
保存された realized sidecar evidence がその identity 本体を含まないため、推測で
`match` とせず `unverifiable` とした。これらを committed snapshot に隠さず記録し、
再生成・公開 provenance 更新後に解消する。
`source-audit.json` SHA-256 は
`e83e66f2a97d98129a36a63e3c253d5e8e13e23ef760b6ccff0155404c862bdf`。

## 日本語 reading の全量監査

`line.text` / `line.reading`、adapter が宣言する `capabilities.reading`、実際の
`generation_input()`を8モデル×161行で照合し、同じ1,288件をすべて実
`adapter.generate()`へ通して、capture runtimeが受け取った引数を照合した。監査側で
別の読み変換を再実装せず、production adapter が作るpayload本体と実生成呼び出しを
比較する。AivisSpeechのsurface / reading / speaker / intonation / tempo、CosyVoiceの
TTS text / instruction、Irodoriのtext / caption、Qwen3-TTSのtext / language /
clone prompt、残るadapter固有のstyle・samplingを含む実引数をexact-matchする。
AivisSpeechとCosyVoiceだけが外部readingを適用し、残る6モデルは原文を保持する。
`generation_input()`だけ正しく`generate()`で値や補助文脈を置換する改変もfail-fastする。

| model | production の日本語入力 | `line.reading` |
| --- | --- | --- |
| AivisSpeech Kohaku | 原文で `/audio_query` を作り、指定時だけ読みから作った `accent_phrases` を置換 | 対応 |
| Chatterbox multilingual v3 | 原文と `language_id=ja` | 非対応 |
| CosyVoice3 | 指定時は `line.reading`、未指定時は `pyopenjtalk.g2p(kana=True)` を `tts_text` へ渡す | 対応 |
| GPT-SoVITS | 原文と日本語 language mode | 非対応 |
| Irodori-TTS | emotion emoji と原文 | 非対応 |
| Qwen3-TTS | 原文と `language=Japanese` | 非対応 |
| Supertonic 3 | 原文と `lang=ja` | 非対応 |
| VoxCPM2 | control prefix と原文、`normalize=False` | 非対応 |

修正前は、AivisSpeech が `line.reading` を原文欄へ直接入れ、GPT-SoVITS と
Supertonic も明示 reading を原文の代わりに使っていた。Irodori-TTS と VoxCPM2 は
さらに reading 未指定の136行まで `pyopenjtalk` で片仮名化していた。加えて旧 source
audit は Irodori / Vox の converter を `lambda text: text` に差し替えていたため、
production と異なる272件を正しい入力として記録していた。これを削除し、監査が
production payload と違えば fail fast するようにした。

reading 修正だけで音声入力が変わるのは397件である。内訳は AivisSpeech 25、
GPT-SoVITS 25、Irodori-TTS 161、Supertonic 25、VoxCPM2 161。Chatterbox と Qwen は
従来から原文、CosyVoice は従来から正しいかな入力だったため reading 起因の再生成は
不要である。

## モデル別の実入力

| model | 役柄 identity の実入力 | 判定 |
| --- | --- | --- |
| AivisSpeech Kohaku | 固定 speaker Kohaku と emotion 別 style | 任意役柄は unsupported |
| Chatterbox multilingual v3 | target text、毎回の reference WAV、intensity | 参照声以外の役柄 text は unsupported |
| CosyVoice3 | target text、毎回の reference WAV、emotion instruction | 参照声以外の役柄 text は unsupported |
| GPT-SoVITS | target text、毎回の reference WAV | 参照声以外の役柄 text は unsupported |
| Irodori-TTS | scenario reference または frozen selection の人評済み役別 anchor、完全な role / scene / delivery caption | 修正対象 |
| Qwen3-TTS | scenario reference または frozen selection の人評済み VoiceDesign anchor | 修正対象 |
| Supertonic 3 | 10 built-in preset の明示 assignment | gender は対応、年齢・役柄 text は unsupported |
| VoxCPM2 | reference または gender / age / archetype / voice / personality の役別設計 | 現行経路は適正 |

固定 speaker / preset や clone-only のモデルへ、存在しない自然言語 role API を渡したとは
記録しない。現在の5素材では37役の年齢と7役の neutral gender を exact に表現できないため、
これらは入力不具合ではなく reference coverage / model capability の制約として公開時に
明示する。

## 連続生成汚染

コード監査と順序 canary では、前の役の reference を暗黙に使う経路は確認できなかった。

- Chatterbox は各 call で `audio_prompt_path` を渡し、upstream が conditionals を再抽出する。
- CosyVoice は空の `zero_shot_spk_id` と現在の reference WAV を毎回渡すため、登録済み
  speaker ID の古い prompt を混ぜる既知 #1400 経路には入らない。
- GPT-SoVITS / Irodori / Qwen / VoxCPM2 も各 call の reference または役別 key を明示する。
- Chatterbox は8個の独立 process
  （isolated A/B×2、forward、reverse、A-B-A、B-A-B）で受付嬢と
  ベテラン冒険者を各7回生成し、clean-process matrix の verdict は `pass`、
  generation input と WAV は byte-identical だった。従来の4対象順序比較も
  byte-identical だった。
- Qwen は同じ8-process matrix を2組実行し、受付嬢 / ベテラン冒険者に加えて
  高齢男性の案内人 / child・neutral の精霊を各7回生成した。両 matrix の verdict は
  `pass` で、generation input、役別 reference、WAV はすべて一致した。
- CosyVoice の forward / reverse は対象ごとに byte-identical だった。一方、同一
  input / seed の独立反復自体に実質的な波形差があり、PCM PSNR は18.4〜22.2 dB、
  受付嬢の isolated A1/A2 は18.83 dB、A-B-A の先頭/末尾も18.77 dBだった。
  したがって差を173 dB級の数値ノイズとは扱わない。ただし、役を挟まない独立反復にも
  同規模の差があり順序との対応がない。8-process matrix でも、同じ prepare 対象・
  同じ先頭位置で前の生成がない forward A1 / A-B-A A1 が20.74 dBだけ異なり、
  その後の A-B-A A3 は baseline に戻った。等価 topology 自体が安定しないため
  aggregate verdict は受付嬢が `review`、ベテラン冒険者が `pass` である。
  現証拠は前の役による汚染を支持せず、CosyVoice は人工の話者 identity 確認を要する。
- Irodori も同じ2組の8-process matrix で4対象を各7回生成した。
  明示 Lux reference、役別 anchor、高齢男性、人外 neutral/child の全対照で
  generation input、reference、WAV が byte-identical となり、両 verdict は
  `pass` だった。

ローカル immutable report の SHA-256:

| model | 対照 | verdict | report SHA-256 |
| --- | --- | --- | --- |
| Qwen | 受付嬢 / ベテラン冒険者 | pass | `a9efbc74efb53f5ed2ef87c9bd6b5e6b2b2abd13e407b90c8525d02be47c97d9` |
| Qwen | 高齢案内人 / 精霊 | pass | `937448f205c715b549fa7a1d46c2f132d61353ee7b1fc3040445c4f8823b10e3` |
| Irodori | 受付嬢 / ベテラン冒険者 | pass | `d5fb3a4747161736554455db5ae4aa397d71390d1551a982322cf8981f72a62d` |
| Irodori | 高齢案内人 / 精霊 | pass | `be1e201780fc72140bd35be60ef733a89ae65558e47471f54523d8f74fbd3b5c` |
| Chatterbox | 受付嬢 / ベテラン冒険者 | pass | `c683f4522b28090505412f412870ac7e5d8169e2c7e54879a28285aac7f3f3b9` |
| CosyVoice | 受付嬢 / ベテラン冒険者 | review | `12ef018c7e78e02356d5ae731da018a9e9c8609d68561d42652d7d6c6dc456ad` |

CosyVoice の canonical artifact は
`artifacts/issue-177/contamination/cosyvoice3-0.5b-2512/matrix-v2-final-env/report-topology-v2.json`
（SHA-256:
`12ef018c7e78e02356d5ae731da018a9e9c8609d68561d42652d7d6c6dc456ad`）
だけである。同じディレクトリの旧
`artifacts/issue-177/contamination/cosyvoice3-0.5b-2512/matrix-v2-final-env/report.json`
（SHA-256:
`d648580fa6955d7710f10fc5a7dfcc6efe93c4e637e1ea3c1194c522dbb60989`）
は topology-v2 再評価で superseded されており、verdict の根拠として使用してはならない。

実音声の確認では、対照的な女性・男性・高齢・child/neutral の canary を使い、
clean-process 単独生成、通常順、逆順、A-B-A 挿入順を同じ seed で比較する。
波形 hash の差だけで汚染とは判定せず、同じ条件の単独反復を non-determinism 基準にして、
話者 identity / gender / age / leakage の人評差が順序と対応した場合だけ汚染とする。

## 再生成境界

役柄conditioningとreading修正のunion 594件に、同じmodel内でまだunionに含まれない
旧generation failure 3件を加えたPhase B replacementは597件である。

- Qwen3-TTS / Irodori-TTS / VoxCPM2: 各161行を N>=3。Qwen / Irodori は役柄、
  Irodori / Vox は reading 入力が全行で変わる。
- GPT-SoVITS: role assignment 12行と reading 明示25行に重複がないため37行を N>=3。
- Chatterbox: role assignment 12行と旧failure 1行の13行を N>=3。
- CosyVoice: role assignment 12行と旧failure 2行の14行を N>=3。
- AivisSpeech / Supertonic: reading 明示25行を各モデルで再生成する。AivisSpeech は
  deterministic single-take engine のため各行1件、Supertonic は N>=3。

全候補は機械 QC 後、gender、age、archetype、同一役の跨行 identity、日本語 reading /
pitch accent、delivery、prompt / reference leakage を明示した画面で選ぶ。production は
選択完了前に部分更新せず、manifest / selection / audio を一括で置換する。

## 公式仕様との照合

- [Qwen3-TTS: Voice Design then Clone](https://github.com/QwenLM/Qwen3-TTS/blob/main/README.md#voice-design-then-clone)
- [Irodori-TTS v3 VoiceDesign model card](https://huggingface.co/Aratako/Irodori-TTS-600M-v3-VoiceDesign)
- [Chatterbox upstream `audio_prompt_path` conditionals](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/tts.py)
- [CosyVoice #1400 `zero_shot_spk_id` contamination condition](https://github.com/QwenAudio/CosyVoice/issues/1400)
- [AivisSpeech Engine: `audio_query` と `accent_phrases`](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/45048e0f7588bd1f39e0b4ab3eb06fee99514a3a/voicevox_engine/app/routers/tts_pipeline.py#L86-L244)
- [Chatterbox multilingual tokenizer の Japanese normalizer](https://github.com/resemble-ai/chatterbox/blob/65b18437192794391a0308a8f705b1e33e633948/src/chatterbox/models/tokenizers/tokenizer.py#L75-L110)
- [CosyVoice3 公式 inference 例](https://github.com/FunAudioLLM/CosyVoice/blob/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/example.py#L71-L102)
- [GPT-SoVITS API v2 の `text` / language contract](https://github.com/RVC-Boss/GPT-SoVITS/blob/d523079fc05d9a8028d6085bffe4a2757c32abb6/api_v2.py#L154-L176)
- [Irodori-TTS `synthesize()` input](https://github.com/Aratako/Irodori-TTS/blob/eaf74d6a19138f743acb5b71a445fd25a57db987/irodori_tts/inference_runtime.py#L857-L963)
- [Qwen3-TTS VoiceDesign API](https://github.com/QwenLM/Qwen3-TTS/blob/022e286b98fbec7e1e916cb940cdf532cd9f488e/qwen_tts/inference/qwen3_tts_model.py#L637-L727)
- [Supertonic Japanese text input](https://github.com/supertone-inc/supertonic-py/blob/908a56486e821e833a80530ff0cae3ad0b046fce/supertonic/core.py#L108-L292)
- [VoxCPM2 `generate()` text contract](https://github.com/OpenBMB/VoxCPM/blob/616d3d3e630a9c96c2853250eef91b0f39dcd5fa/src/voxcpm/core.py#L183-L220)
