# 日本語ネイティブ性 — 「カタコト韻律」の原因・検出・抑制

**調査日:** 2026-07-29 / **対象:** gaya-bench (RPG モブ NPC ガヤ音声量産)
**発端となる Owner 観察:** 「内容・感情が正しくても、日本語はトーンで一発アウトするケースが多い。特に女性声で外国人のカタコトっぽいトーンがチラホラ」「AivisHub の合成モデルでは『せつなめ』『おちつき』系ボイススタイルの方が語尾の変な上昇トーンが少ない」
**スコープ:** 現象の音声学的整理 / 多言語 TTS での発生機序 / 自動検出指標 / 抑制手法 / AivisHub 仮説の検証材料

---

## 1. 総括

- **Owner の直感は文献と一致する。** Idemaru et al. (2019) は L2 日本語の外国語アクセント評定を最も頑健に予測する音響要因が **tone (ピッチアクセントとイントネーションの高低パターン)** であることを、英語母語・中国語母語の2群を通じて示した。日本語はピッチで語彙を弁別する言語なので、内容 (CER) と感情 (intent) が合っていてもトーンで落ちるのは構造的に当然である。
- **既存 QC の韻律 feature は、この現象を捕まえられていない。** N3 pilot の LOLO 結果では 6 feature すべてが人評 adoptable に対しほぼランダム (Hit@1 0.18–0.50、random 0.386)。原因は明確で、`duration_sec` / `mora_per_second` / `pause_sec` / `voiced_ratio` / `f0_semitone_std` / `energy_median_dbfs` はいずれも**発話全体の集計統計**であり、「アクセント核が正しいか」「語尾が上がったか」という**局所的な形**を一切見ていない。足すべきは局所指標である。
- **決定的な負の知見: 汎用 MOS 予測器はアクセント誤りに対して盲目である。** arXiv:2606.19951 は、日本語合成音声のアクセント句の 10–20% / 80–90% を意図的に破壊しても、UTMOS を含む6つの MOS 予測モデルが **0.1 点未満しか動かない**のに対し、人間は 4.00 → 3.19 → 2.16 と **1.84 点**落とすことを示した。UTMOS の SRCC は -0.012、すなわち完全に無力。**「トーンで一発アウト」の検出に汎用 MOS を使ってはならない。**
- **同じ論文が女性声の件にも直接効く。** MOS 予測モデルは mean F0 と **強い負相関** (r = -0.458 〜 -0.788) を示すのに、人間はほぼ無相関 (r = -0.059)。つまり機械スコアは高ピッチ声＝女性声を系統的に不当に低く採点する。**「人間が女性声でカタコトを感じる」ことと「機械が女性声を低く採点する」ことは別現象**であり、混同すると gate が壊れる。すべての F0 系指標は話者内正規化が必須。
- **本件の穴を正面から埋める公開モデルが今年出ている。** PASQA (LY Corporation, INTERSPEECH 2026, arXiv:2606.20137) は日本語ピッチアクセント正誤に特化した MOS 予測モデルで、主観評価に対し **SRCC 0.828 / KTAU 0.614**。コードは CC0、重みは HF 公開。**入力は 16 kHz 音声 + カタカナモーラ列 — 既存 QC はこの両方をすでに持っている** (`expected_reading.normalized` と 16 kHz decode 済みサンプル)。統合コストが異常に低い。
- **ただし PASQA だけでは Owner の主訴には届かない。** 著者は「文末イントネーション / boundary pitch movement はアクセント誤りデータセットの構成外」と明記している。PASQA が捉えるのは**アクセント核位置**であり、**語尾の変な上昇**は別途測る必要がある。この2つは相補的で、両方要る。
- **語尾 F0 の直接計測は既存コードに数十行で足せる。** `qc_runtime._analyze_prosody` は既に `librosa.pyin` の F0 配列と `librosa.effects.split` の有声区間を計算済みで、末尾区間を切り出して semitone インターバルと傾きを出すだけ。追加依存ゼロ、追加計算時間ほぼゼロ。閾値の一次アンカーは Peters (2008) の「**2 semitone 以上の上昇 + 50 ms 以上の有声で疑問と一意に知覚される**」。
- **AivisHub 仮説は機序として妥当。** Style-Bert-VITS2 の style は学習音声の style embedding をフォルダ分け/クラスタで平均した `style_vectors.npy` である。すなわち「おちつき」ベクトルは実際に落ち着いて発話されたクリップ群の重心であり、その群の文末 contour 統計 (下降終止が多い) をそのまま条件づけとして持ち込む。Owner の観察を説明できる。ただし定量実測は文献に無いので自前計測が最短。
- **既存 doc に修正が要る箇所を1つ発見。** `docs/research/expressiveness/methods.md` 手法5-① が「AudioQuery のモーラ単位編集」を挙げているが、**AivisSpeech Engine では `Mora.pitch` / `consonant_length` / `vowel_length` は常に dummy 0.0 を返し編集できない** (VOICEVOX との明示的な差分)。さらに `intonationScale` はピッチレンジではなく「**話者スタイルの感情表現の強さ**」であり、Neutral スタイルでは無視される。§5 に詳述。

---

## 2. 「カタコト韻律」の構成要素と重要度順

重要度は (a) 日本語で語彙・文法を担う度合い、(b) 多言語 TTS で実際に壊れる度合い、(c) ガヤ (5〜30文字の短発話) で観測可能かの3軸で判定した。

### 2-1. 第1位 — アクセント核位置の誤り / 平板化

- **音声学:** 日本語のアクセント句は「1回上昇して1回下降する」単位であり、**核 = 下がり目の位置**が語彙を弁別する (箸 HL / 橋 LH)。国際交流基金の解説が言うとおり、日本語アクセントの本質は「高低」ではなく「**下がり目**」である。
- **なぜ多言語 TTS で壊れるか:** Qwen3-TTS / VoxCPM2 / Chatterbox / CosyVoice はいずれも音素・コーデックトークンベースで、**日本語のアクセント辞書やアクセント句ラベルを front-end に持たない**。核位置は学習データから確率的に創発するだけなので、低頻度語・複合語・固有名詞で外れる。対照的に AivisSpeech / Style-Bert-VITS2 系は pyopenjtalk 由来のアクセント情報を明示的に front-end で持つ。**これが「AivisHub 系の方がマシ」の第一の理由**であり、スタイルの話より前に来る構造的な差である。
- **人間への効き:** arXiv:2606.19951 で MOS 1.84 点低下。単独要因としては最大級。
- **検出可能性:** PASQA で直接測れる (§3-B)。

### 2-2. 第2位 — 句末・文末の不自然な上昇 (BPM の誤挿入)

- **音声学:** 日本語の平叙文末は無標で **下降 (L%)**。X-JToBI では accent phrase 末の複合的なピッチ変動を **boundary pitch movement (BPM)** として独立にラベルし、上昇系 (H%, LH% 等) には機能負荷が付く。郡史郎の分類では文末上昇は少なくとも **疑問型上昇調** (判定要求の質問) と **強調型上昇調** (強い承認要求) に分かれ、それぞれ別の語用論的機能を担う。したがって**平叙のガヤ行で上昇が出ると、聞き手には「疑問でないのに聞き返している」「外国人が読み上げている」と解釈される**。
- **なぜ多言語 TTS で壊れるか:** (i) 英語の非最終句 continuation rise と高頻度化した uptalk、(ii) 中国語の声調 (特に第2声) 由来の上昇パターン、(iii) 参照音声そのものが上昇終止だった場合の転写。多言語モデルは言語 ID を1つしか取らないものが多く (Chatterbox は `language_id` 単一指定が公式に既知の制約)、韻律レベルでは言語間のリークが残る。文献上は cross-lingual TTS の「foreign accent problem」として広く認識されており、METTS / DiCLET-TTS などが言語非依存/言語固有の韻律表現の分離や、言語識別器による accent-invariant 表現の強制でこれを緩和しようとしている — 裏を返せば**素の多言語モデルでは分離できていない**ということ。
- **重要な但し書き:** 日本語話者は上昇そのものを嫌うわけではない。**アクセント型を保ったまま末尾で上げる**のが自然な疑問文であり、英語式に文全体を上げると不自然になる。つまり「上昇の有無」より「**上昇のかかり方と、その行に上昇が許されるか**」が問題。ガヤは呼びかけ・驚き・疑問が多いので、行ごとの期待値なしに一律検出すると偽陽性だらけになる (§4-L1)。
- **検出可能性:** 語尾 F0 スロープで直接測れる (§3-A)。**PASQA のスコープ外**。

### 2-3. 第3位 — アクセント句フレージングの崩れ

- アクセント句境界 (通常は助詞や文節の切れ目) の誤り、過剰分割、および **downstep** (先行核の後で後続句のピッチレンジが圧縮される現象) の欠如。downstep が無いと発話全体が平坦になるか、逆に句ごとにフルレンジでリセットされて凸凹になる。
- 日本語の平叙文は発話全体を通じて **downtrend (declination)** を示すのが標準。これが欠けると「歌うような」印象になる。
- **検出可能性:** 発話全体の F0 線形回帰傾き (declination) は §3-A のついでに測れる。句境界レベルの検証は marine / pyopenjtalk の推定と突合が要る (§3-C、工数中)。

### 2-4. 第4位 — 過剰なピッチレンジ

- L2 イントネーションの逸脱として一般に報告されるのは pitch range、declination line、非最終句の rise の3つ。日本語は英語より狭いレンジで話す傾向がある。
- **ただし既存 `f0_semitone_std` は N3 pilot で判別力を示さなかった** (Hit@1 0.429 vs random 0.389)。レンジの絶対量より **declination の傾き** と **語尾の形** の方が効く可能性が高く、単なるレンジ拡大の再計測は優先度が低い。

### 2-5. 第5位 — モーラ等時性の崩れ

- 日本語はモーラを単位とし、語全体の長さがモーラ数にほぼ比例する強い等時化傾向を持つ。L2 話者は英語の stress-timing を転移させて長短をつけがちで、特に**特殊拍 (撥音ン・促音ッ・長音ー) の短縮**が典型的な崩れ方。
- 定量指標としては nPVI / rPVI / %V / Varco 系が L2 リズム研究の標準。ただし **5〜30 文字のガヤでは区間数が少なく統計が不安定**で、既存の `active_mora_per_sec` 以上のものを短発話で安定して取るのは難しい。優先度は中〜低。

### 2-6. 第6位 — 分節音 (segmental)

- ラ行、母音の無声化、外来語の音節構造など。prosody と segments のどちらが accentedness に効くかは研究間で結論が割れており inconclusive。
- **既存の kana-whisper CER で部分的に捕捉済み**なので、新規投資の優先度は低い。

### 2-7. 女性声で顕著になる要因

**直接の因果を示す文献は見つからなかった。** ただし機序として3系統が考えられ、うち2つは今回の調査で裏づけが取れた。

| 機序 | 内容 | 裏づけ |
| --- | --- | --- |
| **(i) 機械側の系統誤差 (裏づけあり)** | MOS 予測器は mean F0 と r = -0.458〜-0.788 の負相関、人間は r = -0.059。高ピッチ声は自動スコアで不当に低く出る | arXiv:2606.19951 |
| **(ii) F0 推定の不安定 (機序として妥当)** | 高 F0 域では pyin / DIO 等が octave error を起こしやすい。現行 `fmin=65, fmax=1000` は女性声の倍音を掴む余地がある。自動指標側のノイズが増える | 一般的な F0 推定の既知課題 |
| **(iii) 合成の不安定 (傍証あり)** | 高ピッチ側の感情状態ほど合成音声と ground truth の類似度が下がる、という観察がある | KazEmoTTS |

- **(i) と (iii) を混同しないこと。** Owner が耳で聞いて「女性声でカタコトが多い」と感じるのは (iii) の可能性が高く、一方で自動 gate を組んだときに女性声が落ちやすくなるのは (i) の可能性が高い。**両者は逆方向の対策を要求する** — 前者は生成側の対策、後者は指標の話者内正規化。
- **実務上の帰結: すべての F0 系指標は、同一 voice × 同一 model のテイク母集団に対する median / MAD で z 化してから閾値をかける。** 絶対 Hz はもちろん、median 基準の semitone 化だけでも話者間比較には不十分。

---

## 3. 検出指標の推奨

前提: 既存 QC パイプライン (kana-whisper ASR + `_analyze_prosody`) への追加であること。`content.prosody` は `qc_report.py` で「object であること」しか検証していないので、**キー追加は report contract に対して非破壊**。ただし `pilot-set.json` の `FeatureName` union は exact で追加 field を拒否するため、pilot 側は v2 が必要。gate 化するなら `GATE_POLICY_VERSION` を `take-gates-v3` に上げる。

### 3-A. 語尾 F0 スロープ・インターバル 【最優先 / 実装コスト S】

**捉えるもの:** §2-2 (文末の不自然な上昇)、副次的に §2-3 (declination)。

**実装:** `qc_runtime._analyze_prosody` に追加するだけ。既に `librosa.pyin` の `f0` と `librosa.effects.split` の `intervals` を計算済みなので、再計算は不要。

- 最後の active interval の末尾を切り出す (推奨窓 200 ms、最低 60 ms の連続有声フレームを要求)
- `final_f0_interval_st` = 末尾窓内の (最終 F0 − 最小 F0) を semitone 換算
- `final_f0_slope_st_per_sec` = 末尾窓の F0 (semitone) の線形回帰傾き
- `final_rise_detected` = `interval >= 2.0 st` かつ有声長 >= 50 ms (Peters 2008 準拠)
- `declination_st_per_sec` = 発話全体の F0 (semitone) 線形回帰傾き。日本語平叙は負が期待値

**必須のガード:**
- **octave error 対策** — 末尾窓の F0 を発話 median ±6 st でクリップしてから計算する。これを入れないと女性声で偽陽性が出る (§2-7 の機序 ii)
- **無声終止の除外** — 「〜です」の /s/ 終わりなど有声フレームが閾値未満の場合は `None` を返し、gate 対象外にする
- **話者内 z 化** — 閾値判定は生値ではなく voice × model 母集団の z-score で行う

**確度:** 現象への直撃度は高い。ただし**閾値は日本語データで未校正**である。2 semitone というアンカーは Peters (2008) のドイツ語知覚実験由来で、日本語の疑問型上昇調・強調型上昇調の実測分布から取ったものではない。また Peters は「F0 slope より **F0 interval** の方が知覚された文モダリティをよく予測する」と報告しており、**slope より interval を主指標にすべき**。まず `report_only` で実測分布を取ってから gate 化する (既存の `"prosody_thresholds": "report_only"` 運用をそのまま維持)。

**偽陽性リスク:** 高い。ガヤは呼びかけ・驚き・疑問が多く、**正当な上昇調が高頻度**に出る。§4-L1 の行レベルのラベル付けが前提条件。

### 3-B. PASQA によるアクセント正誤スコア 【高優先 / 実装コスト S〜M】

**捉えるもの:** §2-1 (アクセント核位置の誤り)。本件の第1位要因を直接測る唯一の既製手段。

**素性:**
- LY Corporation、INTERSPEECH 2026、arXiv:2606.20137。コード CC0 (github.com/lycorp-jp/PASQA)、重みは HF (`ly-corporation/PASQA`)。SHEET (Speech Human Evaluation Estimation Toolkit) ベース
- アーキテクチャ: wav2vec 2.0 のフレーム特徴 + モーラ列を 256 次元に埋め込んで 1層 Transformer で文脈化 → **cross-attention で融合** (mora-conditioned fusion)。ranking loss と話者不変学習、および**フレーム単位の accent-error localization 補助ヘッド**
- 学習データ: accent-controllable TTS でアクセント核位置を意図的にずらした合成音声 213 万サンプル / 2,899 時間 / 13 話者。誤り率 r=0 / 0.1–0.2 / 0.8–0.9 の3段階
- 性能: 主観評価に対し **SRCC 0.828 / KTAU 0.614 / MSE 1.293**。同条件の UTMOS は SRCC -0.012

**既存 QC への配線がとにかく楽:**
- 入力は **16 kHz 波形 + カタカナモーラ列**。`qc_runtime._decode_audio` は既に `SAMPLE_RATE_HZ = 16_000` で decode しており、`expected_reading.normalized` が権威カタカナ、`count_japanese_mora()` がモーラ数を持つ。**新規に用意するものが実質ない**
- `KanaWhisperQCRuntime` と同じパターンで `PasqaPredictor` を保持し、`RuntimeInspection` に `pitch_accent_mos` を足すだけ。`describe()` に model revision を追加すれば provenance も既存契約に乗る
- VRAM は wav2vec 2.0 級。RTX 4070 Ti 12GB で kana-whisper と同居可能 (確実を期すなら逐次ロード)
- **frame-level localization ヘッドを使えば「どのモーラで外したか」を出せる** → 人間レビュー UI に直結する

**確度と使い方の注意 (重要):**
1. **著者自身が「絶対スコアの較正ではなく、深刻度の順序付けと局所アクセント誤りへの感度が目的」と明言している。** したがって**絶対閾値の hard gate に使ってはならない。同一 line の N テイク内の順位付けに使うのが正しい** — これは既存の N テイク選抜基盤とちょうど噛み合う
2. **文末 BPM はスコープ外**と著者が明記。§3-A との併用が必須
3. 学習データが accent-controllable TTS の合成音声なので、多言語 TTS の out-of-domain 出力に対する頑健性は未検証 (著者が limitation に挙げている)。**導入時に N3 pilot 相当の人評との突合を1回は取るべき**

### 3-C. pyopenjtalk / marine による期待アクセント型との突合 【中優先 / 実装コスト M】

**捉えるもの:** §2-1 と §2-3 (核位置 + 句境界)。

- `pyopenjtalk` (MIT。依存の Open JTalk / hts_engine_API は Modified BSD) でシナリオ text からアクセント句境界と核位置を得る。v0.3.0 以降は `run_marine=True` で DNN ベースの marine 推定も使える (marine は mora / intonation_phrase_boundary / accent_phrase_boundary / accent_status を出力)
- **Windows 環境なら `pyopenjtalk-plus` / `marine-plus` (tsukumijima)** が Windows 対応を明示しており実績がある
- 期待 HL 列と実測 F0 contour を突合し、核位置での下降の有無を検証する

**確度:** 手法としては確実だが、**モーラ↔時間のアラインが要る**のが重い。kana-whisper は timestamp を出しておらず、素直にやるなら forced aligner (MFA 等) を別途足すことになる。**PASQA が同じ情報をアライン無しで内部処理してくれるので、3-B を入れるなら 3-C の優先度は大きく下がる。**

**3-C の真価は別のところにある:** 期待アクセント型を**人間レビュー用に表示する**用途。N3 pilot の rubric で「トーンがおかしい」を評定する際、期待型が併記されていれば判定が安定する。工数の割に自動 gate への寄与は薄いので、**レビュー支援としてのみ**推奨する。

### 3-D. 汎用 MOS を nativeness 判定に使わない 【負の推奨 / 既存計画の修正】

- arXiv:2606.19951 の結果は決定的: アクセント誤り率を 10–20% → 80–90% に上げても6モデルすべてが 0.1 点未満しか動かない。人間は 1.84 点低下
- 加えて mean F0 と r = -0.458〜-0.788 の負相関 → 女性声を系統的に低評価。人間は r = -0.059
- 著者の結論は「既存モデルは異なる特徴部分集合を重視しており、人間の評価パターンと系統的に乖離する」「学習データの構成だけでは修正できない — 韻律品質を明示的に教師する信号が無い」
- **`docs/research/expressiveness/methods.md` の手法6 で UTMOSv2 を選抜スコアラに挙げているが、「トーンで一発アウト」の検出には無効であることを明記すべき。** UTMOSv2 の用途は**収録品質・アーティファクト由来の自然さ**に限定し、日本語ネイティブ性は PASQA + 語尾 F0 に分離する

### 3-E. まとめ表

| 指標 | 捉える要素 | コスト | 既存 QC への配線 | 確度 | 推奨運用 |
| --- | --- | --- | --- | --- | --- |
| **A. 語尾 F0 interval/slope** | §2-2 文末上昇、§2-3 declination | **S** (30–60行、追加依存なし) | `content.prosody.f0.final_*` に追加。report contract 非破壊 | 直撃度 高 / **閾値未校正** | report_only → soft signal → gate |
| **B. PASQA** | §2-1 アクセント核 | **S〜M** (runtime に1モデル追加) | 入力 (16kHz + カタカナ) を既に保有 | **SRCC 0.828**。ただし絶対較正なし | **N テイク内の順位付け専用** |
| **C. pyopenjtalk/marine 突合** | §2-1, §2-3 | **M** (forced aligner が要る) | 新規アライン基盤が必要 | 手法は確実だが B と重複 | 人間レビュー用の期待型表示のみ |
| **D. 汎用 MOS (UTMOS)** | — | — | — | **アクセント誤りに無反応 (SRCC -0.012)** | nativeness には**使わない** |
| **E. 話者内 z 化** | 全 F0 指標の前処理 | **S** | 既存 report の集計に追加 | 必須 | A/B すべてに適用 |

---

## 4. 抑制手法の推奨 (軽い順)

### L1. 行ごとに「期待される文末イントネーション」をラベル付けする 【S / 根拠: 設計上の必然】

- scenario YAML の line に `final_intonation: falling | rising | any` を持たせ、§3-A の判定対象を `falling` 期待の行だけに絞る
- **これが無いと §3-A も §3-B も偽陽性でノイズになる。** ガヤは呼びかけ・驚き・疑問が高頻度で、正当な上昇調 (郡の分類でいう疑問型・強調型) が普通に出る
- 一次近似としては text 末尾の `？` と `emotion` の値から自動導出できるので、初手は自動 + 人手上書き可でよい
- **工数ほぼゼロで、他のすべての施策の前提条件。最初にやるべき。**

### L2. 参照音声を「落ち着き・下降終止」のクリップに固定する 【S / 根拠: 中〜高】

- **機序:** zero-shot TTS の参照は timbre だけでなく prosody・話速・イントネーションを運ぶ。参照が極端に短いと timbre 寄りになり prosody が under-represent されるが、3–8 秒級の参照は prosody を転写する。既存 methods.md が引く arXiv:2606.05367 の Qwen3-TTS 実験 (x-vector が感情韻律の支配的キャリア、`full_swap` で codec トークンの感情が上書きされる) と整合し、**参照選択が最強のレバーであること**を裏づける
- **実践:** 参照クリップを「平叙・文末下降・落ち着いた読み」に固定し、**参照そのものを §3-A で検査してから採用する**。参照が語尾上げしていれば全行に伝播するので、これは1回の検査で 153 行に効く最もレバレッジの高い操作
- **既存 methods.md の S1 とトレードオフになることに注意。** methods.md は「参照を演技済みにする」(感情別ボイスバンク) を最優先に挙げているが、演技参照は語尾上昇リスクを上げうる。**両立させるなら、感情別アンカーを1本ずつ §3-A で検査して合格したものだけを採用する**運用にする
- **確度:** 機序は堅い。ただし「落ち着き系参照が語尾上昇を定量的に減らす」を直接測った文献は見つからなかった。**同一 line・同一 seed で参照だけ差し替えて `final_f0_interval_st` の分布を比較する自前 A/B が最短** (数時間で取れる)

### L3. AivisSpeech 側で style を「おちつき」「せつなめ」に固定する 【S / 根拠: 中】

**Owner 仮説の機序としての妥当性:**

- Style-Bert-VITS2 の style は、**学習音声の style embedding をフォルダ分けまたはクラスタで平均した `style_vectors.npy`** である (デフォルトでは学習フォルダのフォルダ分けに応じたスタイル + 全体平均の Neutral が生成される)。すなわち「おちつき」ベクトルは**実際に落ち着いて発話されたクリップ群の重心**であり、その群の文末 contour 統計 (下降終止が多い、レンジが狭い) をそのまま条件づけとして持ち込む
- SBV2 は CLAP ベースの感情埋め込みを廃して全結合層による style embedding に置き換え、`gin_channels` を 256 → 512 に拡張して表現容量を上げている。style 強度は推論時に連続指定できる
- **加えて、§2-1 で述べた front-end の差の方が実は大きい。** AivisSpeech / SBV2 は pyopenjtalk 由来のアクセント情報を明示的に持つが、Qwen3-TTS / VoxCPM2 / Chatterbox / CosyVoice は持たない。**「AivisHub の方がマシ」の主因はスタイルより front-end である可能性が高い**

**AivisSpeech Engine 固有の制約 (既存 doc の修正が必要な箇所):**

| 項目 | AivisSpeech での挙動 | 影響 |
| --- | --- | --- |
| `Mora.pitch` | **常に dummy 0.0。編集不可** (VOICEVOX との明示的差分) | **モーラ単位のピッチ編集はできない。** methods.md 手法5-① の「AudioQuery のモーラ単位編集」は要修正 |
| `Mora.consonant_length` / `vowel_length` | 同じく常に dummy 0.0 | モーラ単位の尺編集も不可 |
| `intonationScale` (0.0–2.0) | **ピッチレンジではなく「話者スタイルの感情表現の強さ」。** Neutral スタイルでは無視される | 語尾上昇の抑制目的で上下させても意図した効果は出ない。下げると選択スタイルの色が薄まる |
| `tempoDynamicsScale` (0.0–2.0) | AivisSpeech 独自。話す速さの緩急の強弱 | 緩急制御には使える (methods.md の記述は正しい) |
| `pitchScale` | 「0.0 から変更すると音質が劣化する可能性があります」と公式に注記 | 積極的に触るべきでない |
| `enable_interrogative_upspeak` | **常に無視される。**句読点から自動処理 | **VOICEVOX の疑問文自動語尾上げを外部から明示的に off にする API は無い** |
| `pauseLength` / `pauseLengthScale` | 常に無視される | 間の制御は別手段が要る |
| `AccentPhrase.accent` | read-only の記載なし → 編集可能と読める | **アクセント核の修正はできるが、語尾上昇の直接抑制はできない** |

- **確度:** 「style ベクトルが文末 contour を運ぶ」は機序として堅い。ただし「せつなめ/おちつきが定量的に語尾上昇を減らす」の実測は文献に無い → **L2 と同じく、`final_f0_interval_st` を使った自前の style 間比較が最短。** AivisSpeech は同一モデル内で style だけを切り替えられるので、統制の取れた比較が非常に安く取れる (同一 line・同一 seed で style のみ変更)。**Owner 仮説の検証実験としてこれを最優先で回すことを推奨する**

### L4. 指示文・スタイルテキストでの抑制 【S / 根拠: 低】

- **「語尾を上げない」のような否定形の韻律指示が効くという証拠は見つからなかった。** instruction-following TTS のベンチマークは登場しているが (MINT-Bench は emotion / speed / pitch / intonation / paralinguistic の各次元を多言語で評価)、**intonation 次元の制御精度は emotion 次元より低いのが一般的な報告**であり、fine-grained な韻律制御は「open challenge」とされる
- Qwen3-TTS Base は instruct 非対応 (既存 methods.md の確認済み事実)。VoxCPM2 の Controllable Cloning や CosyVoice3 の `inference_instruct2` は指示を受け付けるが、否定形の局所韻律指示に応える保証はない
- **肯定形で「落ち着いた、淡々とした、断定的な口調で」と書く方が、否定形で「語尾を上げるな」と書くより期待値が高い** — 前者は参照/スタイル空間の位置を動かすが、後者はモデルが局所韻律を明示的に制御できることを前提にしてしまう
- **確度: 低。試すのは安いが期待しすぎないこと。** L2/L3 の下位互換と位置づける

### L5. N テイク選抜ゲートでの足切り 【M / 根拠: 高 — 本命】

**既存の N テイク基盤にそのまま乗る。PASQA スコアが「絶対較正ではなく順序付け用」と著者に明言されている以上、まさにこの使い方が正しい。**

推奨構成:

1. 既存 mechanical gate → 既存 content gate (CER) で hard reject (現行どおり)
2. §3-A の `final_rise_detected` を **soft signal** に。**`final_intonation: falling` の行に限る** (L1 が前提)
3. 残ったテイクを **PASQA スコア降順**でソート
4. 最上位を採用、soft signal 該当は人間レビューに回す

**N3 pilot の教訓を必ず踏まえること:** 既存の `explicit_reading_mismatch` は adoptable FRR **0.505**、winner の **56% (28/50)** を落としている。新指標をいきなり hard gate にすると同じ失敗を繰り返す。**必ず `report_only` → `soft signal` → (十分な実測分布が出てから) `gate` の順で昇格させる。** これは既にリポジトリで確立された運用パターン (`"prosody_thresholds": "report_only"`、#110 の「ASR 読み不一致を review signal へ変更」) と一致する。

gate 化する場合は `GATE_POLICY_VERSION` を `take-gates-v3` に、pilot feature を足すなら `pilot-set` を v2 に上げる必要がある。

### L6. 句末 F0 の後処理補正 (pyworld での語尾コンター再形成) 【M / 根拠: 中 — 非推奨寄り】

**実現性はある。** pyworld は音声を f0 / sp (スペクトル包絡) / ap (非周期性) に分解でき、`wav2world` → f0 配列の末尾 N フレームだけ目標下降カーブに置換 → `synthesize` で戻せる。sp / ap は触らない。

**しかし推奨しない理由が3つある:**

1. **provenance 設計と相性が悪い。** 全発話を WORLD で再合成すると音質が一段落ちる。しかも QC パイプラインは Opus 化・ラウドネス正規化・sidecar の `postprocess` profile・WAV/Opus/sidecar の SHA-256 チェーンで固められており、**QC 後段で音声を触ると provenance 検証がすべて壊れる**
2. **根本原因が直らない。** 語尾だけ下げても、その手前のアクセント句の核が間違っていれば「カタコト感」は残る。§2-1 が第1位要因である以上、語尾補正は対症療法
3. **偽陽性がそのまま音声の破壊になる。** 「上げるべきでない上昇」と「正当な強調型上昇」の判別を自動でやり切る必要があり、§3-A の閾値が未校正な現状では危険

**もしやるなら:** QC 後ではなく**生成直後 (postprocess profile の一部)** に入れ、sidecar に補正の有無と補正量を記録する設計にする。**N テイク選抜 (L5) で足りないことが実測で示されてからの最終手段。**

**より筋の良い代替:** そもそも front-end にアクセント情報を持つモデル (AivisSpeech 系) を使う方向。§2-1 で述べたとおり、これが最も構造的な解決になる。

### L7. まとめ表

| # | 手法 | コスト | 効果の根拠 | 確度 | 順序 |
| --- | --- | --- | --- | --- | --- |
| **L1** | 行ごとの期待文末イントネーション ラベル | **S** | 設計上の必然 (偽陽性抑制) | — | **最初にやる。他の前提** |
| **L2** | 参照音声を下降終止・落ち着き系に固定 + 参照自体を検査 | **S** | 参照が prosody を転写 / x-vector が韻律の支配的キャリア (2606.05367) | 機序 高 / 定量実測なし | 2番目 |
| **L3** | AivisSpeech の style を おちつき/せつなめ に固定 | **S** | style = 落ち着き発話クリップ群の重心。加えて front-end がアクセント情報を持つ | 機序 中〜高 / 定量実測なし | **Owner 仮説の検証実験として最優先で回す** |
| **L4** | 指示文での抑制 | **S** | 否定形韻律指示の有効性は未確認。intonation 制御は emotion より弱い | **低** | 安いので試すが期待しない |
| **L5** | N テイク選抜 (PASQA 順位 + 語尾 soft signal) | **M** | PASQA SRCC 0.828。著者が順序付け用途と明言 | **高 — 本命** | 3番目、指標整備後 |
| **L6** | pyworld での語尾コンター補正 | **M** | 技術的には可能だが provenance を壊し、根本原因も直らない | 中 / **非推奨寄り** | 最終手段 |

---

## 5. AivisHub 仮説の検証材料 — まとめ

Owner の「『せつなめ』『おちつき』系スタイルの方が語尾の変な上昇が少ない」という観察について、支持材料・反証材料・未検証点を整理する。

**支持材料:**
1. **style ベクトルの作られ方が機序を説明する。** SBV2 の style は学習音声の style embedding をフォルダ分け/クラスタで平均した重心。「おちつき」は落ち着いて発話されたクリップ群の重心なので、その群の文末 contour 統計を条件づけとして持ち込む
2. **front-end の差がもっと大きい可能性。** AivisSpeech / SBV2 は pyopenjtalk 由来のアクセント句・核情報を明示的に持つが、Qwen3-TTS / VoxCPM2 / Chatterbox / CosyVoice は持たない。§2-1 が第1位要因である以上、これが「AivisHub の方がマシ」の主因である可能性が高い
3. **高ピッチ・高覚醒条件で合成が不安定になる傍証** (KazEmoTTS)。落ち着き系スタイルは低覚醒側なので安定する方向

**反証・注意材料:**
1. **`intonationScale` は Owner が期待するものと違う。** AivisSpeech ではこれは「話者スタイルの感情表現の強さ」であってピッチレンジではない。Neutral では無視される
2. **モーラ単位のピッチ編集はできない** (`Mora.pitch` は dummy 0.0)。VOICEVOX と混同しないこと
3. **`enable_interrogative_upspeak` は常に無視される。** 語尾上げを API で明示的に off にする手段が無い

**未検証点 (自前で取るべきデータ):**
- 同一モデル・同一 line・同一 seed で **style だけを切り替えた** `final_f0_interval_st` の分布比較。AivisSpeech なら統制の取れた比較が非常に安く取れる。**Owner 仮説を直接検証する最短経路であり、§3-A の閾値校正データも同時に得られる**
- 同じ比較を PASQA スコアでも取れば、「style は語尾に効くのか、アクセント核にも効くのか」を分離できる

---

## 6. 出典一覧

**L2 日本語・日本語韻律の音声学**

- Idemaru, K., Wei, P., & Gubbins, L. (2019). Acoustic Sources of Accent in Second Language Japanese Speech. *Language and Speech*. https://journals.sagepub.com/doi/10.1177/0023830918773118 — tone が L2 日本語の accent rating を最も頑健に予測
- Muradás-Taylor, B. (2022). Accuracy and Stability in English Speakers' Production of Japanese Pitch Accent. *Language and Speech*. https://journals.sagepub.com/doi/10.1177/00238309211022376 — L1 英語話者の L2 日本語アクセント型は不安定
- 前川喜久雄・五十嵐陽介・菊池英明・米山聖子・小磯花絵. 『日本語話し言葉コーパス』のイントネーションラベリング (X-JToBI) Version 1.1. 国立国語研究所. https://clrd.ninjal.ac.jp/csj/manu-f/intonation.pdf — BPM の taxonomy
- Venditti, J. The J_ToBI model of Japanese intonation. http://www.cs.columbia.edu/~jjv/pubs/jtobi-webversion.doc — 平叙文は L% + 発話全体の downtrend
- 郡史郎. 日本語のイントネーション (研究文献目録). http://corismus.com/intonation/index.html — 疑問型上昇調 / 強調型上昇調 / 平叙文末の下降増大現象
- 郡史郎. 日本語イントネーションについてのいくつかの聴取実験. 大阪大学. http://www.lang.osaka-u.ac.jp/~caris/articles/日本語イントネーションについてのいくつかの聴取実験.pdf
- 国際交流基金. 日本語アクセントの知識をアップデートしよう！—「高低」から「下がり目」へ. https://www.jpf.go.jp/j/project/japanese/teach/tsushin/research/202503.html
- 東京外国語大学 言語モジュール. アクセントとイントネーション. https://www.coelang.tufs.ac.jp/mt/ja/pmod/practical/03-02-01.php — アクセント型を保った疑問上昇 vs 英語式の全体上昇
- 須藤潤. 中国語母語の日本語学習者の発話における文節末の「て形」のイントネーションと聞き手の反応. 日本語教育学会. https://conference.wdc-jp.com/jass/49/contents/common/doc/2-3.pdf
- Peters, B. et al. (2008). Duration and F0 interval of utterance-final intonation contours in the perception of German sentence modality. *Interspeech 2008*. https://www.isca-archive.org/interspeech_2008/peters08_interspeech.html — **2 semitone 以上 + 50 ms 以上の有声で疑問と一意に知覚。F0 interval が slope より予測力が高い**
- 鹿島央ほか. Japanese Mora-Timing: A Review. https://www.researchgate.net/publication/12232277_Japanese_Mora-Timing_A_Review
- A comparison of rhythm metrics for L2 speech (2022). https://www.researchgate.net/publication/360794161_A_comparison_of_rhythm_metrics_for_L2_speech — %V, Δ, Varco, rPVI, nPVI, CCI
- The Contribution of Prosody to the Perception of Foreign Accent. https://www.researchgate.net/publication/6510939 — prosody vs segments は inconclusive

**自動評価・検出**

- **PASQA: Pitch-Accent-Focused Speech Quality Assessment Model Trained on Synthetic Speech with Accent Errors** (INTERSPEECH 2026, LY Corporation). arXiv:2606.20137. https://arxiv.org/abs/2606.20137 / コード (CC0): https://github.com/lycorp-jp/PASQA / 重み: https://huggingface.co/ly-corporation/PASQA — **SRCC 0.828 / KTAU 0.614。入力は 16kHz 音声 + カタカナモーラ列**
- **Investigating Human-Model Discrepancies in Speech Quality Assessment via Acoustic and Prosodic Perturbations.** arXiv:2606.19951. https://arxiv.org/html/2606.19951 — **MOS 予測器はアクセント誤りに 0.1 点未満しか反応しない (人間は 1.84 点低下)。mean F0 と r=-0.458〜-0.788 の負相関**
- SHEET: A Multi-purpose Open-source Speech Human Evaluation Estimation Toolkit (Interspeech 2025). https://www.isca-archive.org/interspeech_2025/huang25g_interspeech.pdf / https://github.com/unilight/sheet
- Prosody Labeling with Phoneme-BERT and Speech Foundation Models. arXiv:2507.03912. https://arxiv.org/pdf/2507.03912
- Kurihara et al. (2024). Integrating Pronunciation Dictionaries and Accent Sandhi (Interspeech 2024). https://www.isca-archive.org/interspeech_2024/kurihara24_interspeech.pdf

**ツール**

- pyopenjtalk (MIT / 依存は Modified BSD). https://github.com/r9y9/pyopenjtalk — v0.3.0 以降 `run_marine=True`
- pyopenjtalk-plus / marine-plus (Windows 対応). https://github.com/tsukumijima/pyopenjtalk-plus / https://github.com/tsukumijima/marine-plus
- PyWORLD (WORLD vocoder Python wrapper). https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder — `dio` / `stonemask` / `cheaptrick` / `d4c` / `synthesize`

**多言語 TTS の foreign accent 問題**

- METTS: Multilingual Emotional Text-to-Speech by Cross-speaker and Cross-lingual Emotion Transfer. arXiv:2307.15951. https://arxiv.org/abs/2307.15951 — foreign accent problem と multi-scale emotion modeling による分離
- DiCLET-TTS: Diffusion Model based Cross-lingual Emotion Transfer for TTS. arXiv:2309.00883. https://arxiv.org/pdf/2309.00883
- Learning to Speak Fluently in a Foreign Language: Multilingual Speech Synthesis and Cross-Language Voice Cloning. arXiv:1907.04448. https://arxiv.org/pdf/1907.04448
- Chatterbox Issue #346: Native Code-Switching Support for Mixed languages. https://github.com/resemble-ai/chatterbox/issues/346 — 単一 `language_id` の制約
- MINT-Bench: A Comprehensive Multilingual Benchmark for Instruction-Following Text-to-Speech. arXiv:2604.17958. https://arxiv.org/pdf/2604.17958 — emotion / speed / pitch / intonation / paralinguistic の各次元を評価
- Measuring Prosody Diversity in Zero-Shot TTS: A New Metric, Benchmark, and Exploration. arXiv:2509.19928. https://arxiv.org/html/2509.19928v1
- Exact Prosody Cloning in Zero-Shot Multispeaker Text-to-Speech. arXiv:2206.12229. https://ar5iv.labs.arxiv.org/html/2206.12229
- KazEmoTTS: A Dataset for Kazakh Emotional Text-to-Speech Synthesis. arXiv:2404.01033. https://arxiv.org/pdf/2404.01033 — 高ピッチ側の感情状態ほど ground truth 類似度が低い

**Style-Bert-VITS2 / AivisSpeech**

- Style-Bert-VITS2 (litagin02). https://github.com/litagin02/Style-Bert-VITS2 — style_vectors.npy、フォルダ分け/クラスタによる style 生成、style 強度の連続指定
- Style-Bert-VITS2 技術解説 (litagin, Zenn). https://zenn.dev/litagin/articles/8c6edcf6b6fcd6 / https://zenn.dev/litagin/articles/034819a5256ff4 — CLAP 廃止 → 全結合 style embedding、WavLM discriminator、gin_channels 256→512
- **AivisSpeech-Engine README (VOICEVOX ENGINE との差分).** https://github.com/Aivis-Project/AivisSpeech-Engine/blob/master/README.md — **`Mora.pitch`/`consonant_length`/`vowel_length` は常に dummy 0.0、`intonationScale` は感情表現の強さ、`tempoDynamicsScale` は独自、`enable_interrogative_upspeak` と `pauseLength` は常に無視**
- AivisHub. https://hub.aivis-project.com/ — スタイル例: ノーマル / ふつー / あまあま / おちつき / からかい / せつなめ
- Comparative Evaluation of Expressive Japanese Character TTS with VITS and Style-BERT-VITS2. arXiv:2505.17320. https://arxiv.org/html/2505.17320v2

**リポジトリ内の関連ドキュメント (本調査で参照・一部修正提案)**

- `docs/research/expressiveness/methods.md` — 手法5-① の「AudioQuery のモーラ単位編集」は AivisSpeech では不可 (§5)。手法6 の UTMOSv2 は nativeness 判定には無効 (§3-D)
- `docs/research/n3-pilot/report/pilot-report.md` — 既存6韻律 feature の LOLO 結果 (すべてほぼランダム)、`explicit_reading_mismatch` の adoptable FRR 0.505
- `pipeline/src/gaya_pipeline/qc_runtime.py` `_analyze_prosody` — §3-A の追加先
- `pipeline/src/gaya_pipeline/qc.py` `_content_gate` / `GATE_POLICY_VERSION = "take-gates-v2"` — gate 化時にバージョン更新が必要
- `pipeline/src/gaya_pipeline/pilot.py` `FeatureName` — feature 追加には pilot-set v2 が必要
