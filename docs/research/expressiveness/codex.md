# 演技力・表現力の TTS 再現手法 — Codex 独立調査

- **調査日:** 2026-07-29
- **対象:** Gaya Bench（日本語 RPG モブ NPC 音声）
- **前提:** Windows 11 / RTX 4070 Ti 12GB / Python 3.12 / uv

## 1. 結論

現時点で「演技力」を単一の自動スコアで判定できる根拠はない。最短で品質を上げる実務解は、次の順である。

1. **既に実装済みの model-native な逐行制御を比較し、Qwen3-TTS だけ感情参照 A/B を追加する。**
   Irodori-TTS v3、VoxCPM2、CosyVoice3 は、現在の adapter で `emotion` / `intensity` / `delivery` を生成入力へ渡している。まず同じ少数行で人間評価し、制御が実際に効くモデルを絞る。Qwen3-TTS は Base clone に逐行 instruct がないため、#79 の感情別参照は妥当な仮説検証である。ただし「中立参照が棒読みの根本原因」とはまだ実証されていない。
2. **N テイク生成を、hard gate と soft ranking に分けて導入する。**
   破損、ラウドネス、active speech 0 は hard gate にする。#103 の日本語人評で誤拒否率が高かった ASR 読み不一致は `review_required` と監査値に限定する。SER、emotion2vec、DS-WED、F0・energy・pause も、プロジェクト内の日本語人評との相関を確認するまで soft ranking と監査値に限定する。最終選択は人間が行う。
3. **Qwen x-vector 演算は、ライセンスと日本語再現性を解決してから研究実験にする。**
   論文の結果は有望だが、検証感情は angry / happy / sad、主言語は英語、言語横断はポルトガル語までである。公開実装にライセンスがないため、現時点で製品パイプラインへコードや配布ベクトルを取り込めない。

人間の「演技として使える」という判断を最終目的に置き、自動指標はその判断を安くするために使う。感情分類器の確信度や韻律の差の大きさを、演技品質そのものへ読み替えてはならない。

## 2. 調査方法と証拠区分

Claude 側の `theory.md`、`data.md`、`data-pd-addendum.md`、`methods.md` を主張単位で読み、論文、公式リポジトリ、モデルカード、データ利用契約を優先して対照した。本稿では次の区分を使う。

- **確認済み:** 一次資料または現在のリポジトリ実装から直接確認できる。
- **限定的に支持:** 著者の実験結果はあるが、日本語、短い RPG 台詞、12GB 環境への外挿が必要。
- **仮説:** 本プロジェクト内の A/B と人評が必要。
- **採用不可:** ライセンスまたは実行条件が不明で、現状の配布・商用経路に入れられない。

## 3. 主要主張の対照検証

### 3-1. Qwen3-TTS と感情参照

**確認済み**

- 公式のモデル表では、Base は 3 秒 voice clone と fine-tuning 用で、CustomVoice と VoiceDesign にある instruction-following 能力を宣言していない。現行 adapter が `emotion=False` とするのは正しい。
- 現行 `qwen3_tts.py` は、`REFERENCE_TEXT = "こんにちは。今日はとても良い天気ですね。"` を scenario / character ごとに一度 VoiceDesign し、同じ参照を全行の Base clone に使う。逐行の `emotion` / `intensity` / `delivery` は生成入力に入らない。
- [Task-Vector Arithmetic](https://arxiv.org/abs/2606.05367) は Qwen3-TTS-12Hz-1.7B-Base で、感情韻律の強い担体が共同学習された x-vector である可能性を elimination study から示した。多話者 centroid を使った英語 held-out では emotion2vec cosine が平均 `+0.29`、英語からポルトガル語では `+0.09`、多話者ベクトルの WavLM speaker similarity は概ね `0.88` 以上だった。

**限定**

- 論文は x-vector を「支配的 carrier」と位置付けるが、すべての感情表現や日本語での因果を証明したものではない。
- 評価した感情は angry / happy / sad であり、本プロジェクトの 12 emotion を覆わない。
- LoRA/FT の否定結果は、単一話者・単一感情・約 30 分という低多様性条件である。「Qwen の fine-tuning は常に無効」と一般化できない。
- [公開実装](https://github.com/danielbrito91/xvector-emotion-arithmetic) は 2026-07-29 時点で検出可能なライセンスを持たない。コードと配布 `tau` は採用不可である。

**判定**

中立参照は有力な原因仮説だが、現在の証拠から「棒読みの根本原因」と断定しない。#79 は小規模 A/B を先に行い、声質同一性、読み、Owner 人評が改善した場合だけ全 character へ展開する。

### 3-2. model-native な逐行制御

既存報告の作成後に adapter 実装が進んだため、現在の優先順位は変わっている。

| 経路 | 現在の実装 | 一次資料上の能力 | 判定 |
|---|---|---|---|
| Irodori-TTS v3 | caption に voice / emotion / intensity / delivery、本文に emoji、任意の参照音声 | 公式モデルカードは参照音声と caption を同時に使う style-controlled cloning を明記。MIT。ただし複雑・矛盾した caption、漢字読みに制限 | **最優先で人評比較** |
| VoxCPM2 | 参照音声 + emotion / intensity / delivery の control prefix | 公式モデルカードは controllable cloning、日本語、約 8GB BF16、Apache-2.0 を明記。結果は run 間で変動し 1–3 回生成を推奨 | **N テイク運用と相性が良い** |
| CosyVoice3 | `inference_instruct2` に emotion / intensity / delivery | 逐行 instruction を直接利用 | **同じ評価セットで比較** |
| Qwen3-TTS Base | character 単位の中立 VoiceDesign reference | Base clone に逐行 instruct なし | **#79 の参照 A/B 対象** |

Irodori の [公式モデルカード](https://huggingface.co/Aratako/Irodori-TTS-600M-v3-VoiceDesign) は、Reference Speech + Caption + Emoji の併用を現在明記している。VoxCPM2 の [公式モデルカード](https://huggingface.co/openbmb/VoxCPM2) は、Controllable Cloning、日本語を含む 30 言語、約 8GB VRAM、run 間のばらつきを明記している。したがって、新しいモデルや後編集器を増やす前に、この 3 経路の実出力を比較する方が小さく確実である。

### 3-3. DS-WED

[DS-WED 論文](https://arxiv.org/html/2509.19928v3) の `r=0.77` は確認できた。ただし意味を限定する必要がある。

- データは英語の LibriSpeech test-clean / Seed-TTS test-en を使い、同じ system / speaker / text から seed 0–4 で作った **5 テイクの相互差**を評価する。
- PMOS は「どれだけ pitch / rhythm / stress が異なるか」であり、「感情が正しいか」「演技が上手いか」ではない。
- `r=0.77 [0.73, 0.81]` は group 内の prosody difference 人評との相関である。log F0 RMSE は `r=0.30`。
- 最良構成は **HuBERT-base の第 8 層 + 50 cluster**。WavLM-base はより安定だが、「WavLM 第 8 層が最良」は誤りである。
- 著者自身が cross-lingual applicability は英語でしか検証していないと制限している。
- [公式コード](https://github.com/yfyeung/DS-WED) は 2026-07-29 時点で検出可能なライセンスがない。

**用途:** #76 で同一行の候補群が十分に似過ぎていないか、モデル全体が多様性を失っていないかを見る group-level 監査値。単一 take の hard gate や emotion correctness には使わない。実装する場合は、無許諾コードをコピーせず、論文記載の式から独立実装し、依存モデルのライセンスも固定する。

### 3-4. emotion2vec / EECS / SER

[emotion2vec 論文](https://aclanthology.org/2024.findings-acl.931/) は、英語で事前学習し、英語、Mandarin、Bangla、French、German、Greek、Italian、Persian、Russian、Urdu の downstream を評価している。**日本語は含まれない。** また、論文はタスクごとに downstream model が必要で、speaker information が除去されているか未検証と明記する。

emotion2vec+ large の公式モデルカードは 9 class と約 300M parameter を示すが、学習データ詳細は「後日公開」のままで、Hugging Face の license 表示は一般的な SPDX ではなく `model-license` である。[公式 GitHub](https://github.com/ddlBoJack/emotion2vec) にも 2026-07-29 時点で検出可能なライセンスがない。

したがって次を区別する。

- **SER class probability:** 学習済み 9 class が感じ取ったラベル。Gaya Bench の `tired` / `drunk` / `whisper` / `shout` / `laughing` / `pain` とは一致しない。
- **EECS:** 目標側の参照 embedding との cosine。参照 corpus、話者、収録条件に依存する。
- **acting quality:** 台詞の意図、間、強調、役柄、自然さを含む人間判断。上二つと同義ではない。

[The False Resonance](https://arxiv.org/html/2604.26347) は、この区別をさらに厳しくする。emotion2vec 系の zero-shot cosine は speaker / linguistic distractor に負け、元の cosine も概ね `0.92–0.98` の狭い範囲へ集中した。linguistic distractor 条件の triplet accuracy は emotion2vec で MSP-Podcast `20.14%`、CREMA-D `3.38%` まで低下し、valence / arousal の変化との順位相関もほぼゼロだった。これは日本語での反証ではないが、「zero-shot cosine を演技品質へ直結できる」という前提を否定するには十分である。

**用途:** 公式コード・重みのライセンスを確認するまでは実行も取り込みもしない。論文の式から独立実装する場合も、依存モデルとデータのライセンスが明確な経路だけを使う。その後、日本語の Owner 人評と相関を取り、話者・性別・本文・録音条件による偏りを確認する。相関を確認する前は ranking の重みも持たせない。SER の先頭 500ms と全長の一致度は興味深い仮説だが、それを朗読性指標にする一次根拠は見つからなかった。

### 3-5. 2025–2026 の追加手法

| 手法 | 確認できたこと | Gaya Bench での扱い |
|---|---|---|
| [EmoSteer-TTS](https://arxiv.org/abs/2508.03543) | flow-matching TTS の activation steering により emotion conversion / interpolation / erasure を訓練なしで行う | Irodori は構造上の類似があるだけで、移植成功は未検証。研究候補、優先度低 |
| [TED-TTS](https://arxiv.org/abs/2601.03170) | segment-aware conditioning と EOS logit modulation で発話内 emotion / duration を制御 | 特定 backbone 内部への介入が必要。短いガヤの初期解ではない |
| [TTS-CtrlNet](https://arxiv.org/abs/2507.04349) | time-varying emotion control を ControlNet で追加 | 学習とモデル改造が必要。12GB 量産経路より研究寄り |
| Qwen x-vector arithmetic | 追加学習なし、推論時の低コストな強度ノブ | ライセンス、日本語、12 emotion、内部 API 固定が未解決 |
| DS-WED | take 群の韻律差を group-level で測る | 多様性監査。単一 take 選抜には不十分 |
| N テイク + rejection / ranking | VoxCPM2 自身も 1–3 回生成を推奨。近年の TTS 研究でも intelligibility / speaker similarity による候補選別が使われる | 現在の運用要件に最も直接的 |

音声後編集は魅力的だが、Step-Audio-EditX はモデル重みの利用条件を確定できないため保留を維持する。人間ガイド音声からの VC は高品質になり得るが、TTS ベンチから VC 制作へ目的が変わるため別企画とする。

Best-of-N の内容 gate にも一つの ASR を絶対視しない。[ASR Self-Verification](https://arxiv.org/abs/2606.18323) は N=2–4 で catastrophic failure を減らせることを示す一方、[ASR Family Confound](https://arxiv.org/abs/2607.08256) は verifier の family によって候補順位が逆転し得ることを示す。#75 / #76 では、明確な欠落・反復・停止を hard gate にし、近い候補間の順位は単一 ASR の小さな score 差だけで決めない。

## 4. データとライセンスの再評価

| データ | 独立確認 | 判定 |
|---|---|---|
| IEMOCAP | [Data Release Form](https://sail.usc.edu/iemocap/Data_Release_Form_IEMOCAP.pdf) は internal research only、commercial exploitation 禁止 | 商用パイプライン不可 |
| MELD | Friends 由来で、利用可能な権利処理を確認できない | 商用パイプライン不可 |
| CREMA-D | [公式 LICENSE](https://github.com/CheyneyComputerScience/CREMA-D/blob/master/LICENSE.txt) は DB に ODbL、contents に DbCL | DB 利用条件は確認できるが、実演家の追加権利まで保証する記載ではない。研究候補 |
| FastLabel × アマナイメージズ | 公式告知は 9 感情、100 人、6,800 file、収録者・話者の許諾取得を明記 | **有力な商用候補**。AI 学習、評価器、派生特徴、生成物、再配布の契約条項と価格を確認するまで「利用可」と確定しない |
| れきおん | NDL の「保護期間満了」表示に依拠できる候補 | #82 で取得方法・サイト条件を確認。話者ラベルなしの韻律アンカーとして扱い、声質クローンへ流用しない |
| 自社収録 | 用途を列挙した契約を新規締結できる | 長期的に最も説明可能。初期実験には重い |

「FastLabel が唯一の商用可データ」とまでは断定しない。正確には、今回確認できた中で、権利クリアを販売者が明示する最有力候補であり、実際の許諾範囲は契約確認待ちである。

## 5. 12GB / Windows / uv の実現可能性

| 案 | 追加 VRAM | Windows / uv | 実装費 | リスク |
|---|---:|---|---:|---|
| 既存 Irodori / VoxCPM2 / CosyVoice3 の評価 | 追加なし | 既に adapter と lock がある | S | 人評セット作成 |
| Qwen 感情参照 A/B | 同時に 1 モデルをロードする現行構成のまま | 既存 qwen extra | S–M | 参照数、声質 drift |
| N テイク + 音響 hard gate | 生成モデルと同じ、評価は CPU 中心 | 既存 ffmpeg + Python module | M | adapter の seed 可変化、manifest 更新 |
| ASR gate | ASR 実行分。生成モデル解放後に別工程化 | #75 で uv extra 固定 | M | 日本語読み照合 |
| emotion2vec / DS-WED | base なら 12GB 内に収まる見込みだが実測必須 | 別 scorer extra が必要 | M | ライセンス、日本語校正 |
| x-vector arithmetic | ベクトル演算自体は無視できる | Qwen 内部への revision 固定介入 | M–L | 無許諾コード、日本語、内部 API |
| EmoSteer / TED-TTS 移植 | 生成モデルに追加介入 | 研究環境が必要 | L | backbone 依存、検証コスト |

生成モデルと scorer を同時常駐させない。`generate -> normalize/encode -> qc` の段階を分ければ、12GB で複数モデルを抱える必要はない。

現在の Qwen manifest は 160 成功 / 1 失敗で、保存済み RTF と duration の合計から 1 sweep は約 44.8 分、N=5 は約 3.75 時間と見積もられる。N テイクを「数秒/行」とみなさず、N=2–3 の通過率から増分コストを判断する。

## 6. Claude 調査との主な相違・訂正

1. **「中立参照が根本原因」から「最有力仮説」へ降格する。** A/B 前に因果を断定しない。
2. **Qwen LoRA/FT の全面否定をしない。** 当該論文の低多様性条件に限った否定結果として扱う。
3. **x-vector arithmetic を短期本番案から研究案へ下げる。** 日本語未検証、3 emotion、公開コード無許諾である。
4. **DS-WED は演技度ではない。** 同一条件の take 群における prosody difference であり、英語のみ。最良設定も HuBERT-base 第 8 層 + 50 cluster である。
5. **emotion2vec / EECS を hard gate にしない。** 日本語未検証、ラベル不一致、speaker leakage 未検証、ライセンス不明がある。
6. **先頭 500ms SER 一致度と eGeMAPS 固定閾値を仮説扱いにする。** 本プロジェクトの人評との校正なしに採否へ使わない。
7. **現行実装を反映する。** Irodori v3 / VoxCPM2 / CosyVoice3 の逐行制御は既に adapter に入っており、「中期の将来案」ではない。
8. **データの権利を二値化しない。** IEMOCAP は契約上不可だが、CREMA-D、FastLabel、れきおんは用途と契約・取得条件を分ける。

## 7. 推奨実験

### E1: model-native 制御の小規模比較

- 24 行: 6 emotion × intensity 1/3 × 2 character。読みの難しい行を含める。
- #76 の take context 導入前は、固定 seed による重複を避けるため Irodori / VoxCPM2 / CosyVoice3 を各 1 take。
- blind な Owner 評価: `意図一致`、`役として自然`、`緩急`、`採用可否` の 4 軸。
- 既存 loudness / true peak / duration と #75 ASR を併記。

### E2: Qwen 感情参照 A/B

- 同一 character / text / seed で neutral reference と emotion-baked reference を比較。
- 最初は neutral / cheerful / angry / whisper の 4 条件だけ。
- human preference、speaker similarity、読み一致を確認する。
- 改善がなければ全 30 character の bank を生成しない。

### E3: soft scorer の校正

- E1/E2 の全出力を blind 人評する。
- duration、speaking rate、pause、F0/energy を保存する。SER embedding は、コード・重み・依存モデルのライセンス確認後にだけ追加する。
- 各 feature と `採用可否` の相関、model / emotion / character ごとの偏りを見る。
- 閾値はこのデータから決める。相関が弱い feature は表示だけにし、選抜重みを持たせない。

## 8. 出典

- [Qwen3-TTS 公式リポジトリ](https://github.com/QwenLM/Qwen3-TTS)
- [Task-Vector Arithmetic for Emotional Expressivity Control in LM-TTS](https://arxiv.org/abs/2606.05367)
- [xvector-emotion-arithmetic 公式実装](https://github.com/danielbrito91/xvector-emotion-arithmetic)
- [Irodori-TTS v3 VoiceDesign 公式モデルカード](https://huggingface.co/Aratako/Irodori-TTS-600M-v3-VoiceDesign)
- [VoxCPM2 公式モデルカード](https://huggingface.co/openbmb/VoxCPM2)
- [DS-WED 論文](https://arxiv.org/html/2509.19928v3)
- [DS-WED 公式実装](https://github.com/yfyeung/DS-WED)
- [emotion2vec 論文](https://aclanthology.org/2024.findings-acl.931/)
- [emotion2vec 公式実装](https://github.com/ddlBoJack/emotion2vec)
- [The False Resonance: emotion embedding similarity の反証](https://arxiv.org/html/2604.26347)
- [EmoSteer-TTS](https://arxiv.org/abs/2508.03543)
- [TED-TTS](https://arxiv.org/abs/2601.03170)
- [TTS-CtrlNet](https://arxiv.org/abs/2507.04349)
- [Reliable Neural-Codec TTS by ASR Self-Verification](https://arxiv.org/abs/2606.18323)
- [Best-of-N TTS Evaluation is Confounded by ASR Family Alignment](https://arxiv.org/abs/2607.08256)
- [IEMOCAP Data Release Form](https://sail.usc.edu/iemocap/Data_Release_Form_IEMOCAP.pdf)
- [CREMA-D 公式 LICENSE](https://github.com/CheyneyComputerScience/CREMA-D/blob/master/LICENSE.txt)
- [FastLabel 感情音声データセット公式告知](https://fastlabel.ai/news/20230727-emotional-voice)
