# 演技力・表現力の TTS 再現手法 — 統合レポート

- **統合日:** 2026-07-29
- **入力:** `theory.md` / `data.md` / `data-pd-addendum.md` / `methods.md` / `codex.md`

## 1. 意思決定

Gaya Bench の「朗読っぽい」「無感情な棒読み」を、一つのモデル改造や一つの自動指標で解決しない。次の実装順とする。

1. **現在ある逐行演技制御を実出力で比較し、Qwen だけ感情参照 A/B を行う。**
2. **読み・機械品質の hard gate と、演技候補の soft ranking を持つ N テイク運用を作る。**
3. **日本語人評で相関が確認できた指標だけ ranking に採用する。**

Qwen x-vector arithmetic、EmoSteer、TED-TTS は研究トラックに置く。Step-Audio-EditX はモデル重みの利用条件が確定するまで採用しない。人間ガイドからの VC は品質面では有力だが、TTS ベンチとは別企画とする。

### 優先順位

| 優先 | 施策 | Issue | 目的 | 完了条件 |
|---:|---|---|---|---|
| P0 | 既存 native-control 3 経路の比較 + Qwen 感情参照 A/B | #74 後続実験 / #79 | 既にある能力を測り、中立参照原因仮説を検証 | blind 人評、読み・声質非悪化 |
| P0 | 読み QA と音響 feature 抽出 | #75 | hard gate と scorer の共通基盤 | 誤読再現、`gaya qc`、全 clip report |
| P1 | N テイク harness 設計・実装 | #76 | 数打ちガチャを人間が扱える量へ絞る | hard gate / soft rank / 人間選抜を分離 |
| P1 | れきおん取得条件と anchor 候補 | #82 | duration / pause の日本語参考資産 | **調査済み: 公式音源DLなし。提供照会または新規収録が必要** |
| P2 | x-vector 日本語 canary | 新規実験 Issue | Qwen の連続的感情強度 | ライセンス解決後、3 emotion の A/B |

## 2. 統合した事実

### 2-1. 「演技」は多次元

Claude 理論調査の中心結論、すなわち演技差は F0 だけでなく、duration、pause、stress、energy、voice quality、開始直後の立ち上がり、take 間の変化に現れるという方向は妥当である。特に [DS-WED](https://arxiv.org/html/2509.19928v3) は、log F0 RMSE と prosody difference 人評の相関が `0.30` にとどまり、semantic token distance が `0.77` だったことを示す。

ただし、この `0.77` は英語の同条件 5 take 間の**差の大きさ**に対する値である。演技の正しさ、感情一致、採用可否の値ではない。したがって、Gaya Bench では次の 4 層を混同しない。

1. **機械健全性:** ファイル、codec、loudness、true peak、異常尺、無音。
2. **内容一致:** 読み、欠落、反復、尻切れ。
3. **表現記述:** 話速、pause、F0、energy、SER embedding、take 間 diversity。
4. **作品判断:** 台詞意図、役柄、自然さ、ゲーム素材として採用可能か。

1 と、十分に検証された 2 は hard gate にできる。3 は校正前には soft signal、4 は人間判断である。

### 2-2. 現在の adapter が既に持つ演技経路

旧 `methods.md` の執筆後に実装が進み、現在は次が存在する。

- `irodori_tts.py`: caption に voice / emotion / intensity / delivery を入れ、emotion emoji も本文へ付与する。clone と no-ref VoiceDesign の両方を持つ。
- `voxcpm2.py`: registered reference または character VoiceDesign reference を使い、逐行の emotion / intensity / delivery を controllable clone prefix にする。
- `cosyvoice3.py`: `inference_instruct2` へ emotion / intensity / delivery を渡す。
- `qwen3_tts.py`: character ごとの中立 VoiceDesign reference を Base clone に使い、逐行 emotion は渡さない。

このため、新方式を増やすより先に Irodori / VoxCPM2 / CosyVoice3 の演技追従と日本語読みを同じ行で比較する。Qwen は構造が違うため #79 で別 A/B とする。

### 2-3. データ

- IEMOCAP は internal research only、commercial use 禁止であり、本番経路に入れない。
- MELD は原番組由来の権利処理を確認できず、本番経路に入れない。
- CREMA-D は ODbL / DbCL を確認できるが、実演家に関する追加の利用保証としては扱わない。
- FastLabel × アマナイメージズは、収録者・話者の許諾取得を販売者が明記する有力候補である。ただし評価器学習、特徴抽出、生成利用、派生物の条件と価格を契約で確認するまで採用確定ではない。
- れきおんは NDL が「保護期間満了」と表示した個別資料だけを候補とし、#82 で取得方法とサイト条件を確認する。用途は韻律 anchor に限定し、声質クローンへ転用しない。

## 3. 優先上位 3 施策の実装スケッチ

### A. model-native 制御 + Qwen 感情参照

#### 目的

追加モデルなしで、現在の generation path がどこまで演技を出せるかを確定する。

#### 現行経路

```text
scenario line
  ├─ Irodori: ref? + caption + emoji
  ├─ VoxCPM2: ref + (emotion/intensity/delivery) prefix
  ├─ CosyVoice3: ref + instruct2
  └─ Qwen Base: character reference only
```

#### #79 の最小変更面

- 対象: `pipeline/src/gaya_pipeline/adapters/qwen3_tts.py`
- 感情別の reference text / VoiceDesign instruction を明示 table で定義する。
- reference identity と cache key に `emotion` と実験対象の intensity band を含める。
- `generation_input()` に reference SHA、emotion、intensity、reference recipe を含め、現行 generation hash で厳密に分離する。
- cached reference の WAV / JSON pair、identity、SHA が不一致なら失敗する。neutral へ切り替えない。
- profile の `emotion=True` は、A/B で逐行情動が実際に変わることを確認してから変更する。

#### 実験範囲

最初から `30 character × 12 emotion × 3 intensity = 1,080 reference` を生成しない。2 character × neutral / cheerful / angry / whisper × 代表強度で A/B し、次を通った場合だけ拡大する。

- human preference が neutral reference より改善。
- speaker similarity と読みが悪化しない。
- emotion 間で音色 drift が許容範囲。
- reference の生成コストと cache 容量を実測できる。

#### 既存 3 adapter の比較

コード変更前に 24 行の固定評価 set を定義する。現在の adapter は行ごとに固定 seed を再設定するため、#76 の take context 導入前は各 adapter・各行 1 take とする。評価項目は `意図一致 / 役として自然 / 緩急 / 採用可否`。同じ評価 set を #75 / #76 へ引き渡す。

### B. 読み QA と音響 feature 抽出

#### 目的

明確な読み違い・欠落・反復を再現可能に検出し、演技を記述する音響 feature を人評と結び付ける。未校正 feature で自動採否は行わない。

#### コード境界

- CLI: `pipeline/src/gaya_pipeline/cli.py` に `gaya qc` を追加する。
- QC: 新規 `pipeline/src/gaya_pipeline/qc/` に reading 比較、音響 feature、report 出力を分離する。
- 入力: scenario の resolved reading と既存 manifest / artifact。生成 adapter やモデルを再ロードしない。
- 出力: clip ごとの機械健全性、ASR transcript、reading 差分、rate / pause / F0 / energy を持つ versioned report。dry benchmark の manifest と音声は書き換えない。

#### 判定境界

- expected text は表示文字列ではなく、明示 `reading` を優先した既存の resolved reading を使う。
- ASR 側も日本語の表記揺れを吸収できる kana 比較単位へ正規化する。
- hard gate は明確な欠落・反復・停止だけに限定し、僅差の edit distance を品質順位へ使わない。
- rate / pause / F0 / energy は初期段階では report 専用とし、24 行以上の blind 人評で相関を確認してから重みを決める。
- 生成モデルを解放した別工程で実行し、12GB VRAM に TTS と ASR / scorer を同時常駐させない。

### C. hard gate と soft ranking を分離した N テイク harness

#### 目的

一発生成を捨て、品質の悪い take を機械で除き、聞く価値のある候補だけ人間へ出す。

#### データフロー

```text
gaya gen --takes N
  -> adapter-specific seed / sampling を明示して N 回生成
  -> 共通 normalize + Opus encode
  -> hard gate
       file / loudness / true peak / silence / abnormal duration
       ASR reading / omission / repetition
  -> soft features
       rate / pause / F0 / energy
       optional calibrated SER embedding
       group-level DS-WED
  -> rank without auto-accept
  -> R2 には gate 通過 take のみ
  -> site で blind 比較し selected take を export
```

#### コード境界

- CLI: `pipeline/src/gaya_pipeline/cli.py`
- orchestration: `pipeline/src/gaya_pipeline/generation.py`
- adapter contract: `pipeline/src/gaya_pipeline/adapters/base.py`
- QC: 新規 `pipeline/src/gaya_pipeline/qc/`。generation adapter へ scorer を埋め込まない。
- manifest: `pipeline/src/gaya_pipeline/manifest.py` と site schema。

現在の result key は `(model, scenario, line, variant)` で、1 行 1 clip しか保持できない。`variant` 文字列へ take 番号を埋め込む互換 hack は使わず、設計 Issue #76 で新しい manifest format を定義する。最低限、`take`、実際の seed / sampling、gate results、soft scores、selection state を一意に持たせる。旧 format は暗黙に読まず fail fast する。

adapter の seed は現在多くが module 定数である。`--takes` 実装時は、各 adapter が受け取る明示的な take context を設計し、`generation_input()` と `generate()` が同じ seed / sampling を使うようにする。runner が外から乱数だけ変更し、hash に残さない方式は禁止する。

#### gate 方針

| 判定 | 初期用途 |
|---|---|
| PCM/Opus、loudness、true peak、NaN、無音、極端な尺 | hard reject |
| #75 の明確な読み不一致、欠落、反復 | hard reject。ただし dry 比較結果は別 variant として保持 |
| emotion2vec / SER | 診断 feature。ライセンスと日本語相関の確認前は ranking にも使わない |
| duration / rate / pause / F0 / energy | soft feature。固定閾値を置かない |
| DS-WED | 同一行 take 群の diversity 監査。単一 take reject に使わない |
| early-500ms / full SER consistency | 仮説ログのみ。採否に使わない |

#### コスト

生成時間と一時保存量は概ね N 倍になる。公開 Opus は 64kbps のためストレージより GPU 時間が支配的である。現在の Qwen 実測 metadata からは 1 sweep が約 44.8 分、N=5 が約 3.75 時間となる。N=2–3 から始め、VoxCPM2 の公式推奨 1–3 回と実測通過率を見て増やす。全モデル一律 N=10 は行わない。

### 補足研究: Qwen x-vector arithmetic の隔離実験

#### 目的

Qwen Base clone の声質を保ちながら、angry / happy / sad の強度を連続制御できるかを日本語で確認する。

#### 前提 gate

1. 公開実装と配布 `tau` のライセンスを著者に確認する。または論文の式からコードを独立実装し、無許諾実装をコピーしない。
2. Qwen の固定 revision に対し、voice clone prompt 内の x-vector 位置と shape をテストで固定する。
3. ESD 等の非商用データ由来 `tau` は研究実験から本番へ持ち込まない。

#### 実装境界

- adapter 本体へ汎用 fallback を足さず、研究用の明示 mode または別 adapter ID とする。
- `tau` source、SHA、source speakers、emotion、alpha、Qwen revision を generation params / input hash に含める。
- expected field / shape がなければ失敗する。通常 clone へ黙って戻らない。
- 生成モデルをロードしたまま別 scorer を常駐させない。12GB の追加コストは x-vector 演算ではなく評価モデル側で管理する。

#### canary

- 2 character × angry / happy / sad × alpha 0 / 0.5 / 1 / 1.5。
- human preference、speaker similarity、ASR、loudness、artifact を測る。
- alpha 増加に対する人評が単調に改善する範囲があり、声質・読みが悪化しない場合だけ続行する。

この実験は #79 の結果後に行う。参照 bank だけで十分改善するなら、Qwen 内部 API への介入を増やさない。

## 4. 指標の採用ルール

### production hard gate

- codec / sample rate / channel / PCM validity
- integrated loudness / distribution true peak
- NaN / Inf / empty audio
- 校正済みの異常 duration / silence
- #75 で明確に定義された読み欠落・反復

### soft ranking

- speaking rate、pause count / duration
- voiced ratio、F0 range / slope、energy dynamics
- 目標 emotion に対する SER / embedding similarity
- speaker similarity
- naturalness estimator

### group-level audit

- DS-WED
- take 間の duration / pause / F0 / energy 分布
- model / emotion / character ごとの通過率

### 採用しない固定ルール

- log F0 RMSE 単独
- emotion2vec probability 単独
- emotion2vec / EECS による Best-of-N ranking
- early-500ms と全長 SER の不一致だけで reject
- 未校正の eGeMAPS 数値閾値
- LALM-as-judge の自動採否

soft score の重みは、24 行以上の blind 人評との相関から決める。[The False Resonance](https://arxiv.org/html/2604.26347) が示す speaker / linguistic interference を踏まえ、emotion2vec / EECS は相関確認前に重みを持たせない。全 model に一つの閾値を共有せず、model-specific な score distribution を監査する。

## 5. 実験計画

### Phase 0 — 評価 set

- 6 emotion: neutral / cheerful / angry / sad / whisper / shout
- intensity 1 と 3
- 2 character
- 読み曖昧語、短文、呼びかけ、感嘆、間を含む 24 行
- 人評 rubric と blind ID を固定

### Phase 1 — 現行 3 adapter + Qwen A/B

- Irodori / VoxCPM2 / CosyVoice3: #76 前は固定 seed の各 1 take
- Qwen: neutral reference vs emotion reference
- 出力: 人評、既存 QC、#75 feature report

### Phase 2 — scorer 校正

- 人評 `採用可否` と各 feature の関係を測る。
- model / emotion / character を跨いで安定しない feature は表示専用にする。
- emotion2vec / DS-WED の公式コード・重みはライセンス確認前には実行しない。独立実装も、依存モデルとデータのライセンスが明確な場合だけ行う。

### Phase 3 — #76 実装

- N=3 で開始。
- hard reject、soft rank、人間選択を別状態として保存。
- 通過率と人間が聞く take 数を計測し、N=5 の費用対効果を判断する。

## 6. Issue 引き渡し

### #75

- ASR は「文字列完全一致」ではなく、日本語 reading / kana の比較単位を明記する。
- dry benchmark は自動修正しない。修正生成は別 variant とする。
- rate / pause / F0 / energy は report に記録するが、初期閾値で reject しない。

### #76

- hard gate と soft rank を schema 上も分離する。
- result key に take identity が必要。`variant` への文字埋め込みで済ませない。
- seed / temperature / sampling は adapter が実際に使用した値を hash と sidecar に残す。
- DS-WED は group-level、SER は soft、human selected が最終状態。
- ASR verifier は family bias を持ち得るため、僅差の Best-of-N ranking を単一 ASR に任せない。

### #79

- 最初は小規模 A/B。全 bank の生成は A/B 合格後。
- neutral fallback を作らず、欠けた emotion reference は生成前に失敗する。
- emotion 別の voice drift を必ず人評する。

### #82

- NDL の個別資料の公開区分、persistent ID、取得日、加工履歴を記録する。
- 取得条件が不明な音声を stream capture しない。
- 用途を prosody anchor に固定し、speaker clone / fine-tune へ流用しない。
- SP 音質のため、anchor の価値は声質ではなく pause / duration / stress pattern で評価する。

## 7. 未解決事項

- FastLabel の価格と契約範囲。これは Owner の問い合わせ判断が必要。
- emotion2vec / DS-WED / x-vector 公式実装のライセンス。
- Qwen emotion reference が日本語で実際に効くか。
- scorer と日本語 RPG 短文の人評相関。
- れきおんは公式音源ダウンロードも複写も提供されていない。元音源の提供照会または新規収録へ切り替えるかの判断。

未解決事項を silent fallback や推定値で埋めない。必要なモデル、参照、score definition、schema version が欠ける場合は、その実験または生成を明示的に失敗させる。

## 8. 最終推薦

このプロジェクトで最初に作るべきものは、新しい巨大な感情モデルではなく、**同じ台詞を複数の既存制御経路で生成し、壊れた take を落とし、人間が短時間で最良 take を選べる仕組み**である。

Claude 調査が示した duration / pause / voice quality の重要性と、Codex 調査が確認した指標・ライセンスの限界を合わせると、次の原則になる。

> 自動評価は「演技を決める」ためではなく、「人間が聞く候補を減らし、判断根拠を残す」ために使う。

この方針なら、12GB の既存環境、現在の adapter 群、Issue #75 / #76 / #79 / #82 をそのまま活用でき、研究実装を本番経路へ早過ぎる段階で混ぜずに進められる。
