# 「朗読」と「演技」を分ける理論 — 音響特徴と機械制御への翻訳

作成日: 2026-07-28 / 対象プロジェクト: gaya-bench (RPGモブNPCガヤボイスのTTS量産)
担当範囲: 理論 → 音響特徴 → 制御指針の翻訳（工学的実装手法は別担当）

---

## 1. 総括

- Owner評「朗読っぽい／無感情な棒読み」は、**F0レンジが狭い**という単一原因ではない。理論的には (a) 発話内の情報配置、(b) 声質(phonation)の固定、(c) クリップ間の分散不足、の3層の欠落として分解できる。
- 最も強い理論的支柱は前川喜久雄のパラ言語情報(PI)研究。「そうですか」程度の**短い1発話でも6種のPIが80%以上の正解率で聞き分けられる**ことが実証されており、**短文だから演技が乗らない、は成立しない**。乗せる手がかりが1〜2秒に収まることが実証済み。
- 前川はPIの生成に**二経路モデル**を提案している。①言語情報を参照する経路（句末境界音調の選択、モーラ持続時間制御、アクセント核F0下降タイミング）と②参照しない経路（発話全体の持続時間・F0レンジ・振幅・発声様式＝声質）。**この二分がそのままTTS制御の二分（記号/テキスト側の制御 と グローバルスタイル側の制御）に対応する**。現状のTTSは①がほぼ動いていない可能性が高い。
- 朗読/自発の判別で最も効くのは、**F0ではなく「発話と無音のセグメント長分布」＋話速**（Spotify/QMULの多言語研究で、eGeMAPSに話速・無音長統計を足すと scripted F1 が 0.69→0.76）。ただし同研究で**日本語は全言語中もっとも判別が難しい**（手設計特徴でAUC 0.65〜0.70）。日本語ガヤでは手設計特徴だけの評価は危険で、SSL埋め込み系の指標を併用すべき。
- 評価指標として **log F0 RMSE は使ってはいけない**。人間の韻律判断との相関は r̄=0.30 に留まる。DS-WED（離散音声トークン上の重み付き編集距離）は r̄=0.77。
- 生成側の既知バイアスとして、**NAR flow-matching系TTSは韻律が単調になりやすく**（暗黙アラインメントによるduration変動の欠如）、**DPO等のRL後処理は明瞭性と引き換えに韻律多様性を3〜19%削る**。現行パイプラインがどちらかに該当していないか要確認。
- ガヤ特有の現場知見：アニメ音響監督 明田川進は、ガヤで重要なのは「声を張り上げること**ではなく**、求められている雰囲気を読み取ること」と明言。ガヤは**4〜5人でマイク位置を変えて3回録り重ねる**のが標準手法。TTS側は「1クリップの演技力」より「レイヤ化を前提とした分散設計」に寄せるべき。

---

## 2. 朗読 vs 演技の音響的差異（根拠付き）

### 2.1 総論的な差分

| 次元 | 朗読 (read / scripted) | 演技 (acted / expressive) | 根拠 |
|---|---|---|---|
| ポーズ構造 | 句読点位置に、ほぼ一定長で出現 | 統語境界と一致しない位置にも入る／長さが大きく変動 | 発話・無音セグメントの長さ分布が朗読/自発判別で最も顕著な特徴 [S1][S2] |
| 話速 | 発話内でほぼ一定 | 局所的に伸縮（緩急） | 調音速度・話速が自発性の指標 [S1][S2] |
| 発話全体の長さ | テキスト長にほぼ比例 | PI型で最大2倍以上変動 | 前川: A(感心)/D(落胆)/S(疑い)の平均モーラ長がN(中立)の2倍超 [S3] |
| F0レンジ | 中庸・全文で一様 | 局所的に拡大／圧縮 | フォーカスによるF0レンジ拡大＋後続の圧縮(PFC) [S4] |
| F0平均 | 標準 | 高覚醒感情で上昇、低覚醒で低下 | Scherer系の感情音響プロファイル [S5][S6] |
| 声質 | modal固定 | breathy / pressed / creaky を切り替え | 前川: 高速内視鏡で発声様式がPI型ごとに体系的に変化 [S3] |
| 強度ダイナミクス | 平坦 | 高覚醒で急峻なピーク | 怒りは「突発的な振幅ピーク」、喜びは「広いダイナミックレンジ」 [S6] |
| 語尾（句末境界音調） | ほぼ全て単純下降 L% | 上昇・下降上昇・遅延上昇などを使い分け | X-JToBIのBPM目録、前川の「疑い」の遅延上昇 [S3][S7] |
| 非流暢性・生理音 | 除去されている | 吸気音・息継ぎが残る | 呼吸音付き合成は無音ポーズ版より自然性で強く選好される [S8] |
| 分節音 | 標準的な調音 | 母音空間が変形（F1/F2シフト） | 前川: EMAで舌背位置がPI型ごとに実際に移動、F2が体系的にシフト [S3] |

### 2.2 前川(2004)のパラ言語情報6類型 — 短文における定量データ

**これが本プロジェクトで最も直接使えるデータ**。標準語話者3名が「そうですか」「あなたですか」等の短文を、6種のパラ言語的意味を意識して発話した資料の分析。

6類型: **N**=中立, **F**=焦点(強調), **D**=落胆, **A**=感心, **I**=無関心, **S**=疑い

| 特徴 | N(中立) | F(焦点) | D(落胆) | A(感心) | I(無関心) | S(疑い) |
|---|---|---|---|---|---|---|
| 発話持続時間 | 基準(1.0) | 短い | **長い(モーラ平均で2倍超)** | **長い(同上)** | 短い | **長い(同上)** |
| 句頭F0上昇幅 | 弱〜消失(〜20-50Hz) | 弱 | 弱 | **明瞭(〜170Hz)** | 弱 | **明瞭(〜200Hz)** |
| 句頭上昇の前 | — | — | — | **低ピッチ区間が延長** | — | **低ピッチ区間が延長** |
| 句末上昇 | 単純上昇 | 単純上昇 | — | — | — | **低ピッチ持続後に大幅上昇**（川上の「反問の上昇」） |
| アクセント核F0下降のタイミング | 基準(≒0) | ≒0 | やや遅延 | **遅延(≒+0.12s)** | ≒0 | **遅延(≒+0.15s)** |
| スペクトル傾斜 H1-A3 | 中(≒26dB) | — | **最大(≒34dB)＝息漏れ声** | 大(≒33dB) | — | **最小(≒19dB)＝pressed** |
| 声門（高速撮影） | 完全閉鎖 | — | **不完全閉鎖(息漏れ)** | — | — | **閉鎖区間比率がNより大＝pressed** |
| 母音フォルマント | 基準 | — | — | F2低め | — | **F2が体系的に上昇**（舌背前進をEMAで確認） |
| 聴取同定正解率 | 0.86 | **0.59** | **0.99** | 0.89 | 0.81 | **0.98** |

*注: F0上昇幅・H1-A3・下降タイミングの数値は論文の箱ひげ図(図4,5,7)からの読み取りで、おおよその値。*

**知覚空間の構造（MDS 3次元解、STRESS=0.04）と音響特徴との重回帰（R²=0.89/0.60/0.64）:**

| 次元 | 解釈 | 分離するもの | 最も効く音響変数 |
|---|---|---|---|
| DIM1 | **顕著さ (salience)** | {A,D,S} vs {N,F,I} | **DUR（発話全体の持続時間, β≒0.60〜0.62）**、Peak Timing |
| DIM2 | **問合せ (query)** | S vs {A,I} | **PR_F（発話末尾2モーラのF0レンジ, β≒−1.05）** |
| DIM3 | **大きさ (loudness)** | {A,F,S} vs {N,I,D} | **RMS（β≒0.50〜0.63）**、F0 |

**→ 制御設計への含意（重要）**
1. 「演技しているように聞こえる」の第一因子は **F0ではなく持続時間**。短文で演技を出す最短経路は、テキスト長に対して**発話長を大きく振ること**。
2. 第二因子は **末尾2モーラのF0レンジ**。語尾処理が独立した制御軸として効いている。
3. 第三因子が音量(RMS)。
4. 非母語話者（日本語未学習の英語母語話者）の知覚空間では **DIM2「問合せ」軸が欠落**していた。句末境界音調によるPI表現は言語依存性が高く、**日本語ガヤは日本語の句末音調体系に沿って制御しないと効かない**。

### 2.3 演技しても壊してはいけないもの

前川の図2が示すのは、A/D/Sで発話長が2倍に伸びても、**短母音/長母音の音韻的長短対立は明瞭に保存される**という点。演技のためのduration操作は**一様スケーリングであってはならず**、音韻対立を保存する非一様な伸縮でなければならない。TTSでdurationを機械的に×2すると「間延びした朗読」になるだけで演技にはならない。

### 2.4 「演技」の過剰性という既知の落とし穴

演技音声コーパスは自然発生の感情より**誇張され強度が高い**傾向があり、感情認識率も高く出る。学術的には妥当性の問題として批判される点だが、**ゲームのモブNPCというドメインではこの「誇張」がむしろ正解**である（舞台的発話は日常発話よりF0レンジが大きい [S9]）。ベンチの目標を「自然な会話音声」ではなく「演出された演技音声」に置くことを明示すべき。

### 2.5 高覚醒/低覚醒の音響プロファイル（ガヤの感情タグ設計用）

| | 高覚醒（怒り・喜び・驚き・警戒） | 低覚醒（悲しみ・退屈・落胆） |
|---|---|---|
| F0平均 | 上昇 | 低下 |
| F0変動 | 大 | **小（＝知覚的に単調）** |
| 話速 | 速い | 遅い |
| ポーズ | 短い | 長い |
| 強度 | 大 | 小 |
| 高域エネルギー | 増加 | 減少 |
| 弁別（怒り vs 喜び） | 怒り=突発的振幅ピーク／pressed・constricted／F3-F4の集中。喜び=広いダイナミックレンジ／F0変動大／明るい共鳴 | — |

出典 [S5][S6]。**低覚醒感情の音響プロファイルは「棒読み」の音響プロファイルとほぼ同じ**（低F0変動＝単調）という点は重要。ガヤに落ち着いた/悲しい系のタグを付ける場合、「棒読み」と区別する手がかりは声質(breathy化)と語尾処理に依存する。

---

## 3. 演技理論 → 制御パラメータ翻訳表

### 3.1 前川の二経路モデルに沿った制御レイヤ分割

```
【経路①: 言語情報を参照する制御】= 記号・テキスト側で指定すべきもの
  - 句末境界音調(BPM)の選択          → 文末記号 / BPMタグ
  - モーラ持続時間の制御(音韻対立を保存) → phoneme duration の局所スケール
  - アクセント核によるF0下降タイミング   → アクセント句のピーク位置シフト
  - フォーカス位置                    → prominence タグ

【経路②: 言語情報を参照しない制御】= グローバルスタイル側で指定すべきもの
  - 発話全体の持続時間                → global speed / duration scale
  - 発話全体のF0レンジ                → global pitch range scale
  - 発話全体の振幅                    → global energy
  - 発声様式(phonation type)          → style vector / reference audio / voice quality tag
```

現状「朗読っぽい」なら、**経路①がまったく動かず経路②の弱い変調だけで感情を作ろうとしている**可能性が高い。経路②だけでは「明るい朗読／暗い朗読」にしかならない。

### 3.2 演出指示 → 音響実体 → 機械制御 の対応表

| 演出用語（現場語彙） | 演技上の意味 | 音響的実体 | 機械制御パラメータ（具体値の出発点） |
|---|---|---|---|
| **「間を取って」「一拍置いて」** | 次語への注意集中／情報のため | 該当語直前の無音 + 直前モーラのF0を低いまま保持 | 語境界に無音 150–400ms 挿入。直前モーラのF0を発話F0下位20%点に固定。落語の「間」理論では沈黙は次の語のエネルギー蓄積 [S10] |
| **「そこ立てて」「強く」「拾って」** | フォーカス付与 | 当該アクセント句のF0レンジ拡大＋強度上昇、**後続句のF0圧縮(PFC)** | 焦点句: F0レンジ ×1.3–1.8, energy +3–6dB, duration ×1.1–1.3。後続句: F0レンジ ×0.5–0.8。※日本語のPFCは**焦点語が有核語(A-word)のとき**に productive。無核語では最低F0の下降で代替 [S4] |
| **「語尾上げて」「疑問っぽく」** | 問合せ／呼びかけ | 句末境界音調 L%H% | 末尾2モーラのF0レンジを拡大（前川MDS DIM2の主変数 PR_F） |
| **「語尾を収めて」「言い切って」** | 断定・独白・自己完結 | L%のみ、末尾モーラの短縮、強度フェードアウト | 末尾モーラ duration ×0.8, energy −6dB/末尾150ms, F0傾き −3〜−6 semitone/s。日本の声優訓練では「語尾でセリフのすべてが決まる」とされる技術項目 [S11] |
| **「疑い」「うさんくさく」** | S型 | 低ピッチ区間が持続した後の大幅な句末上昇（川上の「反問の上昇」）、pressed phonation | BPM = LH%（遅延上昇型）、H1-A3を低く（≒19dB相当）、F2をやや上昇 [S3] |
| **「感心」「へえ〜」** | A型 | 句頭に明瞭なF0上昇（先行して低ピッチ区間が延長）、発話長2倍、弱い息漏れ | 発話頭に低ピッチ助走 100–200ms → F0上昇 150–200Hz。duration ×2。H1-A3 +7dB [S3] |
| **「落胆」「がっかり」** | D型 | breathy（声門不完全閉鎖）、発話長 長い、句頭上昇なし | H1-A3 最大（+8dB程度）、aspiration noise 付加、duration ×2、句頭F0上昇を抑制 [S3] |
| **「そっけなく」「興味なさそうに」** | I型 | 発話長 短い、句頭上昇なし、F0レンジ狭い、音量小 | duration ×0.85, F0レンジ ×0.7, energy −4dB。**ただしこれは棒読みと音響的に近いので、声質を modal から少しずらす（軽い creaky）ことで差別化が必要** |
| **「テンション上げて」「熱く」** | 高覚醒化 | F0平均↑、話速↑、RMS↑、ポーズ短縮、spectral tilt が浅く | F0 mean +2〜4 semitone, rate ×1.15–1.3, energy +4dB, pause ×0.6, alpha ratio 上昇 [S5][S6] |
| **「距離を出して」「奥で」** | 遠景ガヤ | **声を張る（pressed, F0↑, 高域↑）が収録レベルは下げる** | 「小さい声」ではなく「遠くに届かせる声を小さく録る」。TTS側は張った声を生成し、ミックス側で減衰＋残響。現場でも4〜5人をマイク位置を変えて3回重ねる [S12] |
| **「息を入れて」「呼吸を感じさせて」** | 生理性の付与 | 吸気音、breathy onset | 発話頭に吸気ノイズ 80–200ms。呼吸音付き合成は無音ポーズ版より自然性で選好される [S8] |
| **「立ち上がりを鋭く」「頭からいって」** | アタック | 語頭の energy 立ち上がり時間の短縮、onset F0 を高めから、先行無音 | onset 0–50ms の loudness slope を最大化。第一声のF0を発話中央値 +2〜4 semitone から開始 |
| **「もう少し明るく」「少し抑えて」** | 現場で推奨される簡潔指示形式 | グローバルスタイルの微調整 | 良いディレクションは「簡潔な指示＋理由を一言」。「なんとなく違う」は禁じ手とされる [S13]。→ **ベンチのタグ体系も「方向＋度合い」の形式にすべき** |
| **感情温度スケール 1–5** | ゲームVO現場の標準マークアップ | arousal の段階指定 | 1=calm/minimal, 5=extreme/peak。**このスケールをそのままTTSのstyle intensity にマップするのが実務的に最も整合する** [S14] |

### 3.3 スタニスラフスキー系（目的／障害／戦術）の翻訳

俳優訓練の中核は「**目的(objective)＝何が欲しいか**」「**障害(obstacle)＝何が邪魔しているか**」「**戦術(tactic)＝どうやって取りに行くか**」。これは感情ラベル（怒り／喜び）より上位の概念で、**台詞を「読む」のでなく「使う」ようにさせる**装置である [S15]。VO実務でも「原稿を読んでいるように聞こえる」のを防ぐ手段として明示的に使われている。

ガヤ生成への翻訳:

| 演技理論の概念 | ガヤでの具体化 | メタデータ/プロンプト設計 |
|---|---|---|
| 目的 (objective) | 「気づかせたい」「追い払いたい」「助けを求めたい」等の**動詞** | クリップに `intent` フィールドを持たせる。感情形容詞ではなく動詞で書く |
| 障害 (obstacle) | 距離が遠い／騒がしい／相手が動いている／言いにくい | `obstacle` が声の張り・pressed度・話速を決める。距離が遠い→張る、言いにくい→ためらい(間+breathy) |
| 戦術 (tactic) | 脅す／おだてる／茶化す／急かす | 同じテキスト・同じ感情でも戦術違いでバリエーションを作る＝**ガヤのバリエーション設計の直接の道具** |
| 「前史」(与えられた状況) | 発話の直前に何が起きていたか | **短文で最も効く**。発話冒頭の状態（既に高いF0／既に息が上がっている／既にpressed）を決める |

**ガヤバリエーション設計の実装案**: 同一テキストに対し `intent × tactic` の直積でN通り生成し、音響的に最も離れたものを採択する（後述の DS-WED を採択基準に使える）。ゲームのbark設計論でも「barkは刺激-応答システムであり、プレイヤーの無言の問いに答える形で書く」とされ、**発話の動機を明示することが良いbarkの条件**とされている [S16]。

### 3.4 日本の声優訓練の技術体系との対応

日本の声優養成で標準的に教えられる表現軸は「**緩急・高低・強弱・間**」＋「声の響かせ方（響かせる場所の切替）」[S17][S11]。これは音響パラメータへほぼ一対一で対応する:

| 訓練用語 | 音響パラメータ | 測定量 |
|---|---|---|
| 緩急 | 局所調音速度の変動 | 窓ごとの mora/s の標準偏差 |
| 高低 | F0レンジ・F0平均 | semitone 単位の 5–95%tile span, median |
| 強弱 | 強度ダイナミクス | RMS/loudness の 5–95%tile span |
| 間 | ポーズ構造 | 発話内無音の個数・長さ分布、発話前無音 |
| 響かせる場所（胸/鼻/頭） | 声質・スペクトル配分 | 「怒り=胸腹からの低く重い声」「悲しみ=息を混ぜた声」「喜び=鼻/頭に響く高く明るい声」→ spectral tilt, H1-H2, formant配置 |
| 語尾を収める | 句末処理 | 末尾F0傾き・末尾duration比・末尾H1-H2 |

これは**音響直結の語彙体系がすでに現場に存在している**ということであり、ベンチのディレクションタグはこの4軸＋声質＋語尾の6軸で設計するのが日本語ドメインでは最も筋が良い。

---

## 4. 短文ガヤ（1〜3秒）で演技が成立する条件

### 4.1 前提: 短文でも演技は成立する（実証済み）

前川の実験は「そうですか」「あなたですか」という**1〜2秒の短文**で、6種のパラ言語情報が母語話者に**80%以上の正解率**で同定されることを示している（D=0.99, S=0.98, A=0.89, N=0.86, I=0.81。唯一F=0.59が低い）。したがって「1〜3秒だから演技が乗らない」は誤り。**乗せられる手がかりが1〜2秒に収まることが実証されている。**

ただし **F（焦点/強調）だけが低い(0.59)** 点は示唆的で、**短文単独では「強調」は伝わりにくい**（文内に対比対象がないため）。ガヤの演出タグに「強調」系を置いても効果が薄い可能性がある。

### 4.2 短文で効く順序（前川MDSの寄与順）

1. **発話全体の持続時間（DUR）** — 最強の因子。テキスト長に対して発話長を大きく振る
2. **末尾2モーラのF0レンジ（PR_F）** — 語尾処理
3. **振幅（RMS）** — 音量
4. **句頭F0上昇の有無と幅**
5. **声質（H1-A3 / phonation type）**
6. **アクセント核F0下降のタイミング**

### 4.3 短文特有の構造的制約

| 制約 | 帰結 |
|---|---|
| 宣言的下降(declination)を張る余地がない | 「文全体のF0の描き方」で演技を作れない。→ **端点（立ち上がりと語尾）に情報を集約するしかない** |
| 発話内ポーズが0〜1個しか入らない | 「間」は**発話内**ではなく**発話前**（無音→第一声の落差）と**語尾の余韻**として実装する |
| 文脈が音声内に存在しない | 「前史」を発話冒頭の声の状態に圧縮する。冒頭がmodal・平常F0だと必ず朗読になる |
| 語尾が発話の1/3を占める | 語尾処理の重みが長文の3倍以上。BPMと末尾声質のバリエーションが決定的 |

### 4.4 「立ち上がりのアタック」の理論的裏付け

感情認識研究では、**1秒と2秒の間で性能が最も大きくジャンプ**する。そして重要なのは、**自発音声は最初の数秒で既に感情情報を担っているのに対し、台本音声（scripted）は最初の1〜2秒からの予測が困難**という差が報告されている点 [S18]。

**これは「朗読っぽさ」の音響的定義そのものである**: 朗読は感情が発話の後半になってようやく立ち上がる（あるいは立ち上がらない）。演技は**第一声から既に感情が乗っている**。

→ **ガヤの実装要件**: クリップの先頭 0〜300ms の時点で、そのクリップの感情/態度が確定していること。テキストの意味を読んでから感情が乗り始める設計では必ず朗読に聞こえる。

具体的なチェック: クリップの先頭 500ms だけを切り出して感情認識モデルに掛け、全長での予測と一致するか。一致率が低ければ「立ち上がりが遅い＝朗読的」。

### 4.5 ガヤ収録の現場知見（そのままTTS設計要件になるもの）

| 現場知見 | 出典 | TTS設計への翻訳 |
|---|---|---|
| ガヤは「聞き取れるか取れないかぐらいの声」だが、**おろそかにできない**。ベテランと新人で明確に差が出る | 明田川進 [S12] | 個々のクリップの品質が全体の質感を決める。平均化してはいけない |
| 重要なのは**声を張り上げることではなく、求められている雰囲気を読み取ること** | 明田川進 [S12] | 音量/テンションを上げても演技にはならない。**声質と語尾と間で作る** |
| 現在のガヤは**メインとは別に後からまとめて録る**。4〜5人で録り、**マイク位置を変えて同じ4人にもう一度、計3回**、重ねて大人数を作る | 明田川進 [S12] | **1クリップに全部背負わせない**。TTS側は「レイヤに重ねる前提の分散」を設計する。同一話者で距離・角度違いのバリエーションを生成する価値が高い |
| ベテランの声が前に出るようミックスする | 明田川進 [S12] | ガヤは均質であってはならない。前景/中景/背景の階層を意図的に作る |
| ゲーム音声は**「抜き撮り」（1人ずつ収録）**で掛け合いがない。演出が距離感・シチュエーションを言葉で補う | [S19] | TTSも同じ制約下にある。**各クリップに「相手との距離」「場面」のメタデータを持たせる**ことが人間収録での不足補填と等価 |
| 汎用台詞（ダメージ声・押しつぶされる声など）は注釈と前後の台詞を参考に調整する | [S19] | 汎用ラインこそメタデータ依存度が高い |
| 「もっと強そうに」のような**ざっくりした指示では「正解ではあるがちょっと違う」ズレが生じる**。感情の根拠を補足することが重要 | [S19] | プロンプト/タグは形容詞だけでなく**状況＋動機**を含める（§3.3のintent/obstacle設計） |
| 感情温度スケール1–5をスクリプトに書き込むと現場のコミュニケーションが劇的に速くなる | [S14] | ベンチのメタデータスキーマに `intensity: 1-5` を必須項目として入れる |

---

## 5. 「演技度」の定量指標 — ベンチ自動評価向けの提案

### 5.0 使ってはいけない指標（先に明示）

| 指標 | 問題 |
|---|---|
| **log F0 RMSE** | 人間の韻律知覚との相関 r̄=0.30。ピッチ偏差の一部しか捉えず、リズム・強度を無視 [S20] |
| **MCD** | 参照音声必須。r̄=0.66だが、ガヤには「唯一の正解」が存在しないため原理的に不適 |
| **LALM (Gemini等) as judge** | 韻律差の評価者としては r≒0.22–0.27 で信頼できない。プロンプト感度も高い [S20] |
| フレーム単位の音響比較全般 | 「1発話に最適解は1つ」を仮定するため、**妥当な韻律のバリエーションを不当に減点する** [S20] |

### 5.1 レイヤA: クリップ内 演技度（intra-clip expressiveness）

すべて eGeMAPSv02（88パラメータ、F0はセミトーン尺度、H1-H2/H1-A3/spectral slope/alpha ratio/Hammarberg index/jitter/shimmer/HNR/loudness peak rate 等を含む）[S21] から算出可能。

| # | 指標名 | 定義 | 狙い |
|---|---|---|---|
| A1 | `f0_span_st` | 有声区間F0の 5–95%tile差（semitone） | 高低の幅 |
| A2 | `f0_sd_st` | 有声区間F0の標準偏差（semitone） | 単調さ検出 |
| A3 | `attack_slope` | 発話開始 0–150ms の loudness 立ち上がり傾き | 立ち上がりのアタック |
| A4 | `onset_f0_dev_st` | 最初の有声100msのF0中央値 − 発話全体F0中央値（semitone） | 第一声の位置。0付近＝平常＝朗読的 |
| A5 | `tail_f0_slope_st_s` | 末尾250msのF0回帰傾き（semitone/s） | 語尾の方向（上げ/下げ/平ら） |
| A6 | `tail_pr_st` | **末尾2モーラのF0レンジ**（semitone） | 前川MDS DIM2の主変数。最重要 |
| A7 | `tail_h1h2` | 末尾200msのH1-H2 | 語尾の息漏れ／pressed |
| A8 | `vq_h1a3` | 発話全体のH1-A3中央値 | 発声様式（D≒34dB / N≒26dB / S≒19dB を目安に3値化可）[S3] |
| A9 | `rate_local_cv` | 200ms窓ごとのモーラ数の変動係数 | 緩急 |
| A10 | `energy_span_db` | loudnessの5–95%tile差 | 強弱 |
| A11 | `pause_profile` | 発話内無音（>60ms）の個数・総長・最長 | 間 |
| A12 | `dur_per_mora_ratio` | 実測モーラ平均長 ÷ 中立基準モーラ長 | **前川MDS DIM1の主変数**。1.0付近＝中立、2.0付近＝顕著 |
| A13 | `breath_presence` | 発話頭200ms内の吸気ノイズ有無（0/1） | 生理性 |
| A14 | `early_emotion_consistency` | 先頭500msのSER予測 と 全長SER予測 のコサイン類似度 | **§4.4の朗読性検出。低い＝立ち上がりが遅い＝朗読的** |

### 5.2 レイヤB: クリップ間 多様性（inter-clip diversity）— 「全部同じ」の検出

「朗読っぽい」という評価は、しばしば**個々のクリップではなくセット全体の均質性**から来る。ガヤは数十〜数百クリップを並べるので、この層が支配的になりやすい。

| # | 指標名 | 定義 | 備考 |
|---|---|---|---|
| B1 | **`DS-WED`** | VADで前後無音を除去 → SSL（WavLM-base または HuBERT-base の**第8層**）で埋め込み → **k-means k=50** で離散トークン化 → トークン列間の**重み付きLevenshtein距離** | 人間の韻律多様性判断との相関 **r̄=0.77**（MCD 0.66, logF0RMSE 0.30 を大きく上回る）。RTF=0.110 とMCD(0.203)・logF0RMSE(0.549)より高速。k=50 が最良、中間層(6–9層)が最良、WavLM-baseの方が安定 [S20]。**置換操作の重みを挿入/削除より大きくする**と、抑揚・語強勢への知覚感度に合いやすい |
| B2 | `egemaps_gen_var` | セット全体のeGeMAPS 88次元共分散行列の一般化分散（log det）または上位主成分の分散 | 解釈可能な多様性 |
| B3 | `per_feature_cv` | A1–A12 各指標のセット内変動係数 | どの軸が固まっているか診断できる |
| B4 | `tail_type_entropy` | 語尾タイプ（上昇/下降/平ら/伸ばし/切り）分布のエントロピー | 語尾の一様さ検出 |

### 5.3 レイヤC: 「演技度」合成スコア — 参照分布との対数尤度比

最も筋が良い設計は、**絶対値の閾値ではなく参照分布との比較**。

```
演技度(clip) = log P(x | 演技コーパス) − log P(x | 朗読コーパス)
```

- `x` = A1–A13 の特徴ベクトル（またはSSL埋め込みの統計量）
- **演技側参照**: 既存ゲーム/アニメの人間収録ガヤ、または声優収録の参照セット
- **朗読側参照**: 既存の朗読調TTS出力、朗読コーパス（JSUT等）
- 実装は各分布をガウス（または GMM）で近似 → マハラノビス距離差、あるいは単純に**二値分類器の対数オッズ**

**分類器設計の注意（重要な実測知見）**:
- 朗読/自発の二値分類は、handcrafted特徴（eGeMAPSv02 + 話速 + 発話/無音/重複区間の長さ統計）で英語 AUC 0.94 に達するが、**日本語は全言語中最低クラス（eGeMAPSv02で AUC 0.65、handcraftedで 0.70）**。Whisperなどのtransformer埋め込みでは日本語 AUC 0.78–0.79 まで改善するが、それでも他言語（英語0.97）に遠く及ばない [S1]。
- 論文は原因として日本語がモーラ拍リズム言語であることを挙げている。
- **→ 日本語ガヤの演技度分類器は、必ず SSL/Whisper 系埋め込みを主特徴にし、handcrafted特徴は解釈用の補助に留めること。**
- eGeMAPSv02 に**話速と発話/無音区間の長さ統計を足すと scripted F1 が 0.69→0.76 に改善**した [S1]。→ ポーズ・話速統計は必ず入れる。

### 5.4 レイヤD: 「棒読み検出器」— 早期失敗検知（fail-fast）

合成スコアより先に、明快な失格条件を置くと運用が楽になる。以下は出発点となる目安値（実データでキャリブレーション必須）:

| フラグ | 条件 | 意味 |
|---|---|---|
| `FLAT_PITCH` | `f0_sd_st` < 1.5 semitone | 平坦 |
| `NO_ATTACK` | `\|onset_f0_dev_st\|` < 0.5 semitone かつ `attack_slope` が下位20% | 第一声が平常＝前史がない |
| `UNIFORM_TAIL` | セット内 `tail_f0_slope_st_s` のSDが下位10% / `tail_type_entropy` が低い | 語尾が全部同じ |
| `FIXED_VQ` | セット内 `vq_h1a3` のSD < 2dB | 声質がmodal固定 |
| `FIXED_RATE` | セット内 `dur_per_mora_ratio` のCV < 10% | 発話長が振れていない（**前川MDS DIM1が動いていない＝最重要フラグ**） |
| `LATE_EMOTION` | `early_emotion_consistency` < 0.6 | 感情の立ち上がりが遅い＝朗読的 |
| `LOW_DIVERSITY` | セットの DS-WED 平均が参照セットの下位10%tile未満 | セット全体が均質 |

### 5.5 生成側への診断的示唆（指標が低かった場合の原因候補）

DS-WEDによるゼロショットTTSベンチマークから、韻律多様性を下げる既知要因 [S20]:

| 要因 | 影響 | 対処の方向 |
|---|---|---|
| **NAR flow-matching系（E2-TTS, F5-TTS, ZipVoice）** | AR系（XTTS-v2, CosyVoice）より韻律多様性が明確に低い。暗黙アラインメントによる回帰目的が multimodal な韻律分布を平均化し、over-smoothed な出力になる | AR系または MGM系（MaskGCT）を検討 |
| **duration制御の欠如** | duration perturbation（係数0.8–1.2）を掛けるだけで DS-WED が **+13〜28%** 改善。F5-TTSでは約30%改善 | **duration variation は韻律多様性の中核ドライバ**。明示的なduration制御を持つモデル／後処理でのduration摂動が即効性のある改善策 |
| **DPO等のRL後処理** | 明瞭性向上と引き換えに韻律多様性が **−3〜−19%**（CosyVoice2で−18.8%/−18.6%） | 明瞭性最適化を掛けすぎていないか確認 |

**前川の理論と突き合わせると整合的**: MDS DIM1（顕著さ）の主変数は DUR。duration が動かないモデルは、**パラ言語情報の第一主成分そのものが表現できていない**。「朗読っぽい」という評価は、これの直接的な帰結である可能性が高い。

---

## 6. 出典一覧

### 学術文献

- **[S3]** 前川喜久雄「パラ言語情報の生成と知覚 (Production and Perception of Paralinguistic Information)」国立国語研究所. https://www.gavo.t.u-tokyo.ac.jp/tokutei_pub/houkoku/model/maekawa.pdf
  - 本レポートの中核。6PI類型の音響分析（持続時間・F0・フォルマント・スペクトル傾斜）、EMAによる調音計測、高速内視鏡による声門観察、MDSによる知覚空間、二経路生成モデル。
  - 関連: K. Maekawa, "Production and Perception of 'Paralinguistic' Information," Proc. Speech Prosody 2004, Nara, pp. 367-374.
  - 関連: K. Maekawa et al., "X-JToBI: An extended J_ToBI for spontaneous speech," Proc. ICSLP 2002, Denver, 3, pp.1545-1548.
  - 関連: M. Fujimoto and K. Maekawa, "Variation of phonation types due to paralinguistic information: An analysis of high-speed video images," Proc. ICPhS 2004.
  - 関連: 川上蓁「文末などの上昇調について」国語研究 No.16, pp.25-46 (1963).
- **[S1]** S. Elisha, A. McDowell, M. Beguerisse-Díaz, E. Benetos, "Classification of Spontaneous and Scripted Speech for Multilingual Audio," (Spotify / QMUL), arXiv:2412.11896. https://arxiv.org/pdf/2412.11896
  - 11言語グループでの朗読/自発分類。日本語が最難。eGeMAPSv02+話速・無音統計で scripted F1 0.69→0.76。言語別AUC表。
- **[S20]** Y. Yang, B. Han, et al., "Measuring Prosody Diversity in Zero-Shot TTS: A New Metric, Benchmark, and Exploration," arXiv:2509.19928. https://arxiv.org/pdf/2509.19928
  - DS-WED の定義（VAD→SSL 8層→k-means k=50→重み付き編集距離）、ProsodyEval データセット、PMOS との相関（DS-WED 0.77 / MCD 0.66 / logF0RMSE 0.30）、AR vs NAR、duration perturbation、DPO の影響。
- **[S2]** Daly & Zue, "Statistical and linguistic analyses of F0 in read and spontaneous speech," ICSLP 1992. https://www.isca-archive.org/icslp_1992/daly92_icslp.html
  - 4000超の朗読/自発対発話。自発音声の平均F0は有意に高いが、F0の変動幅は両スタイルでほぼ同等。
- **[S4]** 日本語のフォーカスプロソディ:
  - A. Lee & Y. Xu, "Conditional realisation of post-focus compression in Japanese," Speech Prosody 2018. http://www.homepages.ucl.ac.uk/~uclyyix/yispapers/Lee_Xu_SP2018.pdf
  - "Revisiting focus prosody in Japanese," Speech Prosody 2012. https://www.isca-archive.org/speechprosody_2012/lee12_speechprosody.html
  - "The f0 perturbation effects in focus marking: Evidence from Korean and Japanese," PLOS ONE. https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0283139
  - 焦点によるF0レンジ拡大・強度増加・句末境界音調・後続句圧縮(PFC)。日本語のPFCは有核語条件で productive。
- **[S5]** K. R. Scherer, "Acoustic Profiles in Vocal Emotion Expression." http://www.columbia.edu/~rmk7/HC/HC_Readings/Scherer.pdf / K. R. Scherer, T. Johnstone, "Vocal Expression of Emotion."
- **[S6]** C. H. C. Del Valle, "Acoustic markers of emotional expression in professional lyrical singing," Front. Psychol. 17:1880734 (2026). https://doi.org/10.3389/fpsyg.2026.1880734
  - 歌唱が対象だが、怒り（声道収縮・声門内転増・SPL上昇・突発的振幅ピーク・F3-F4集中）vs 喜び（広いダイナミックレンジ・F0変動大・明るい共鳴）の弁別が明快。Scherer et al. (2017) の分類も引用。
- **[S7]** 前川喜久雄・五十嵐陽介・菊池英明・米山聖子・小磯花絵「『日本語話し言葉コーパス』のイントネーションラベリング Version 1.1」. https://clrd.ninjal.ac.jp/csj/manu-f/intonation.pdf
  - X-JToBI。句末境界音調(BPM)の目録、アクセント句/イントネーション句、BIラベル。
  - Japanese ToBI Labelling Guidelines: https://kb.osu.edu/server/api/core/bitstreams/8946fdd1-b865-5e6e-89e5-4d8b48652352/content
- **[S8]** 呼吸音と自然性:
  - "The effects of breath sounds on the perception of synthetic speech," PubMed 7759655. https://pubmed.ncbi.nlm.nih.gov/7759655/
  - "Frame-Wise Breath Detection with Self-Training: An Exploration of Enhancing Breath Naturalness in Text-to-Speech," arXiv:2402.00288. https://arxiv.org/html/2402.00288v1
- **[S9]** P. Keating & G. Kuo, F0 range 研究 / 「演劇的な読み方は F0 レンジが大きい」. https://linguistics.ucla.edu/people/keating/Keating_Kuo_Apr2012.pdf
- **[S18]** 短発話の感情認識:
  - "Attentive Convolutional Neural Network based Speech Emotion Recognition: A Study on the Impact of Input Features, Signal Length, and Acted Speech," arXiv:1706.00612. https://arxiv.org/pdf/1706.00612
  - "Towards Learning Emotion Information from Short Segments of Speech," IEEE. https://ieeexplore.ieee.org/document/10095892/
  - 自発音声は最初の数秒で既に感情情報を担うが、台本音声は最初の1〜2秒からの予測が困難。1秒→2秒で性能が最も大きくジャンプ。
- **[S21]** F. Eyben et al., "The Geneva Minimalistic Acoustic Parameter Set (GeMAPS) for Voice Research and Affective Computing," IEEE Trans. Affective Computing. https://sail.usc.edu/publications/files/eyben-preprinttaffc-2015.pdf
  - eGeMAPS 88パラメータ（周波数系: F0/jitter/フォルマント、エネルギー系: shimmer/loudness/HNR、スペクトル系: alpha ratio/Hammarberg index/spectral slope/H1-H2/H1-A3、時間系: loudness peak rate/有声・無声区間長）。F0は27.5Hzを0とするセミトーン尺度。
- 韻律強調制御: "EE-TTS: Emphatic Expressive TTS with Linguistic Information," arXiv:2305.12107. https://arxiv.org/html/2305.12107v2
  - POS/係り受け＋BERTから強調位置を予測し、pitch/duration/spectral energy を階層的に変調。自然性MOS 4.37 / 表現性MOS 4.24。
- 藤崎モデル: 石原達馬ほか「音声基本周波数の藤崎モデル指令列の統計的語彙モデル」. https://www.kecl.ntt.co.jp/people/kameoka.hirokazu/publications/Ishihara2013ASJ03published.pdf
  - フレーズ指令（句全体のF0ベースライン）とアクセント指令（局所的なF0隆起）の分離。**「演技」のグローバル制御＝フレーズ指令の振幅、局所制御＝アクセント指令の振幅、という分離が前川の二経路モデルと整合する。**

### 演技理論・実務知見

- **[S15]** Stanislavski の目的/障害/戦術:
  - Stanislavski's system (Wikipedia). https://en.wikipedia.org/wiki/Stanislavski's_system
  - "Actions, Objectives and Super-Objectives," The Maydays. https://www.themaydays.co.uk/actions-objectives-and-super-objectives/
  - C. Gutekunst & J. Gillett, *Voice into Acting: Integrating Voice and the Stanislavski Approach* (Bloomsbury).
  - "Mic 'Stan': Infusing Stanislavski Technique into your VO Reads," Such A Voice. https://www.suchavoice.com/blog/2019/08/22/mic-stan-infusing-technique-vo/
- **[S12]** 明田川進（音響監督）「音物語」第69回「ガヤは役者と音響スタッフの腕の見せどころ」アニメハック. https://anime.eiga.com/news/column/aketagawa_oto/117964/
- **[S19]** 「ゲーム音声収録における演出家の役割とは？」G-angle. https://www.g-angle.co.jp/blog/voice/game_voice_director/
- **[S13]** 音田「いいディレクションとは？」note. https://note.com/otoda_0101/n/ne71f90d61ec7 / 「ディレクションを聞いてはいけない」 https://note.com/otoda_0101/n/n27da15354fe8
- **[S14]** "Game Voice Directing Guide: Script to Final Audio Asset," Muziument. https://muziument.com/en/blog/game-sound-director-voice-directing-workflow
  - 感情温度スケール1–5、トリガー状況の1文注記、固有名詞の発音表記。
  - 関連: "What a Voice Director Is Listening for From Video Game VO Actors," Backstage. https://www.backstage.com/magazine/article/what-a-voice-director-is-listening-for-from-video-game-vo-actors-73006/
- **[S16]** bark の書き方:
  - "How to write for video games, Level One: Barks," The Narrative Dept. https://www.thenarrativedept.com/blog/barks
  - S. Beaulieu, "How a character says hello: writing 'barks' for video games." https://sarah-beaulieu.com/en/writing-barks-for-video-games
- **[S17]** 日本の声優訓練の技術体系:
  - 「【声優志望必見】セリフ練習に役立つ！声の表現力UPトレーニング」NAYUTAS. https://nayutas.net/school/okayama/blog/49159/
  - 「声優養成所で学ぶ！キャラクターを魅力的に演じるための実践的テクニック」インターナショナル・メディア学院. https://iam.tv/column/197/
- **[S11]** 「セリフのニュアンスは語尾で決まる！声優の語尾を収める技術とは？」. https://yu-goblog.com/101
- **[S10]** 「間」の理論:
  - 「【お笑い/落語】『間(ま)』の正体！面白さが決まるタイミングと芸人の神業」. https://ameblo.jp/kimurerl/entry-12949910497.html
  - 渡部直人「落語におけるパフォーマンスの特殊性とその変化」笑い学研究30 (2023). https://www.jstage.jst.go.jp/article/warai/30/0/30_129/_pdf/-char/ja
- モブ収録の実務: 「モブ収録（ガヤ収録）って何？声優の仕事の裏側」Voice Camp. https://note.com/voicecamp_school/n/n980d0c6b6dfa
- 演技コーパスの誇張バイアス: acted emotion は自然発生の感情より誇張・高強度で、認識率も高く出る（IEMOCAP は acted と improvised を併有、EMODB/eNTERFACE/LDC は acted のみ）。関連レビュー: "Towards Speech Emotion Recognition 'in the wild'," arXiv:1708.03920. https://arxiv.org/pdf/1708.03920

---

## 付録: 実装優先度の提案

理論から導かれる、効果の大きい順:

1. **発話長（duration）をテキスト長に対して大きく振る** — 前川MDS第1主成分。duration摂動だけで DS-WED +13〜28% の実測もある。最も費用対効果が高い。
2. **語尾処理（末尾2モーラのF0レンジ・傾き・声質）を独立制御軸にする** — 前川MDS第2主成分。短文では発話の1/3を占める。
3. **第一声から感情を立てる** — 冒頭のF0を平常値からずらし、声質を modal から外す。`early_emotion_consistency` で監視。
4. **声質（H1-A3 / phonation type）をタグ化する** — breathy(落胆系) / modal / pressed(疑い・怒り系) の3値だけでも効く。
5. **発話前の無音と吸気音** — 「間」は短文では発話内でなく発話前に置く。
6. **クリップ間の分散を明示的に最大化する** — intent × tactic の直積生成 → DS-WED で最も離れた候補を採択。
7. **句末境界音調(BPM)を記号レベルで指定する** — 日本語固有で、非母語話者には知覚できないほど言語依存的（＝日本語話者には確実に効く）。
