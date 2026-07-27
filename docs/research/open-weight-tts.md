# オープンウェイトTTSモデル調査レポート — RPGガヤボイス生成用ベンチマーク候補選定

**調査日: 2026年7月27日**
**対象環境: NVIDIA RTX 4070 Ti (VRAM 12GB) / RAM 32GB / Windows 11**
**用途: RPGモブNPCの「ガヤボイス」(中距離で聞こえる短い一言セリフ、群衆環境音声)**

---

## 1. 総括 — 2026年7月時点のオープンウェイトTTS勢力図(日本語対応視点)

2025年後半から2026年前半にかけて、オープンウェイトTTSは「**多言語LLM系ゼロショットクローン**」と「**日本語特化の軽量専用モデル**」の二極化が進んだ。前者の中心は **Qwen3-TTS**(Alibaba, 2026年1月, Apache-2.0)で、3秒クローンに加え自然言語で声質を記述する **VoiceDesign** を備え、日本語を正式10言語の1つとして扱う初の主要中国系モデルとなった。同系統に **VoxCPM2**(OpenBMB, 2026年4月, Apache-2.0, 48kHz)、**CosyVoice 3**(Apache-2.0)、**MOSS-TTS v1.5**(OpenMOSS, 2026年6月, Apache-2.0)が続き、いずれもApache-2.0で商用に安全という点が2026年の最大の変化である。

一方、日本語品質そのものでは日本コミュニティ発の **Irodori-TTS**(Aratako, MIT, v3=2026年)が台頭した。500M〜600Mと極小ながら日本語専用設計で、**絵文字による非言語音・感情制御**とキャプション文による声デザインを両立し、RTF 0.13という速度で日本語音響類似度でFish Speech S2 Proと同等スコアを記録した(2026年6月の第三者音響分析)。従来王者の **Style-Bert-VITS2 / AivisSpeech** は日本語アクセントの正確さで依然トップ評価だが、ゼロショットクローンを持たず「多数のモブ声を量産する」用途とは相性が悪い。

**逆に、品質上位モデルほどライセンスが締まった**のが2026年の重要な傾向である。TTS Arena最上位の **Fish Audio S2 Pro** は2026年3月にコード込みで「Fish Audio Research License」へ移行し商用は別途有償契約が必須、**Higgs Audio V3**(2026年6月)は明確に非商用ライセンス、**IndexTTS-2** も非商用かつ日本語非対応、**Sarashina2.2-TTS**(SB Intuitions)は非商用に加え除去不可の電子透かし付きである。したがって「商用ゲームに収録できる日本語TTS」という条件で絞ると、実質的な候補は **Apache-2.0/MITの中量級モデル群**に限定される。

表現制御では **Step-Audio-EditX**(StepFun, Apache-2.0)が突出しており、感情14種・スタイル32種(roar/shout/murmur含む)・非言語22種(laugh/sigh/giggle/cough/breath等)のタグ体系を持つ。ただし日本語イントネーションが中国語訛りになる報告があり、単体生成よりも他モデル出力への「編集パス」としての運用が現実的。ガヤ用途では **Qwen3-TTSのVoiceDesignで権利クリーンな声を大量設計 → クローンで固定化** というワークフローが最も有望である。

---

## 2. モデル比較表

凡例: 日本語品質の評価は **A(ネイティブ級)/B(実用)/C(訛り・不安定)/-(非対応)**。根拠は各行に明記。VRAMはbf16/fp16推論時の目安。

### 2-A. 商用利用が可能な候補(本命群)

| モデル | 開発元 | 最新版・日付 | パラメータ / VRAM目安 | 日本語品質(根拠) | 感情・演技制御方式 | クローン | コードライセンス | 重みライセンス | 生成物の商用利用 | 特記事項 |
|---|---|---|---|---|---|---|---|---|---|---|
| **Irodori-TTS** (500M-v3 / 600M-v3-VoiceDesign) | Aratako (個人/日本) | v3 = 2026年 (v2は2026年前半) | 500M / 600M、**〜2GB程度** | **A**。2026-06-18の第三者音響分析でComposite 0.6472(Fish S2 Proと同点)、Prosody 0.7710で6モデル中1位 | **絵文字埋め込み**でスタイル+非言語音(笑い・咳・ため息)、加えてキャプション文で演技指定 | ○ ゼロショット(参照音声)+ Speaker Inversion | MIT | MIT | **可** | 日本語専用(他言語不可)。RF-DiT + DACVAE、48kHz。RTF 0.13x(RTX 5060 Ti実測)。**学習データ非開示 → 要確認** |
| **Qwen3-TTS** (12Hz 1.7B: Base / CustomVoice / VoiceDesign、0.6B: Base / CustomVoice) | Alibaba Qwen | 2026-01-22 公開 (arXiv 2601.15621) | 0.6B(2.52GB) / 1.7B(4.54GB)、**実測〜6.5GB** | **B+**。日本語WER 3.823(公式)。Qiita比較で★4/5、Zenn/note複数で「OSSでここまで自然な日本語が出るのか」と高評価。ただし長文・技術語でF0跳ねが出る | **VoiceDesign**: 自然言語で声質+感情を記述(「緊張している若い女性、息成分あり」等)。Base/CustomVoiceは指示文でtimbre/emotion/prosody制御 | ○ **3秒**ゼロショット | Apache-2.0 | Apache-2.0 | **可** | **ガヤ最有力**: VoiceDesignで架空の声を作る→Baseでクローン固定化、で「誰の声でもない」権利クリーンなモブ声を量産可能。ストリーミング97ms。GGUF量子化あり |
| **VoxCPM2** | OpenBMB | 2026年4月 (arXiv初出 2025-09-29) | 2B、**〜8GB** | **B(要検証)**。30言語に日本語を含むがG2P・固有名詞・口語の検証が別途必要との指摘(2026-05-13)。前掲音響分析でASR一致率0.8179で6モデル中1位(内容保持が最良)、ただし抑揚に違和感 | 自然言語記述による**Voice Design**(性別/年齢/トーン/感情/速度)、Controllable Cloningでtimbre維持したまま感情操作 | ○ ゼロショット | Apache-2.0 | Apache-2.0 | **可** | tokenizer-free拡散AR。**48kHz出力**(16kHz参照を超解像)。RTF 0.30(RTX 4090)/0.13(Nano-vLLM) |
| **Step-Audio-EditX** | StepFun AI | 重み2025-11-12、日本語対応2025-11-28、**2026-01-29更新**(性能+4%、非言語タグ追加、学習コード公開) | 3B、**11.5GB実測(RTX4090)** / 4bit 6-8GB | **C+**。日本語対応は公式。ただしZenn実測で「イントネーションが日本語非ネイティブ、中国語っぽい」。日本語参照音声を使えば改善の可能性 → **要検証** | **本調査で最強のタグ体系**: 感情14(happy/angry/sad/fear/surprised/confusion/excited/depressed 等)、スタイル32(**roar / shout / murmur / whisper / child / older / act_coy** 等)、非言語22(**sigh / laugh / chuckle / giggle / cough / breath / snort / 各種驚き・疑問の間投詞**) | ○ ゼロショット(中英川粤が主) | Apache-2.0 | Apache-2.0 (HFモデルカード) | **可** | `[Japanese]`タグで日本語呼び出し。反復編集で感情強度を段階制御。**12GBではギリギリ**、4bit量子化推奨。「生成」より「編集」向きとの評 |
| **MOSS-TTS v1.5 / Realtime / Nano** | OpenMOSS + MOSI.AI | v1.5 = 2026年6月 (arXiv 2603.18090) | 8B(Delay) / **4B(Local-Transformer)** / 1.7B(Realtime) / 0.1B(Nano) | **B(要検証)**。日本語は公式サポート言語に含まれる。日本語での第三者評価は未発見 → **要確認** | 言語タグ、`[pause X.Ys]`による明示的な間制御、句読点追従プロソディ。声デザインは別モデルMOSS-VoiceGenerator(**中英のみ**) | ○ ゼロショット | Apache-2.0 | Apache-2.0 | **可** | 多話者対話生成(MOSS-TTSD)、効果音生成(MOSS-SoundEffect)を含むファミリー。8Bも最適化で8GBに収まるとの記載。RTF 0.51、TTFB 180ms。**言語数の記載がGitHub(31)とHF(20)で不一致 → 要確認** |
| **CosyVoice 3** (Fun-CosyVoice3-0.5B-2512) | Alibaba FunAudioLLM | 2025年12月 | 0.5B、**〜4GB** | **B-**。9言語に日本語含む。前掲音響分析ではCosyVoice2が「声質・抑揚がトップ群に一歩劣る」。v3の日本語単独評価は未発見 → **要確認** | 指示文で言語/方言/感情/速度/音量を指定。参照音声の感情を継承する方式 | ○ 多言語・クロスリンガル ゼロショット | Apache-2.0 | Apache-2.0 | **可** | 極小・高速。18以上の中国語方言。RL版はCER 0.81%(中国語) |
| **GPT-SoVITS** | RVC-Boss | v4 = 2025-04-22 / **v2ProPlus = 2025-06-06** | 90M〜330M + 77M、**推論6GB / 学習12GB** | **B**。日本語は公式サポート。SeedTTSでSIM 0.737・WER 0.013(v2ProPlus、言語横断)。日本語特化の定量評価は未発見 → **要確認** | **専用の感情タグ体系なし**(READMEでtodo扱い)。表現は参照音声のプロソディ依存 | ◎ **5秒**ゼロショット + 1分で少数ショット微調整 | MIT | MIT (lj1995/GPT-SoVITS) | **可** | **本調査で最もクリーンなライセンス**。多数の参照音声からモブ声を量産する用途に向く。感情制御は参照音声を演技別に用意する運用でカバー |
| **Style-Bert-VITS2 (JP-Extra)** | litagin02 | v2.7.0 = 2025-08-24 | 数千万パラメータ、**〜2GB** | **A**。日本語比較で一貫して最上位(Qiita 2026-03-14で★5/5、日本語BERTによるピッチアクセント処理が優秀との評) | スタイルベクトルの連続強度スライダ + 離散プリセット(Neutral/Happy/Sad/Angry)。日本語BERTが本文から感情を推定 | △ 少数ショット微調整のみ(10分〜)。**ゼロショット不可** | **AGPL-3.0** | モデル毎に個別(HFカード要確認) | **条件付き可** | 日本語アクセント精度は依然トップ。**AGPLのネットワーク条項**: 改変版をゲームのバックエンドAPIとして自社ホストする場合はソース開示義務が生じうる → **法務確認推奨**。クライアント同梱・非改変なら実務上問題になりにくい |
| **AivisSpeech / AivisSpeech-Engine** | Aivis Project | Engine v1.2.0 = 2025-04-30 / アプリ v1.1.0-dev = 2025-12 | SBV2ベース、**CPU動作可** | **A**。SBV2と同アーキテクチャ、Qiita 2026比較で★5/5 | `intonationScale`(0.0-2.0)、`tempoDynamicsScale`(0.0-2.0) | △ 事前学習した`.aivm`モデルの読み込み方式 | LGPL-3.0 | **音声モデル毎に個別(AivisHub)** | **モデル次第 — 要確認** | エンジン自体はクレジット不要で商用可。**配布されている個別キャラ音声モデルのライセンスを1つずつ確認する必要あり(最大の落とし穴)**。2026年は有料のAivis Cloud APIに開発の主軸が移行した様子 |
| **Chatterbox Multilingual v3** | Resemble AI | **v3 = 2026-06-10**(25言語)、Turbo = 2025-12-15 | 0.5B(Nano 110M / Turbo 350M)、**2-4GB** | **C+**。日本語は25言語に含まれる。Qiita 2026比較では★3/5(多言語モデルとしての日本語対応レベル) | `exaggeration`スカラ(0.0平坦 / 0.5標準 / 0.7+劇的)。Turbo/Nanoは`[laugh][cough][chuckle][sigh]`の非言語タグ | ○ ゼロショット(〜10秒) | MIT | MIT | **可** | 全出力に**PerTh電子透かし**が埋め込まれる(法的制約ではないが検出可能)。v3の多言語版が非言語タグを継承するかは **要確認** |
| **MioTTS-2.6B** | Aratako | 2026年前半 | 2.6B、**〜6GB** | **B(要検証)**。英日バイリンガル約10万時間学習。第三者評価は未発見 → **要確認** | 記載なし(参照音声依存) | ○ ゼロショット | LFM Open License v1.0 | 同左 | **条件付き可** | LiquidAI/LFM2-2.6Bベース。RTF 0.135-0.145(RTX 5090, vLLM)。**年商1000万USD未満の企業のみ無償商用可**、超過時はLiquid AIとの別途契約が必要 |
| **Magpie-TTS Multilingual 357M** | NVIDIA | **v2607 = 2026-07-21**(v2602 = 2026-03で日本語追加) | 357M、**極小** | **B+**。**日本語CER 1.40% / SSIM 0.775(公式実測)**。学習データに「Japanese Anime Speech Dataset V5」を含む点はゲーム用途に好材料 | 明示的な感情制御なし。5音色に「感情的トーン」の変化はあると記載 | **×** ゼロショットクローンは**セキュリティ上の理由で削除済み** | NVIDIA Open Model License | 同左 | **可(公式にcommercial ready)** | 12言語対応。**固定5音色(Aria/Jason/John Van Stan/Leo/Sofia)のみ → モブ声の多様性を出せない**のが致命的。日本語品質のベースライン計測用としては優秀 |
| **ZONOS2** | Zyphra | **2026-06-12** | 900M active / 8B total (MoE)、**要確認** | **B(要検証)**。バイトトークナイズで日本語の音素化を改善と明記。日本語の第三者評価は未発見 | **v0.1にあった感情ベクトル(happy/angry/sad/fear)がv2の公式資料から消えている → 要確認**。speaking-rate 8段階と音響品質ダイヤルのみ記載 | ○ ゼロショット(ECAPA-TDNN話者埋め込み)、stable/expressive の2モード | Apache-2.0 | Apache-2.0 | **可** | v0.1(2025-02, Apache-2.0)は明確な感情制御を持っていた。v0.1の方がガヤ用途に合う可能性あり。v0.1比でスループット4倍 |
| **Supertonic 3** | Supertone (韓国) | 2026年 | **99M、CPU動作可** | **B(要検証)**。31言語、日本語デモあり(note.com)。WER/CERはVoxCPM2級と主張 | 記載なし | **要確認** | MIT(コード) | **OpenRAIL-M** | **条件付き可** | OpenRAIL-Mは利用用途制限+帰属表示義務を伴う。MIT/Apacheと同等ではない → **法務確認推奨**。CPUで大規模GPUより高速との主張は大量生成に魅力 |
| **Kokoro-82M** | hexgrad | v1.0 = 2025-01-27(**2026年に後継なし**) | 82M、**CPU可** | **C-**。日本語ハンズオン記事(kun432/Zenn)で「日本語モデルより英語音声で日本語を読ませた方が良く感じる」との酷評。Arena Elo 1057-1060はいずれも英語 | なし(固定プリセット54音色) | × 不可 | Apache-2.0 | Apache-2.0 | **可** | ライセンスと軽さは理想的だが**日本語品質が本命群に大きく劣る**。速度ベースライン用途 |

### 2-B. 参考記載(著名だが本件では候補外)

| モデル | 開発元 | 最新版・日付 | 日本語 | 重みライセンス | 生成物の商用利用 | 除外の主因 |
|---|---|---|---|---|---|---|
| **Fish Audio S2 / S2-Pro** | Fish Audio | S2 = **2026-03-09** (arXiv 2603.08823) | ○ Tier1、日本語10万時間学習 | Fish Audio Research License | **不可(要有償契約)** | TTS Arena最上位のオープンウェイト(Elo 1110-1129)。`[laugh][whisper][excited]`等15,000超のインラインタグは本調査で最良。**2026-03にコードごと研究ライセンスへ移行**、商用は business@fish.audio と別途契約が必須 |
| **OpenAudio S1 / S1-mini** | Fish Audio | 2025年 | ○ | CC-BY-NC-SA-4.0 | **不可** | 旧世代。重みが非商用 |
| **Higgs Audio V2** | Boson AI | 2025-08 | △ 要確認 | Boson Higgs Audio 2 Community License | **条件付き(年間アクティブユーザ10万まで)** | 5.8Bで12GBはギリギリ。10万AAU超で有償契約が必要 |
| **Higgs Audio V3** | Boson AI | **2026-06-04** | 記載あり(品質階層不明) | Research and Non-Commercial License | **不可** | 感情21タグ+SFX 9種(scream/laughter/crying等)でガヤ用途に理想的だが、V2より**ライセンスが後退**し非商用限定 |
| **IndexTTS-2** | Bilibili | 2025年 | **× 非対応**(日本語トークンが未知語になる) | 非商用(商用は要問合せ) | **不可** | 日本語非対応 + 非商用の二重NG |
| **Sarashina2.2-TTS** | SB Intuitions | 2025-2026 | ◎ 日本語特化 | Sarashina Model NonCommercial License | **不可** | 日本語品質は期待できるが**非商用**。さらに**SilentCipher電子透かしを除去・無効化することが契約上禁止** |
| **VibeVoice 1.5B / 7B** | Microsoft | 2025-08 | **× 英中のみ**(公式モデルカードに「他言語は非対応、意味不明または不快な出力になりうる」と明記) | MIT | 可(だが日本語不可) | 一部ブログの「12言語・日本語対応」は**誤情報**。加えてMicrosoftが公式リポジトリと7Bを削除、7Bはコミュニティ再アップロードのみ(供給リスク)。7Bは18-19GBで12GB超過 |
| **Maya1** | Maya Research | 2025年 | **× 英語のみ** | Apache-2.0 | 可(だが日本語不可) | テキスト記述による声デザイン+20超の感情タグはガヤ用途に理想的。**日本語対応待ち**。要ウォッチ |
| **Orpheus TTS** | Canopy Labs | 2025-03、2026-04にサウジアラビア語追加 | **× 日本語版なし** | Apache-2.0 | 可(だが日本語不可) | 多言語研究リリースに中/印/韓/西伊はあるが日本語なし |
| **Kyutai TTS / Pocket TTS** | Kyutai Labs | Pocket TTS = 2026-05-04(6言語) | **× 非対応** | CC-BY 4.0 (重み) | 可(だが日本語不可) | 100MでCPUリアルタイムは魅力だが英仏独西葡伊のみ |
| **Dia / Dia 2** | Nari Labs | Dia2 | **× 英語最適化**、多言語はロードマップ止まり | Apache-2.0 | 可(だが日本語不可) | 一部日本語ブログの「10言語対応」は**未確認情報** |
| **Raon-OpenTTS** | KRAFTON AI | **2026-06-16** (arXiv 2605.20830) | **× 英語のみ** | Apache-2.0 (コード・重み) | 可(だが日本語不可) | ゲーム会社発・**公開音声データのみで学習(51万時間)**という出自の綺麗さは注目に値する。日本語版が出れば最有力候補になりうる。要ウォッチ |
| **MegaTTS3** | ByteDance | 2025-03-22(以降更新なし) | **× 中英のみ** | Apache-2.0 | 可(だが日本語不可) | クローン用WaveVAEエンコーダを**公式が意図的に非公開**。コミュニティ再現版は権利関係が不明瞭 |
| **LLaSA (Llasa-1B/8B)** | HKUSTAudio | 2025-02 | **× 中英のみ**、日本語finetuneも存在せず | CC-BY-NC-4.0 | **不可** | 日本語NG + 非商用の二重NG |
| **T5Gemma-TTS-2b-2b** | Aratako | 2026-04-03 | ○ (日本語2万時間) | Gemma ToU + **CC-BY-NC 4.0** | **不可** | 同作者のIrodori-TTSと異なり非商用。依存コーデックもCC-BY-NC |
| **Kani TTS 2** | nineninesix | 2026年 | **× v2に日本語版なし**(v1はトークナイザのみ日本語を含む) | Apache-2.0 | 可(だが日本語実質不可) | 400M・3GB VRAMと軽量。日本語版が出れば再評価 |
| **VOICEVOX** | ヒホ他 | 継続開発 | ◎ 日本語特化 | 独自(キャラ毎に規約) | **キャラ毎に条件付き可** | 事前定義キャラのみでモブ声の多様性・ゼロショットクローンがない。キャラ毎のクレジット表記義務がゲーム収録では運用負荷 |

---

## 3. ベンチ候補ショートリスト(推奨8モデル)

ガヤ用途の評価軸: **(a)短い一言の自然さ** **(b)感情の振れ幅** **(c)モブらしい多様な声の量産性** **(d)非言語音(叫び・笑い・悲鳴)** **(e)商用安全性** **(f)12GB内での生成スループット**

### Tier 1 — 必ず回すべき4本

#### 1. Irodori-TTS-600M-v3-VoiceDesign (MIT)
- **選定理由**: 日本語専用設計で、第三者音響分析においてProsody 1位・Composite同率1位。RTF 0.13xと圧倒的に速く、大量のガヤ生成に最適。MITでライセンスが明快。**絵文字で笑い・咳・ため息を直接注入できる**のはガヤ用途に直結する。
- **予想される強み**: 短い一言でのアクセント精度。日本語ネイティブの抑揚。1本あたりの生成コストが最小で、数千本のモブセリフを現実的な時間で回せる。キャプション文+参照音声+テキストの3系統条件付けで声のバリエーションを作りやすい。
- **予想される弱み**: 漢字の読み(作者自身が「ひらがなに変換推奨」と明記)→ ガヤ台本を事前にかな化する前処理が必要。**学習データ非開示**のため、商用収録前にデータ出自のリスク評価が要る(要確認)。日本語専用なので多言語展開時に使えない。

#### 2. Qwen3-TTS-12Hz-1.7B (VoiceDesign + Base の組み合わせ) (Apache-2.0)
- **選定理由**: ガヤの本質的課題は「**権利的にクリーンな声を大量に用意すること**」。VoiceDesignは自然言語で架空の声を設計でき、それを参照音声としてBaseでクローン固定化すれば、実在人物に由来しないモブ声を無限に量産できる。Apache-2.0で最も安心。
- **予想される強み**: 「30代のがさつな男性、酒場の客」「甲高い子供、興奮気味」といったテキスト指示でキャラ設計が完結する。声の多様性という評価軸で最高得点が期待できる。3秒クローンで一度作った声の再現性も担保できる。
- **予想される弱み**: 生成ごとに声が微妙に揺れる(クローン固定化で回避)。長文・技術語で崩れやすく、F0の局所的な跳ねが不自然さとして知覚される報告あり。ただし**ガヤは短文なのでこの弱点は影響が小さい**と予想される。非言語音の専用タグ体系は持たない。

#### 3. Step-Audio-EditX (Apache-2.0)
- **選定理由**: 本調査で唯一、`roar` `shout` `murmur` `laugh` `sigh` `giggle` `cough` `breath` といった**ガヤに直結する語彙をタグとして明示的に持つ**。感情強度を反復編集で段階制御できるため、「同じセリフの怒り度違い」を機械的に量産できる。
- **予想される強み**: 群衆の怒号・悲鳴・ざわめきといった非言語寄りの表現。他モデルで生成した日本語音声に対する「感情エディタ」としての二段構え運用。
- **予想される弱み**: **日本語イントネーションが中国語訛りになる実測報告**が最大の懸念。日本語参照音声を与えた場合の改善度を最優先で検証すべき。VRAM 11.5GB実測は12GB環境でギリギリ(4bit量子化前提での評価を推奨)。30秒以下推奨だがガヤは短文なので問題なし。

#### 4. Style-Bert-VITS2 (JP-Extra) または AivisSpeech (AGPL-3.0 / LGPL-3.0)
- **選定理由**: 日本語アクセント精度の**基準線(リファレンス)**として必須。他モデルの日本語がどれだけ劣化しているかを測る物差しになる。
- **予想される強み**: ピッチアクセントの正確さ、句読点処理。少数の主要モブキャラを作り込む用途では今なお最良。CPUでも動くため大量バッチが安価。
- **予想される弱み**: **ゼロショットクローン不可**のため、モブ声の多様性を出すには話者ごとに学習が必要でスケールしない。ガヤ用途の本命にはなりにくい。AivisSpeech経由で既存キャラ音声を使う場合、**各音声モデルの個別ライセンス確認が必須**。SBV2本体のAGPLはバックエンドAPI化する設計だと法務リスクが生じる。

### Tier 2 — 比較のために回す4本

#### 5. VoxCPM2 (Apache-2.0)
- **選定理由**: Apache-2.0 + 48kHz + テキスト記述による声デザイン、という条件を全部満たす2026年の新顔。ASR一致率で6モデル中1位=**セリフの内容が正しく伝わる**ことが担保されている(中距離で聞こえるガヤでは可聴性が重要)。
- **予想される強み**: 台詞の明瞭度。48kHz出力はゲーム内でのフィルタ処理(距離減衰・リバーブ)に対する余裕が大きい。
- **予想される弱み**: 抑揚に違和感が残るとの聴感評価。日本語のG2P(漢字読み・固有名詞)が未検証で、地名・固有名詞の多いRPG台本では事故る可能性 → 検証項目に含めるべき。

#### 6. GPT-SoVITS (v2ProPlus / v4) (MIT)
- **選定理由**: 本調査で**最もライセンスがクリーン**(コード・重みともMIT)。5秒のゼロショットクローンで、手持ちの参照音声から一気にモブ声を展開できる。
- **予想される強み**: 権利処理済みの参照音声素材があるプロジェクトでは最短距離。日本語対応の実績が長く、日本コミュニティの知見が厚い。
- **予想される弱み**: **感情タグ体系がない**(公式にtodo扱い)ため、感情の振れ幅は参照音声を演技別に用意して稼ぐ必要がある = 素材依存。ガヤの「感情の振れ幅」軸では不利。

#### 7. MOSS-TTS v1.5 (Local-Transformer 4B / Realtime 1.7B) (Apache-2.0)
- **選定理由**: `[pause X.Ys]`による明示的な間制御と多話者対話生成(MOSS-TTSD)を持つ。ガヤは「複数人が重なって喋る」音像が本質なので、多話者対話生成は他にない強み。効果音生成モデル(MOSS-SoundEffect)も同ファミリーでApache-2.0。
- **予想される強み**: 群衆の掛け合い・重なりの生成。RTF 0.51とTTFB 180msで実用速度。
- **予想される弱み**: 日本語の第三者評価が皆無で品質が完全に未知数。声デザイン用のMOSS-VoiceGeneratorは**中英のみ**で日本語に直接使えない(生成した声を参照音声として日本語対応モデルに渡す迂回は可能)。ドキュメント間で言語数の記載が不一致。

#### 8. Chatterbox Multilingual v3 (MIT) **または** MioTTS-2.6B (LFM Open License)
- **Chatterbox選定理由**: MIT + `exaggeration`スカラという単純明快な感情ダイヤル。「同じセリフを誇張度違いで10本」という機械的な振れ幅生成がしやすい。**弱み**: 日本語品質が★3/5評価と本命群に劣る。全出力にPerTh電子透かしが入る(法的には問題ないが、収録音声が検出可能である点は把握しておくべき)。
- **MioTTS選定理由**: 英日特化2.6BでRTF 0.14。Irodori-TTSと同じ作者による別系統(LLMベース)で、Irodoriとの比較に意味がある。**弱み**: LFMライセンスの**年商1000万USD上限**。インディー規模なら実質無償商用可だが、パブリッシャーが付く場合は要確認。感情制御機構が非公開。

### ベンチマーク設計上の推奨事項
- **前処理**: Irodori-TTS・VoxCPM2向けに、ガヤ台本の漢字を事前にかな化するパイプラインを用意する(日本語モデル共通の弱点)。
- **評価セット**: 「短い一言(3-8モーラ)」「感嘆・悲鳴」「ざわめき用の意味の薄い発話」の3カテゴリを用意し、中距離を想定したローパス+リバーブ処理**後**の可聴性・自然さで評価する。ドライ音源の品質順位と処理後の順位は一致しない可能性が高い。
- **声の多様性の測定**: 話者埋め込み(ECAPA-TDNN等)のコサイン距離分布で「生成された声がどれだけ散らばっているか」を定量化すると、ガヤ用途の適性が客観的に測れる。
- **二段構え運用の検証**: Qwen3-TTS VoiceDesign または MOSS-VoiceGenerator で声を作り、Irodori-TTS / GPT-SoVITS でクローンして日本語を喋らせる、というクロスモデル運用を1条件として入れる価値が高い。

---

## 4. 落選理由リスト

| モデル | 落選理由 | 分類 |
|---|---|---|
| **Fish Audio S2 / S2-Pro** | 2026年3月にコードごと「Fish Audio Research License」へ移行。研究・非商用は無償だが**商用は Fish Audio との別途有償契約が必須**。生成物をゲームに収録できない。加えて出力を他の生成モデルの学習に使うことも禁止 | ライセンスNG |
| **OpenAudio S1 / S1-mini** | 重みが CC-BY-NC-SA-4.0(非商用) | ライセンスNG |
| **Higgs Audio V3** | Boson Higgs TTS 3 Research and Non-Commercial License。**V2より後退**して明確な非商用に | ライセンスNG |
| **Higgs Audio V2** | 年間アクティブユーザ10万人を超えると有償契約が必要な Community License。加えて5.8Bで12GBはfp16だとほぼ余裕なし | ライセンス条件付き + VRAM懸念 |
| **IndexTTS-2** | **日本語トークンが未知語になり意味のある音声が出ない**。加えて商用は別途ライセンス要 | 日本語NG + ライセンスNG |
| **Sarashina2.2-TTS** | Sarashina Model NonCommercial License(非商用)。さらに**SilentCipher電子透かしの除去・無効化が契約上禁止** | ライセンスNG |
| **T5Gemma-TTS-2b-2b** | Gemma ToU + CC-BY-NC 4.0。依存する音声コーデックも CC-BY-NC | ライセンスNG |
| **LLaSA (Llasa-1B/8B)** | CC-BY-NC-4.0(非商用)、かつ中英のみで日本語finetuneも存在しない | ライセンスNG + 日本語NG |
| **VibeVoice 1.5B / 7B** | 公式モデルカードが「英語・中国語のみで学習、他言語は非対応」と明記。**一部ブログの日本語対応記述は誤り**。加えてMicrosoftが公式リポジトリと7Bを削除済みで供給の安定性に難。7Bは18-19GBで12GB超過 | 日本語NG + 供給リスク + VRAM超過 |
| **Maya1** | Apache-2.0で制御機能もガヤ向きだが**英語のみ** | 日本語NG(要ウォッチ) |
| **Orpheus TTS** | 多言語研究リリースに日本語版が存在しない | 日本語NG |
| **Kyutai TTS / Pocket TTS** | 対応6言語(英仏独西葡伊)に日本語なし | 日本語NG |
| **Dia / Dia 2** | 英語最適化、多言語は公式ロードマップ段階 | 日本語NG |
| **Raon-OpenTTS** | KRAFTON発・Apache-2.0・公開データのみ学習と条件は良いが**英語のみ** | 日本語NG(要ウォッチ) |
| **MegaTTS3** | 中英のみで日本語非対応。さらにクローン用WaveVAEエンコーダを公式が非公開にしており、コミュニティ再現版は権利関係が不明瞭 | 日本語NG + 権利不明 |
| **Kani TTS 2** | v2の公開モデルに日本語版がない(v1はトークナイザに日本語を含むのみで品質未検証) | 日本語NG |
| **Kokoro-82M** | ライセンス(Apache-2.0)と軽さは理想的だが、日本語ハンズオン評価が「英語音声で日本語を読ませた方がマシ」と極めて低い。**クローン不可・感情制御なし**でモブ声の多様性も出せない | 日本語品質NG + 機能不足 |
| **Magpie-TTS Multilingual** | 日本語CER 1.40%と品質・ライセンス(NVIDIA Open Model License、商用可)は良好だが、**ゼロショットクローンが削除され固定5音色のみ**。モブ声の多様性という中核要件を満たせない | 機能不足(ベースラインとしては有用) |
| **ZONOS2** | Apache-2.0・日本語音素化改善と条件は良いが、**v0.1にあった感情ベクトル制御が公式資料から消えている**。ガヤの感情振れ幅要件に対する適合が不明 | 制御機能が要確認(次点) |
| **Supertonic 3** | 99MでCPU動作、日本語対応と魅力的だが、重みが **OpenRAIL-M**(利用制限+帰属義務)でMIT/Apacheと同等ではない。クローン可否も未確認 | ライセンス要確認(次点) |
| **VOICEVOX** | 日本語品質は高いが事前定義キャラのみ。ゼロショットクローン不可でモブ声の多様性が出せず、キャラ毎のクレジット表記義務が数千本収録の運用と噛み合わない | 機能不足 + 運用負荷 |
| **Audio Flamingo 3 / AF-Next, Parakeet** | **TTSではない**(前者は音声理解・推論、後者はASR)。調査シード中の誤分類を訂正 | 対象外 |

---

## 5. 出典URL一覧

### 一次情報(リポジトリ・モデルカード・論文)
- Qwen3-TTS VoiceDesign: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign
- Qwen3-TTS CustomVoice: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice
- Qwen3-TTS Base 1.7B: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base
- Qwen3-TTS Base 0.6B: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base
- Qwen3-TTS GitHub: https://github.com/QwenLM/Qwen3-TTS
- Irodori-TTS GitHub: https://github.com/Aratako/Irodori-TTS
- Irodori-TTS-600M-v3-VoiceDesign: https://huggingface.co/Aratako/Irodori-TTS-600M-v3-VoiceDesign
- Irodori-TTS-500M-v3: https://huggingface.co/Aratako/Irodori-TTS-500M-v3
- MioTTS-2.6B: https://huggingface.co/Aratako/MioTTS-2.6B
- T5Gemma-TTS-2b-2b: https://huggingface.co/Aratako/T5Gemma-TTS-2b-2b
- Step-Audio-EditX GitHub: https://github.com/stepfun-ai/Step-Audio-EditX
- Step-Audio-EditX README: https://github.com/stepfun-ai/Step-Audio-EditX/blob/main/README.md
- Step-Audio-EditX HF: https://huggingface.co/stepfun-ai/Step-Audio-EditX
- Step-Audio-EditX 技術報告: https://arxiv.org/abs/2511.03601
- VoxCPM2: https://huggingface.co/openbmb/VoxCPM2
- VoxCPM GitHub: https://github.com/OpenBMB/VoxCPM
- MOSS-TTS GitHub: https://github.com/OpenMOSS/MOSS-TTS
- MOSS-TTS HF: https://huggingface.co/OpenMOSS-Team/MOSS-TTS
- MOSS-VoiceGenerator: https://huggingface.co/OpenMOSS-Team/MOSS-VoiceGenerator
- MOSS-SoundEffect: https://huggingface.co/OpenMOSS-Team/MOSS-SoundEffect
- MOSS-TTSD GitHub: https://github.com/OpenMOSS/MOSS-TTSD
- CosyVoice3: https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512
- CosyVoice2: https://huggingface.co/FunAudioLLM/CosyVoice2-0.5B
- Style-Bert-VITS2: https://github.com/litagin02/Style-Bert-VITS2
- Style-Bert-VITS2 LICENSE: https://github.com/litagin02/Style-Bert-VITS2/blob/master/LICENSE
- GPT-SoVITS: https://github.com/RVC-Boss/GPT-SoVITS
- GPT-SoVITS LICENSE: https://github.com/RVC-Boss/GPT-SoVITS/blob/main/LICENSE
- GPT-SoVITS 重み: https://huggingface.co/lj1995/GPT-SoVITS
- AivisSpeech: https://github.com/Aivis-Project/AivisSpeech
- AivisSpeech-Engine: https://github.com/Aivis-Project/AivisSpeech-Engine
- AivisHub 利用規約: https://hub.aivis-project.com/terms-of-service
- Kokoro-82M: https://huggingface.co/hexgrad/Kokoro-82M
- Fish Audio S2-Pro: https://huggingface.co/fishaudio/s2-pro
- Fish Speech LICENSE: https://github.com/fishaudio/fish-speech/blob/main/LICENSE
- OpenAudio S1-mini: https://huggingface.co/fishaudio/openaudio-s1-mini
- Fish Audio S2 技術報告: https://arxiv.org/html/2603.08823v2
- Chatterbox GitHub: https://github.com/resemble-ai/chatterbox
- Chatterbox Multilingual v3: https://www.resemble.ai/resources/chatterbox-multilingual-v3-tts-with-embedded-watermarking-for-25-languages
- ZONOS2: https://www.zyphra.com/our-work/zonos2
- Zonos GitHub: https://github.com/Zyphra/Zonos
- Higgs Audio V2 LICENSE: https://huggingface.co/bosonai/higgs-audio-v2-generation-3B-base/blob/main/LICENSE
- Higgs Audio V3: https://huggingface.co/bosonai/higgs-audio-v3-tts-4b
- Magpie-TTS Multilingual 357M: https://huggingface.co/nvidia/magpie_tts_multilingual_357m
- Magpie-TTS モデルカード: https://build.nvidia.com/nvidia/magpie-tts-multilingual/modelcard
- Supertonic GitHub: https://github.com/supertone-inc/supertonic
- Supertonic 3 HF: https://huggingface.co/Supertone/supertonic-3
- Kyutai Pocket TTS 多言語化: https://kyutai.org/blog/2026-05-04-pocket-tts-multilingual/
- Raon-OpenTTS GitHub: https://github.com/krafton-ai/RAON-OpenTTS
- Raon-OpenTTS 論文: https://arxiv.org/pdf/2605.20830
- MegaTTS3: https://github.com/bytedance/MegaTTS3
- Llasa-1B: https://huggingface.co/HKUSTAudio/Llasa-1B
- VibeVoice-1.5B: https://huggingface.co/microsoft/VibeVoice-1.5B
- Kani TTS 2: https://github.com/nineninesix-ai/kani-tts-2
- Orpheus TTS: https://github.com/canopyai/Orpheus-TTS
- sarashina2.2-tts: https://github.com/sbintuitions/sarashina2.2-tts/blob/main/README_ja.md
- LFM Open License v1.0: https://www.liquid.ai/lfm-license

### 第三者評価・ベンチマーク・比較記事
- 日本語ボイスクローン6モデル音響分析 (2026-06-18): https://zenn.dev/fujinumagic/articles/local-japanese-tts-voice-clone
- 日本語TTSモデル徹底比較2026 (Qiita, 2026-05-12): https://qiita.com/0h-n0/items/8f78f7acd31000612d13
- ローカルTTS 5モデル日本語品質比較 (Qiita, 2026-03-14): https://qiita.com/GeneLab_999/items/bc07147b589a93bf6114
- AITuber開発者向けローカルTTS 10選 (note, 2026-05-04): https://note.com/aituberonair/n/n0133274f79ff
- 日本語オープンソースTTS比較 (Neosophie, 2026-04-27): https://neosophie.com/ja/blog/20260317-tts
- Qwen3-TTS VoiceDesign 実機検証 (2026-01-24): https://blog.tumf.dev/posts/diary/2026/1/24/qwen3-tts-voice-design-clone-workflow/
- Step-Audio-EditX 実機検証 (Zenn, kun432): https://zenn.dev/kun432/scraps/d8b13226d609d1
- Kokoro 日本語音声検証 (Zenn, kun432): https://zenn.dev/kun432/scraps/d0618dfa29200d
- VoxCPM2 と日本語 (lilting.ch, 2026-05-13): https://lilting.ch/en/articles/voxcpm2-tokenizer-free-local-tts
- Qwen3-TTS オープンソース化 (Simon Willison, 2026-01-22): https://simonwillison.net/2026/Jan/22/qwen3-tts/
- TTS Arena Leaderboard: https://tts-agi-tts-arena-v2.hf.space/leaderboard
- Artificial Analysis TTS Leaderboard: https://artificialanalysis.ai/text-to-speech/leaderboard/provider-voice
- オープンソースTTS技術の進化 (NOB DATA, 2026): https://nobdata.co.jp/report/creative_ai/11/

---

## 付記: 本レポートの確度について

- **ライセンス**は原則としてGitHubのLICENSEファイルまたはHuggingFaceモデルカードのlicenseフィールドという一次情報で確認した。特にFish Audio(2026年3月に商用不可へ移行)、Higgs Audio V3(V2より制限強化)、VibeVoice(公式が英中のみと明記)については、複数のSEO系ブログが**事実と異なる記述**をしており、一次情報との齟齬を確認済み。
- **日本語品質の評価**は、学術的なMOS/Elo研究が日本語単独では存在しないため、(1)公式が公表するWER/CER、(2)第三者による音響分析(2026-06-18のZenn記事)、(3)日本語コミュニティのハンズオン検証、の3層で根拠を示した。ブログの主観評価に依拠している箇所はその旨を明記している。
- **「要確認」と記した項目**は、一次情報で裏が取れなかったか、資料間で記述が矛盾していたもの。実機ベンチマークで検証すべき対象として扱うこと。
- **VRAM実測値**は多くのモデルで公式記載がなく、第三者の実測報告に依拠している。RTX 4070 Ti (12GB) での実際の挙動はベンチマーク時に必ず計測すること。特にStep-Audio-EditX(11.5GB実測)は余裕がない。
