# Qwen3-TTS gender / speaker identity canary

検証日: 2026-07-30

対象 Issue: [#142](https://github.com/Hitsuki-Ban/gaya-bench/issues/142)

## 結論

**不合格。** VoiceDesign instruct へ構造化された `character.gender=female` と
`character.age=young_adult` を明示すると、女性指定への追従は大きく改善した。
しかし neutral / angry / whisper を独立 VoiceDesign reference から生成する設計では、
固定 seed でも angry の話者が明確に変わり、同一キャラクターの speaker identity を
維持できなかった。`guard-onna-002` の「するな！」も厳密な日本語語調で不合格だった。

[Qwen3-TTS 公式 README](https://github.com/QwenLM/Qwen3-TTS/blob/main/README.md#voice-design-then-clone)
が一貫したキャラクター声向けに示すのは、1本の designed reference から reusable clone
prompt を作って複数行へ再利用する経路である。感情ごとに独立 VoiceDesign した reference
が同一話者になる保証はなく、本 canary でもその同一性を確認できなかった。

したがって次の運用を確定する。

- Qwen3-TTS を感情制御候補から外し、`emotion` capability は `false` のまま維持する。
- 明示 gender / age を加えた独立 emotion reference bank は production adapter へ入れない。
- #71 で公開済みの character-only baseline は既存比較データとして維持し、今回の方式で
  再生成しない。
- #10 では Qwen の emotion-bank 全量再生成を行わない。#142 は完了として依存から外し、
  #126 と release 対象集合の確定後に残りモデルを進める。
- 固定 speaker を持つ CustomVoice は VoiceDesign / Base と別のモデル経路であり、
  必要なら別 Issue の canary とする。本 Issue では追加経路を実装しない。

## 条件

対象は同じ若い成人女性衛兵 `castle-gate/guard-onna` の3行である。

| line | emotion / intensity | text |
| --- | --- | --- |
| `guard-onna-001` | neutral / 2 | 身分を明かしなさい。 |
| `guard-onna-002` | angry / 2 | 怪しい動きをするな！ |
| `guard-onna-003` | whisper / 1 | ……ふぁ……いかん、いかん…… |

- 左: #96 の現行 emotion bank。VoiceDesign instruct に構造化された gender / age はない。
- 右: 左と同じ実装・入力へ `female` / `young_adult` の明示だけを加えた canary。
- 左右とも seed `0`、sampling、Base / VoiceDesign revision、後処理 v7 は同一。
- Base revision: `fd4b254389122332181a7c3db7f27e918eec64e3`
- VoiceDesign revision: `5ecdb67327fd37bb2e042aab12ff7391903235d3`
- `qwen-tts==0.1.1`, `torch==2.11.0+cu130`
- Windows 11 / RTX 4070 Ti 12GB / BF16 / SDPA

盲聴画面には、若い成人女性への適合、3行を通した同一話者性、文字読みと厳密な
日本語 pitch accent / 句調、emotion / delivery、prompt / reference prosody leakage
の5基準を明示した。左右は3行を通して同じ系列に固定し、系列単位でも identity を
判定した。生の回答は [`blind-decision.json`](blind-decision.json) に保存した。

## Blind 結果

| line | preference | 左 | 右 | 解釈 |
| --- | --- | --- | --- | --- |
| `guard-onna-001` | right | 男性、reading 不正、delivery 合格 | 若い成人女性、reading / delivery 合格 | gender / age 明示が改善 |
| `guard-onna-002` | right | 男性、reading 不正、delivery 合格 | 若い成人女性、reading 不正、delivery 合格 | 右も「するな！」の語調に失敗 |
| `guard-onna-003` | right | 男性、reading 合格、delivery 不正 | 女性だが年齢感不一致、reading / delivery 合格 | 右の擬声語はやや誇張 |

全6候補で leakage は `none` だった。系列単位では左右とも `identity=drift` で、
どちらも angry の話者が明確に変わると判定された。右系列は全行で preference を得たが、
本実験の必須条件である「3行が同一話者」を満たさないため production 合格にはしない。

## 実際の VoiceDesign instruct

`guard-onna-001`:

```text
声質: 硬質でよく通る声。抑揚を抑えた規律正しい話し方。
話者の性別: 女性
話者の年齢層: 若い成人
必ず若い成人女性の声として発声し、指定と異なる性別・年齢の声にしない。
性格: 冷静沈着で無駄口を叩かない。仕事には忠実。
場面: 城壁に囲まれた王都の正門前。昼、検問のため旅人や商人の列ができている。
感情: 自然で落ち着いた中立の感情
感情の強度: 中程度。台詞の意図が明確に伝わる強さで表す
演技: 自然な間を取り、平静な口調で話す。
感情が変わっても、同じキャラクターの声質、年齢感、話者としての同一性を保つ。
実在の人物や声優を模倣せず、この架空キャラクターの声として自然に発声する。
```

`guard-onna-002`:

```text
声質: 硬質でよく通る声。抑揚を抑えた規律正しい話し方。
話者の性別: 女性
話者の年齢層: 若い成人
必ず若い成人女性の声として発声し、指定と異なる性別・年齢の声にしない。
性格: 冷静沈着で無駄口を叩かない。仕事には忠実。
場面: 城壁に囲まれた王都の正門前。昼、検問のため旅人や商人の列ができている。
感情: 抑えきれない怒り
感情の強度: 中程度。台詞の意図が明確に伝わる強さで表す
演技: 語気を強め、短く鋭く言い切る。
感情が変わっても、同じキャラクターの声質、年齢感、話者としての同一性を保つ。
実在の人物や声優を模倣せず、この架空キャラクターの声として自然に発声する。
```

`guard-onna-003`:

```text
声質: 硬質でよく通る声。抑揚を抑えた規律正しい話し方。
話者の性別: 女性
話者の年齢層: 若い成人
必ず若い成人女性の声として発声し、指定と異なる性別・年齢の声にしない。
性格: 冷静沈着で無駄口を叩かない。仕事には忠実。
場面: 城壁に囲まれた王都の正門前。昼、検問のため旅人や商人の列ができている。
感情: 息を混ぜた小さな囁き
感情の強度: 弱め。声質を崩さず控えめに表す
演技: 声量を抑え、耳元で囁くように話す。
感情が変わっても、同じキャラクターの声質、年齢感、話者としての同一性を保つ。
実在の人物や声優を模倣せず、この架空キャラクターの声として自然に発声する。
```

## 音声 provenance

音声 binary は repository に commit せず、ローカルの `artifacts/issue-142/` に保存した。

| line | 左 Opus SHA-256 | 右 reference SHA-256 | 右 Opus SHA-256 |
| --- | --- | --- | --- |
| `guard-onna-001` | `4e5809bf925d756a8645f0c3bdc434e2199ea5d1e3c50d8fdb1f36084380b6fc` | `fd82aee59731e4c6551b37a755b28cfdc2c2347ae53185eab8129ce3084ee317` | `be6d94767b94813d00afa8d24f66caa30e319033ba343ef2f5ab1705419d6b7b` |
| `guard-onna-002` | `1c75ac05c36567171c7a606fc46c6fea94361edfe2d6e1252480225205bf7de1` | `b51bd6564defb54a1e19f3d949260180592548776027415b67f49e44e7963cb9` | `7bf7d2c0f4da32cab2d01264bd80a60ada138077fb6f947dd03c224d5a23907c` |
| `guard-onna-003` | `2a4fcbd0b25fc4456eeb5f73201ae41106d07bbb9ab61f1c2ae2c85abad55264` | `2f4e41c93cfb9470846c97714285b2b88452e26ff9b9447d40e27b41ea42b931` | `0807625348f13d39c1fd620fde1753fa2f5623963514c4e517cd0332956bf9b2` |

右の3 reference 準備は66.637秒、各 target 生成は35.468秒 / 12.080秒 /
21.393秒だった。target 生成の最大 CUDA peak は4,199.837 MiB allocated /
4,352 MiB reserved で、12GB VRAM gate は通過した。

## 機械補助

Kana-Whisper の転写は左右で一致した。

| line | 左 / 右 transcript | 左 median F0 | 右 median F0 |
| --- | --- | ---: | ---: |
| `guard-onna-001` | `ミブンオアカシナサイ` | 108.73 Hz | 213.64 Hz |
| `guard-onna-002` | `アヤシーウゴキオスルナ` | 208.76 Hz | 264.54 Hz |
| `guard-onna-003` | `フワ、イカン、イカン` | 105.59 Hz | 209.97 Hz |

F0 は gender 指定の追従と左系列内の変動を補助的に示すが、性別・年齢・話者同一性の
自動 gate には使っていない。ASR も文字列レベルの近似確認だけであり、人評で検出した
厳密な日本語 pitch accent / 句調の誤りを合格に変えない。
