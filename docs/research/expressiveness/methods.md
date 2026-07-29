# 演技的表現と緩急制御の工学手法 — 量産パイプライン向け調査

**調査日:** 2026-07-28 / **対象:** gaya-bench (RPG モブ NPC ガヤ音声量産) / **実行環境前提:** Windows 11 native, RTX 4070 Ti 12GB
**スコープ:** 工学手法とその実用性評価に限定。感情理論・データ資源は別調査担当。

---

## 1. 総括

- **「朗読っぽい棒読み」の第一容疑者は、モデルの表現力ではなく現行 Qwen3-TTS アダプタの結線である。** `pipeline/src/gaya_pipeline/adapters/qwen3_tts.py` は VoiceDesign instruct を `声質 / 性格` だけから組み立て、参照クリップとして `REFERENCE_TEXT = "こんにちは。今日はとても良い天気ですね。"` という中立・平叙・丁寧体の1文を毎キャラ1本だけ生成し、153行すべてをそこからクローンしている。シナリオの `emotion` / `intensity` / `delivery` は Qwen 経路で**一切使われていない** (Irodori アダプタは使っている)。つまり「最も棒読みな1文」を声の定義そのものに焼き込んでいる。
- この推測は理論ではなく実証で裏づけられる。arXiv:2606.05367 は**まさに Qwen3-TTS-12Hz-1.7B-Base を対象に**4オペランドの消去実験を行い、感情韻律の支配的キャリアが ECAPA-TDNN の **x-vector (話者埋め込み)** であることを特定した。決定打は `full_swap` 条件 — 怒りの発話の codec トークン全部に中立 x-vector を組み合わせると、出力は中立と区別不能な穏やかな音声になる。**LM は codec トークンの感情を無視し、x-vector の方向に従う。**
- 同じ論文が、Base への LoRA/フルFT (単一話者・単一感情・約30分) は「ノイズ」か「素のモデルと区別不能な穏やかな音声」の2レジームしか生まず、制御可能な感情音声の中間レジームが存在しないことを示した。**微調整は本プロジェクトで最も費用対効果が悪い。**
- Qwen3-TTS Base に per-line instruct が効かないのは仕様。公式モデル表で Base は instruct 非対応、HF ディスカッションでも「クローン声への instruct は何もしない」「Base に対する voice dataset の finetune しか方法がない」と報告されている。現行 `emotion=false` の判定は正しい。
- 一方、**そのままの構成で効く打ち手が3つ揃っている**: (a) 参照クリップを「演技済み」にする感情別ボイスバンク、(b) x-vector task arithmetic (訓練不要・コード公開・α で強度連続制御)、(c) N テイク生成 + 自動選抜。いずれも 12GB に収まり、モデル差し替えを伴わない。
- ショートリスト内では **VoxCPM2 の Controllable Cloning** (参照音声でクローンしつつ style instruction に従う) が唯一「任意の声 × per-line 演技指示」を同時に満たす。Apache-2.0、公式 BF16 VRAM 約8GB。**Qwen の構造的制約に対する正面からの代替案。**
- Irodori-TTS の絵文字 45種は、本プロジェクトの emotion enum とほぼ 1:1 で対応し、しかも **緩急を直接指定できる唯一の記法** (⏩ 早口 / 🐢 ゆっくり / ⏸️ 間 / 💥 強調、繰り返しで強度up)。日本語専用・MIT。
- Step-Audio-EditX は能力面では最適解 (感情/話法/パラ言語/速度の後編集、反復編集で精度向上、**2025-11-28 から日本語対応**、12GB 最小・4bit 6-8GB) だが、**重みライセンスは今日時点でも未解決** — HF モデルカードは "the code in this open-source repository is licensed under the Apache 2.0 License" と*コードについてのみ*述べ、HF API の license フィールドは空。保留判断の維持が妥当。

---

## 2. 手法比較表

評価軸: 効果期待度 (棒読み解消への寄与) / 実装コスト / 量産適性 (153クリップ規模での再現性・自動化しやすさ) / 12GB 可否 / 権利面。
効果期待度は ◎ > ○ > △ > ×。実装コストは S (数時間) / M (数日) / L (1週間超)。

| # | 手法カテゴリ | 具体策 | 効果期待度 | 実装コスト | 量産適性 | 12GB | 権利面 |
|---|---|---|---|---|---|---|---|
| **1** | **指示・プロンプト工学** | ①Qwen VoiceDesign instruct に emotion/intensity/delivery を注入し**感情別ボイスバンク**化 (キャラ×感情でボイスID発行)<br>②参照文を中立平叙文から**感情に合った演技文**へ差し替え<br>③Irodori 絵文字45種 + caption<br>④CosyVoice3 `inference_instruct2` (日本語指示文可)<br>⑤Chatterbox `exaggeration`/`cfg_weight` | ◎ (①②)<br>◎ (③)<br>○ (④⑤) | **S** (①②③はアダプタ内の文字列生成のみ) | ◎ 決定論的・キャッシュ可。ただし①は声IDが感情数だけ増え音色ドリフト管理が要る | ○ 現行のまま | ○ Qwen/CosyVoice Apache-2.0、Irodori MIT。VoiceDesign 生成声が実在人物に似た場合は声ID不採用の既存運用を継続 |
| **2** | **演技参照音声によるスタイル転写** | 感情×強度の**アンカー音声グリッド** (3-10秒×12感情×3強度) を `assets/voices` に登録し、行の emotion/intensity でアンカーを選択。Qwen は ICL モード (ref_audio+ref_text) 必須、GPT-SoVITS は感情別参照 + 複数参照融合、VoxCPM2 は Controllable Cloning で参照+指示の併用 | ◎ | **M** (アンカー素材の用意が本体) | ◎ 一度作れば行→アンカーのマッピングは機械的 | ○ | △ **アンカー音声の出所が最大の論点**。自前録音か許諾済み素材のみ。VoiceDesign 生成物をアンカーにすれば権利は閉じる |
| **3** | **人間ガイド→VC** | 自分で演技録音 → RVC v2 (要 10-20分の対象音声で学習) または Seed-VC V2 (ゼロショット 1-30秒、FT は1発話/100step) で声質だけ差し替え | ◎ (演技品質は最高) | **M〜L** (録音工数 + VC 環境) | △ 行数に比例して人間の録音時間が線形に増える。ガヤは5-30文字なので153行で1-2時間程度だが、**「TTSベンチ」から「VCベンチ」へ企画が変質する** | ○ RVC/Seed-VC とも小型で余裕 | ○ ガヤ演技は Owner 自身の声 → クリーン。**ただし RVC エコシステムの既存配布モデル (声優・著名人) は絶対に使わない**。変換先音色は VoiceDesign 生成声か許諾素材に限定 |
| **4** | **音声編集・感情変換パス** | ①Step-Audio-EditX で生成後に感情/話法/パラ言語/速度を後編集 (反復2-3回で精度が飽和)<br>②EmoSteer-TTS 型 activation steering (flow-matching DiT 限定: F5/E2/CosyVoice2 → **Irodori-TTS も rectified-flow DiT なので適用余地**)<br>③TED-TTS 型の発話内感情・duration steering | ◎ (①)<br>○ (②③、要移植) | **M** (①: 推論足すだけ)<br>**L** (②③: 研究実装の移植) | ○ ①は行単位バッチ可、ただし生成の後段にもう1モデル分の時間が乗る | ○ ①12GB最小/16GB推奨、4bit 6-8GB。**4070Ti 12GB は最小要件ちょうど** | ✗ **①は重みライセンス未記載のまま。保留継続が妥当** ②③はコード公開だが論文実装 |
| **5** | **明示的韻律制御** | ①AivisSpeech の `speedScale`/`pitchScale`/`intonationScale` (=選択スタイルの感情表現強度 0.0-2.0)/`tempoDynamicsScale` (0.0-2.0, テンポ揺らぎ強度)/`volumeScale`。`Mora.consonant_length` / `vowel_length` / `pitch` は編集不能<br>②MFA (日本語 境界誤差 <15ms) で強制アライン → フレーズ分割 → 区間別タイムストレッチ + 間の挿入<br>③Praat/Parselmouth の TD-PSOLA によるピッチ・尺再形成 (自然さで WORLD より優位)、pyworld で F0 再合成 | ○ (①は発話全体の速度・スタイル強度・緩急に作用)<br>△ (②③は緩急は付くが**演技は付かない**) | **S** (②③は CPU・スクリプトのみ)<br>**S** (①は既存 REST API) | ○ ①は公開 API に seed 指定がなく反復比較が必要<br>◎ ②③は決定論的 | ◎ ①は CPU/RAM 1.5GB、②③も CPU | ○ AivisSpeech は ACML-1.0 公式モデル限定運用を継続。Praat/MFA/pyworld はツール側の権利問題なし |
| **6** | **マルチテイク自動選抜** | 1行 N テイク (seed/温度を振る) → ①Whisper large-v3 で CER/WER (誤読・欠落を棄却) ②emotion2vec_plus_large の目標感情セントロイドとの cos (=EECS, **演技度**) ③UTMOSv2 fusion_stage3 (自然さ, VoiceMOS2024優勝) ④ECAPA/WavLM SECS (キャラ同一性) ⑤pyworld で F0/energy/話速の分散 (**低分散=棒読み検出**) → 加重で最良テイク採用 | ◎ | **M** (スコアラ1モジュール + manifest への選抜パス) | ◎ **完全自動化前提の設計。モデル非依存で全アダプタに効く** | ○ 評価器はいずれも小型。コストは N 倍の生成時間のみ (Qwen は数秒/行、N=5×153 で現実的) | ○ 評価器はすべて研究公開モデル |
| **7** | **微調整 (LoRA/FT)** | 演技コーパスで Qwen3-TTS Base を LoRA/FT | **×** | **L** | ✗ | △ | △ 学習素材の権利処理が別途必要 |

### 補足: 7 に × を付けた根拠 (一次実験結果)

AivisSpeech Engine 1.2.0 では `Mora.consonant_length` / `vowel_length` /
`pitch` は常にダミー値 `0.0` で、変更しても音声合成結果へ反映されない。
`intonationScale` も全体のピッチレンジではなく、選択した話者スタイルの
感情表現強度である。全スタイル平均の Normal ではこの値が無視されるため、
語尾下降を直接制御するパラメータとして扱わない。

arXiv:2606.05367 §3.1.1 / §4.1 Step 1 が Qwen3-TTS-12Hz-1.7B に対して直接実施している。

- 条件: フル FT (lr ∈ {2e-6, 2e-5}) と LoRA (q/k/v/o_proj, r=64, α=128, 約29M trainable / attention + `codec_head` + 15 `lm_head` = 約60M trainable)、lr スイープ {1e-6 … 1e-4}、epoch {4…39}、ESD 話者0017 の単一話者・単一感情 約30分。
- 結果: 「高 lr → ノイズ」「低 lr → ベースと区別できない穏やかな音声」の**2レジームのみ。知的可能な感情音声が出る中間レジームは観測されなかった。** LoRA + codec_head では意図した感情への方向性を伴わない汎用的な表現力 (笑いの発生など) が出るだけ。
- 著者の解釈: Qwen3-TTS には CFS2/VITS のような prosody の専用サブネットワークが存在せず、韻律は x-vector と ICL トークンに条件づけられた自己回帰継続から創発するため、重みへの介入では条件づけ自体が変わらない。
- 同論文が引く Purwar & Choudhary (2026): LM-TTS の LoRA は**データの音響的多様性が低いと loss-quality divergence を起こす**。
- 著者自身の留保: これは単一話者・単一感情という低多様性レジームでの否定結果であり、大規模な多話者・多感情 FT の可能性は排除していない。ただし参照点として IndexTTS2 は 8×A100 で3週間、MiniMax-Speech は感情カテゴリごとに独立 LoRA。**いずれも本プロジェクトの射程外。**
- 低リソース FT の一般的な目安 (Rasa, モジュラー型アーキテクチャ): 表現的データ 15分以上で MUSHRA "Fair"、30分以上で "Good"。ただし LM-TTS には外挿できない。

---

## 3. 本プロジェクトへの推奨

### 3-1. 短期 — 今のパイプラインに足せるもの (モデル差し替えなし)

**S1. 参照クリップを「演技済み」にする (最優先・最小工数)**

現行アダプタの `REFERENCE_TEXT = "こんにちは。今日はとても良い天気ですね。"` は、TTS に与えうる最も棒読みな1文である。これがキャラ声の定義に焼き込まれ、`full_swap` 実験が示すとおり LM はこの x-vector の方向に従い続ける。

- (a) VoiceDesign instruct に `line.emotion` / `intensity` / `delivery` を注入する。現状 `声質 / 性格` しか渡していない (Irodori アダプタは既に `感情: X（強度 n/3）` `演技: <delivery>` を組み立てており、**同じ変換ロジックが Qwen 側に無いだけ**)。
- (b) 参照文を感情別に差し替える。`cheerful` なら弾む呼びかけ、`shout` なら実際の叫び文、`whisper` なら囁き文 — VoiceDesign に「その感情で喋っている参照音声」を作らせる。
- (c) 結果としてキャラ×感情の**感情別ボイスバンク**になる。Irodori コミュニティが独立に到達している定石 (VoiceDesign で望む声を作り、それを標準版の参照音声として使う) と同じ形。
- リスク: 感情ごとに声IDが分かれるため、同一キャラの音色ドリフトが起きうる。S2 がこれを直接緩和する。

**S2. x-vector task arithmetic (訓練不要・α で強度連続制御)**

`x_new = x(target, neutral) + α · τ_emo`、`τ_emo = E_s[x(s, emo)] − E_s[x(s, neutral)]`。

- **本プロジェクトの本番モデルそのもの (Qwen3-TTS-12Hz-1.7B-Base) で検証済み。** 追加学習なし、追加コストは 2048次元ベクトルの加算1回。
- 実測 (EN held-out, `avg4spk`, α*): angry EECS 0.925 (base 0.539 / 天井 0.957)、happy 0.687 (base 0.425)、sad 0.761 (base 0.540)。平均 ΔEECS **+0.288**、6組合せすべてで Δ ∈ [+0.20, +0.39]。同一性 SECS_W 0.907-0.926 (≥0.88 維持)、UTMOSv2 3.11-3.33 でベースから劣化せず、WER_norm 約5-7% は人間の天井と同等。
- **α がシナリオの `intensity` フィールドに直接マップする。** α ∈ {0, 0.5, 1, 1.5, 2, 2.5} で掃引済み。α を上げると感情↑・同一性↓のトレードオフが連続的に動く。
- τ は多話者平均 (`avg4spk`: 4話者×50発話/感情) を使うこと。単一話者 τ は元話者の音色残差を運び込み、同一性が 0.912→0.810 に落ちる。
- 感情方向のノルムは x-vector ノルムのわずか 15.4%、cos(neutral, angry)=0.988 — **恒等軸とほぼ直交する微小摂動**。だから線形介入が壊れずに効く。
- コード公開: `github.com/danielbrito91/xvector-emotion-arithmetic` (uv 管理、`data/tau/*.pt` に事前計算 τ 約450KB、`scripts/deploy/emotionize_audio.py`)。**ライセンス未記載なので取り込み前に要確認。**
- 制約: (i) 配布 τ は angry/happy/sad の3つのみ、本プロジェクトは12感情。(ii) **日本語未検証** (EN→PT-BR の言語横断は成功、+0.09)。(iii) アダプタの `create_voice_clone_prompt` 内部に x-vector 注入フックが要る。

**S3. マルチテイク自動選抜**

モデル非依存で全アダプタに効き、リスクが最も低い。N=3〜5、スコアは Whisper CER (棄却) / emotion2vec EECS (演技度) / UTMOSv2 (自然さ) / SECS (同一性) / F0・energy・話速の分散 (棒読み検出)。先行例は Voxtral TTS (rejection sampling + DPO)、TADA (話者埋め込みヘッドによるオンライン棄却)、SER データ拡張パイプライン (ASR-WER + 話者類似度 + 感情フィルタ)、CoT-TTS チャレンジ (継続時間・感情強度・ラウドネスでフィルタ)。**副産物として、モデル比較ベンチの客観指標がそのまま手に入る** — 現行ベンチの評価軸強化としても価値がある。

**S4. Irodori 経路の緩急記法を使い切る**

Irodori アダプタは既に emotion→絵文字マップを持つが、45種の中には**緩急そのものを指定できる記法**がある: ⏩ 早口 / 🐢 ゆっくり / ⏸️ 間 / 💥 強調 / 👂 囁き / 😱 叫び / 🤭 含み笑い / 🥴 酔い / 😖 苦痛。繰り返しで強度が上がる。`intensity` を絵文字の反復数に、`delivery` の緩急表現を ⏩/🐢/⏸️ に写像すれば、**発話内の緩急を明示制御できる唯一の現行経路**になる。

**S5. 後処理での緩急付与 (CPU・決定論的)**

MFA (日本語 境界誤差 <15ms) で強制アライン → フレーズ分割 → 区間別タイムストレッチ + 間の挿入。ピッチ・尺の再形成は Praat/Parselmouth の TD-PSOLA (自然さで WORLD より優位)。**明確な限界: 尺と間は変えられるが、F0 レンジや声質といった感情の音響相関は変わらない。緩急は付くが演技は付かない。** S1/S2 の補助として、単独の解決策としては扱わない。

### 3-2. 中期

**M1. VoxCPM2 の Controllable Cloning を演技経路の本命として前倒し評価する**

現状の実装順 (波3) より優先度を上げる価値がある。**参照音声で話者をクローンしつつ style instruction に従う**という、Qwen Base が構造的にできないことを1モデルで満たす。voice design と controllable cloning が専用 control head や style encoder なしに同一のシーケンス構成で実装されている。Apache-2.0 (重み含む・商用可明示)、公式 runtime 表で BF16 VRAM 約8GB (ショートリスト唯一の L1 根拠)、48kHz。日本語固有名詞の評価が未了なのが唯一の穴。

**M2. 感情×強度アンカー音声グリッドを資産化する**

`assets/voices/metadata.yaml` を拡張し、キャラID × emotion × intensity でアンカーを引けるようにする。GPT-SoVITS は感情別参照 + 複数参照融合をネイティブに持ち、Seed-VC V2 は AR モジュールでアクセント・感情変換を担当 (無効化すると V1 相当の音色のみ変換)。アンカーの出所は VoiceDesign 生成物か自前録音に限定すれば権利が閉じる。**S1 の感情別ボイスバンクはこの資産の第一形態**なので、短期施策がそのまま中期資産に育つ設計にしておく。

**M3. Step-Audio-EditX のライセンス解決を追跡し、解決したら後編集段として組み込む**

能力は本課題に対する最適解に近い: 感情 (angry/happy/sad/fear/surprised…)、話法 (whisper/act_coy/child/older/serious…)、パラ言語 (呼吸・笑い・ため息・咳・言い淀み)、速度 (faster/slower/more faster/more slower)、denoise。**反復編集で感情・話法の精度が1回目から上がり、2-3回で飽和**するという性質は、自動リトライループと相性が良い。日本語は 2025-11-28 版から対応。12GB 最小・16GB 推奨 (4070Ti は最小ちょうど)、4bit 量子化で 6-8GB。**ブロッカーは今日も未解消** — HF モデルカードの Apache 2.0 記述は "the code in this open-source repository" にかかっており、HF API の license フィールドは空。**現行の保留判断を変える一次根拠は見つからなかった。**

**M4. 人間ガイド演技 → VC を「別トラック」として企画判断に上げる**

2025-2026 のゲーム音声制作ガイドでは、スクラッチ VO + style transfer (SoundID VoiceAI, Respeecher 等) が barks 制作の標準的手法として記述されている。品質面では確実に最高だが、**本プロジェクトを「TTS ベンチ」から「VC ベンチ」に変質させる**ため、Owner 判断事項。技術的には RVC v2 (対象音声 10-20分で学習、RMVPE により低品質入力でもピッチ追跡が安定、元話者の抑揚・感情を保持) と Seed-VC V2 (ゼロショット 1-30秒、CFM 67M + AR 90M、RTX 3060 Laptop で約430ms) の2択。Seed-VC は難条件で明瞭度・ピッチ安定性が落ちるとの報告があり、Seed-VC の音色シフタを RVC ベースに置換した方が発音精度が上がるという知見もある。**RVC 界隈の既存配布モデルは権利上使えない**点を明記して起案すること。

### 3-3. 実験して確かめるべき仮説

| # | 仮説 | 検証方法 | 判定基準 | コスト |
|---|---|---|---|---|
| **H1** | 「棒読み」の主因は中立参照文である | 同一キャラ・同一行を「中立参照文でクローン」vs「感情に合った参照文でクローン」で生成し比較 | Owner 主観 + EECS 差 | **S — 最優先。これだけで結論が変わる可能性がある** |
| **H2** | x-vector τ は日本語でも機能する | ESD (英語) 由来の配布 τ を日本語キャラ声に α スイープで適用。論文は EN→PT-BR で言語横断性を示したが日本語は未検証 | EECS 上昇 かつ SECS_W ≥0.88 かつ CER 非悪化 | **S-M** |
| **H3** | 12感情ぶんの τ を自前で作れる | 日本語感情コーパスから多話者セントロイド差で τ を抽出 (論文レシピ: 4話者×50発話/感情)。データ資源は別調査担当の範囲 | angry/happy/sad の配布 τ と同等の ΔEECS | **M** |
| **H4** | 複数 τ の線形合成で中間感情が作れる | τ_happy + τ_surprised 等。**論文の future work であり未検証。** 多話者平均で方向の相関が下がる (cos 0.58→0.24) ことは合成に有利な兆候 | 合成感情の EECS が単独 τ を下回らない | **M — 成功すれば12感情を3-4本の τ で賄える** |
| **H5** | 韻律分散指標で棒読みを機械判定できる | 既存 153クリップの F0 std / energy std / 話速分散を測り、Owner の「棒読み」判定と相関を取る | 相関が出れば S3 の重み設計が根拠を持つ | **S — 既存アセットのみで完結、生成不要** |
| **H6** | EmoSteer-TTS 型 activation steering が Irodori-TTS に効く | Irodori は rectified-flow DiT (joint-attention DiT blocks) であり、実証済みの F5-TTS/E2-TTS/CosyVoice2 と同じ flow-matching クラス。emotion2vec で top-k トークンを選び difference-in-means で steering vector を作る | 感情変換が効き、α>3 で崩壊する既知の挙動を再現 | **L — 探索的。ただし成功すれば日本語専用モデルに連続的な感情強度ノブが付く** |
| **H7** | VoxCPM2 の style instruction が日本語で per-line に効く | 同一参照音声 × 12感情の指示文で生成し、指示追従を測る | EECS が指示感情方向に動く | **M — M1 の判断材料** |

### 参考: 評価対象外とした選択肢

- **IndexTTS2**: 感情と音色の分離、感情ベクトル8次元 (happy/angry/sad/afraid/disgusted/melancholic/surprised/calm)、感情参照音声 + `emo_alpha`、自然言語感情記述の3経路を持ち、機能面では非常に魅力的。**しかし HF の公式言語タグは en/zh のみで日本語は非公式**、かつライセンスが研究・個人利用向けで商用に制限があるとの複数報告があり、公式 LICENSE の一次確認もできなかった。本プロジェクトの G1/G2 ゲートに抵触するため非推奨。
- **Unsloth 経由の TTS ファインチューン** (Orpheus 3B の `<laugh>`/`<sigh>` 等の感情トークン、Spark/Llasa/CSM 対応、QLoRA 7B が RTX 3060 12GB で可): 12GB では技術的に可能だが、**ショートリストのどのモデルもカバーせず、日本語対応も弱い**。カテゴリ7の否定的評価を覆すものではない。
- **SSML**: ショートリストの LLM-TTS 系はいずれも SSML 非対応。LLM で韻律タグを自動付与するパイプラインの研究はあるが、受け手のエンジンが SSML/AudioQuery 対応である必要があり、実質 AivisSpeech 系のみ。
- **SpeechEditBench** (2026, 指示追従音声編集ベンチ): Step-Audio-EditX 等を評価しているが **ZH/EN のバイリンガルで日本語を含まない**ため、日本語編集品質の根拠には使えない。

---

## 4. 出典一覧

### 一次: 本プロジェクト対象モデルの直接的知見

- [Task-Vector Arithmetic for Emotional Expressivity Control in Language-Model-Based Text-to-Speech (arXiv:2606.05367v2, 2026-06-23)](https://arxiv.org/pdf/2606.05367) — Qwen3-TTS-12Hz-1.7B に対する4オペランド消去実験、x-vector centroid arithmetic、EN/PT-BR 実測表、LoRA/FT 否定結果
- [danielbrito91/xvector-emotion-arithmetic](https://github.com/danielbrito91/xvector-emotion-arithmetic) — 上記の再現コード、事前計算 τ、deploy スクリプト
- [QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) — Base/VoiceDesign/CustomVoice のモデル別 instruct 対応、ICL vs `x_vector_only_mode`
- [Qwen3-TTS-12Hz-1.7B-CustomVoice · How to customize emotion in a cloned voice? (Discussion #38)](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice/discussions/38) — Base のクローン声に instruct が効かない旨の報告と回避策議論
- [Emotion & Instruction Control for Voice Cloning (QwenLM/Qwen3-TTS Discussion #218)](https://github.com/QwenLM/Qwen3-TTS/discussions/218) — 感情制御の feature request、instruct 方式と inline tag 方式の支持が拮抗
- [Aratako/Irodori-TTS-600M-v3-VoiceDesign](https://huggingface.co/Aratako/Irodori-TTS-600M-v3-VoiceDesign) — caption/絵文字/参照音声の3条件、Semantic-DACVAE 32次元、rectified-flow DiT、MIT + 利用方針
- [Irodori-TTS EMOJI_ANNOTATIONS.md](https://huggingface.co/Aratako/Irodori-TTS-600M-v3-VoiceDesign/blob/main/EMOJI_ANNOTATIONS.md) — 絵文字45種の完全リストと反復による強度制御
- [openbmb/VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) / [VoxCPM2 Technical Report (arXiv:2606.06928)](https://arxiv.org/pdf/2606.06928) — Voice Design と Controllable Cloning、Apache-2.0
- [AivisSpeech Engine 1.2.0 README](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/0a310883265c64f43365fde5593b1296b14ae99b/README.md) — `Mora` の長さ・pitchがダミーであること、`intonationScale` が「スタイルの感情表現の強さ」を意味すること、`tempoDynamicsScale` 等
- [stepfun-ai/Step-Audio-EditX (HF)](https://huggingface.co/stepfun-ai/Step-Audio-EditX) / [GitHub](https://github.com/stepfun-ai/Step-Audio-EditX) — 編集能力、反復編集、日本語対応 (2025-11-28)、12GB 最小、ライセンス記述の範囲
- [RVC-Boss/GPT-SoVITS features wiki](https://github.com/RVC-Boss/GPT-SoVITS/wiki/GPT%E2%80%90SoVITS%E2%80%90features-(%E5%90%84%E7%89%88%E6%9C%AC%E7%89%B9%E6%80%A7)) — v2ProPlus の複数参照音声融合、多言語テキスト感情抽出、RoPE 化
- [Plachtaa/seed-vc](https://github.com/Plachtaa/seed-vc) — V1/V2 構成、`convert-style` と AR モジュールによるアクセント・感情変換、FT 要件

### 研究 (手法の根拠)

- [EmoSteer-TTS: Fine-Grained and Training-Free Emotion-Controllable TTS via Activation Steering (arXiv:2508.03543)](https://arxiv.org/html/2508.03543v1) — flow-matching DiT への activation steering、F5/E2/CosyVoice2 で検証、感情変換/補間/消去
- [TED-TTS: Training-Free Intra-Utterance Emotion and Duration Control for TTS (arXiv:2601.03170)](https://arxiv.org/abs/2601.03170) — 発話内の感情遷移と duration steering (局所 duration 埋め込み + EOS ロジット変調)
- [Voxtral TTS (arXiv:2603.25551)](https://arxiv.org/html/2603.25551v1) — rejection sampling + DPO による WER/話者類似度の後訓練
- [TADA: Speech Modeling via Text-Acoustic Dual Alignment (arXiv:2602.23068)](https://arxiv.org/html/2602.23068) — 話者埋め込みヘッドによるオンライン棄却サンプリング
- [ISCSLP 2026 CoT-TTS Challenge (arXiv:2606.21933)](https://arxiv.org/pdf/2606.21933) — 感情強度・ラウドネス・有効発話長による候補フィルタ
- [Speech emotion recognition using multimodal LLMs and quality-controlled TTS-based data augmentation (Computer Speech & Language, 2025)](https://www.sciencedirect.com/science/article/pii/S0885230825001524) — ASR-WER + 話者類似度 + 感情フィルタの品質制御パイプライン
- [emotion2vec: Self-Supervised Pre-Training for Speech Emotion Representation (arXiv:2312.15185)](https://arxiv.org/html/2312.15185v1) — 9言語で SSL ベースラインを上回る感情表現、EECS の基盤
- [SpeechEditBench (arXiv:2606.01804)](https://arxiv.org/pdf/2606.01804) — 指示追従音声編集のバイリンガル (ZH/EN) ベンチ、Step-Audio-EditX 他を評価
- [Neural Pitch-Shifting and Time-Stretching with Controllable LPCNet (arXiv:2110.02360)](https://arxiv.org/pdf/2110.02360) — TD-PSOLA が自然さで WORLD に優ること、Parselmouth 実装への言及
- [Context-Aware Prosody Correction for Text-Based Speech Editing (arXiv:2102.08328)](https://arxiv.org/pdf/2102.08328) — 編集音声の韻律補正
- [Montreal Forced Aligner and the state of speech-to-text alignment in 2026 (arXiv:2606.18466)](https://arxiv.org/abs/2606.18466) — MFA 3.0 の日本語での平均境界誤差 <15ms
- [Improving French Synthetic Speech Quality via SSML Prosody Control (arXiv:2508.17494)](https://arxiv.org/pdf/2508.17494) / [hi-paris/Prosody-Control-French-TTS](https://github.com/hi-paris/Prosody-Control-French-TTS) — LLM による韻律タグ自動付与パイプライン
- [Rasa: Building Expressive Speech Synthesis Systems in Low-resource Settings (arXiv:2407.14056)](https://arxiv.org/pdf/2407.14056) — 表現データ 15分/30分での MUSHRA 到達水準
- [Maestro-EVC: Controllable Emotional Voice Conversion Guided by References and Explicit Prosody (arXiv:2508.06890)](https://arxiv.org/pdf/2508.06890) / [TRACE-EVC (arXiv:2607.03666)](https://arxiv.org/pdf/2607.03666) / [DurFlex-EVC (arXiv:2401.08095)](https://arxiv.org/pdf/2401.08095) — 感情変換の研究系実装

### 実務・二次情報

- [Qwen3-TTS Voice Cloning Guide 2026: Reference Audio, Voice Design & Accent Tips](https://ocdevel.com/blog/20260302-qwen-tts-voice-cloning) — VoiceDesign の記述長上限 (2048文字)、ペース・ペルソナ指定
- [Irodori-TTSの導入方法・使い方 (くろくまそふと, 2026-04-28)](https://kurokumasoft.com/2026/04/28/irodori-tts/) — 絵文字による感情制御の実例、VoiceDesign と標準版の使い分け
- [Irodori-TTSとは｜絵文字で感情を制御する日本語TTSの使い方・v3](https://www.issoh.co.jp/tech/details/12221/) — VoiceDesign で作った声を標準版の参照音声に使う運用
- [Chatterbox TTS configuration guide](https://apidog.com/blog/chatterbox-tts/) — `exaggeration` 既定0.5、劇的用途で exaggeration≈0.7 + cfg_weight≈0.3、高 exaggeration が発話速度を上げる件
- [ResembleAI/chatterbox-turbo · Exaggeration and cfg_weight not supported in turbo model](https://huggingface.co/ResembleAI/chatterbox-turbo/discussions/22) — turbo 版では当該パラメータが利用不可
- [CosyVoice 3 (arXiv:2505.17589)](https://arxiv.org/html/2505.17589v2) / [CosyVoice3 demo](https://funaudiollm.github.io/cosyvoice3/) — `inference_instruct2`、日本語指示文の例、テキスト由来感情表現での最高性能
- [AI Voice for Games: The 2026 Production Guide for Game Developers (Onepin)](https://onepin.ai/blog/ai-voice-for-games-production-guide-2026) — pre-rendered / runtime / cloning の3モード、barks の位置づけ
- [Voice AI in Game Audio & Film (Sonarworks)](https://www.sonarworks.com/blog/learn/voice-ai-game-audio-film-sound-designers) — スクラッチ VO + style transfer がスタジオ標準である旨
- [RVC-Project/Retrieval-based-Voice-Conversion-WebUI](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) / [RVC v2 概要 (tasarim.ai)](https://tasarim.ai/en/models/rvc-v2) — 10-20分の学習データ要件、RMVPE
- [EZ-VC: Easy Zero-shot Any-to-Any Voice Conversion (arXiv:2505.16691)](https://arxiv.org/pdf/2505.16691) — Seed-VC との UTMOS/話者類似度比較
- [Unsloth TTS Fine-tuning Guide](https://unsloth.ai/docs/basics/text-to-speech-tts-fine-tuning) — 対応 TTS モデル、Orpheus の感情トークン、Elise データセット (約3時間/1200サンプル)
- [index-tts/index-tts](https://github.com/index-tts/index-tts) — IndexTTS2 の感情制御3経路、duration control が本リリースでは未有効、Windows での DeepSpeed/flash-attention の難点
- [IndexTTS2総合レビュー (note/czmilo)](https://note.com/czmilo/n/n6501c72fbd7b) — 8次元感情ベクトル、商用利用制限の指摘
- [Best Open-Weight Text-to-Speech Models 2026 (Presenc AI)](https://presenc.ai/research/best-open-weight-text-to-speech-models-2026) — 2026 時点の open-weight 勢力図
- [StepFun AI Releases Step-Audio-EditX (MarkTechPost)](https://www.marktechpost.com/2025/11/09/stepfun-ai-releases-step-audio-editx-a-new-open-source-3b-llm-grade-audio-editing-model-excelling-at-expressive-and-iterative-audio-editing/) — dual codebook tokenizer、large-margin 合成データによる設計思想
