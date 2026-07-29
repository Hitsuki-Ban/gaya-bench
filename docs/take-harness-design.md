# 品質ゲート付き N テイク選抜ハーネス設計

- 対象 Issue: [#76](https://github.com/Hitsuki-Ban/gaya-bench/issues/76)
- 設計日: 2026-07-29
- 状態: 実装前レビュー

## 1. 決定

一発生成を公開 manifest へ直接書く現在の経路を廃止し、次の 4 工程へ分ける。

1. `gaya gen --takes N --seed-base S` が、明示した take identity と生成条件で
   ローカル候補を生成する。
2. TTS runtime を解放した後、機械品質と内容一致を hard gate として評価する。
3. hard reject でない候補だけを manifest v4 と R2 の策展用候補集合へ昇格する。
4. サイトで同じ台詞・モデルの候補を blind 比較し、人工選択を export して
   manifest の `curations` へ明示的に適用する。

全 attempt の真値は git 管理外の run ledger に残す。公開 manifest には
策展可能な candidate と確定済み curation projection だけを置く。hard reject、
生成失敗、QC 失敗を公開側で後から filter する構造にはしない。

最初の実装では seed だけを変える。temperature、top-p などは各 adapter が
実際に対応し、versioned recipe として値を監査できる場合だけ追加する。
初期 N は 3 とし、通過率と人間の試聴量を測ってから N=5 を判断する。
全モデル一律 N=10 は行わない。

### 目的

- silence、破損、明確な読み違いなど、聞く価値のない take を人間の前で落とす。
- seed、sampling、入力、出力、判定を再現可能な一つの履歴へ結ぶ。
- 自動指標を「演技の正解」とせず、最終判断を blind 人評へ残す。
- rejected take が R2 や通常の比較画面へ混入しない構造にする。

### 対象外

- emotion2vec / EECS / DS-WED による自動採用。
- 未校正の duration、F0、pause、speaker similarity 閾値。
- モデル固有 sampler の共通抽象化を先回りして作ること。
- rejected artifact の自動削除、R2 lifecycle、複数 curator の競合解決。
- manifest v3 と v4 の同時運用。

## 2. 現状と変更理由

現行経路には「1 model × 1 line × 1 variant = 1 clip」という前提がある。

- `LineJob` と adapter API は take context を持たず、多くの adapter が固定 seed を
  module 内に持つ。
- 生成物は `<line>-dry.{wav,opus,json}` へ固定され、第 2 take が第 1 take を
  上書きする。
- manifest v3 の key は `(model, scenario, line, variant)` である。
- `gaya qc` は公開 manifest の clip だけを入力とするため、公開前候補を gate
  できない。
- `gaya publish` は manifest の全 clip を固定の単一 take key へ upload する。
- サイトの通常比較、A/B、localStorage はいずれも take identity を持たない。

したがって `for take in range(N)` を足すだけでは、cache、manifest、QC、R2、
サイトのいずれでも候補を区別できない。候補生成と公開を先に分離し、その後
manifest とサイトを take-aware に切り替える。

なお、161 行中で明示 `reading` があるのは現状 1 行だけである。明示 reading が
ないこと自体を hard reject にすると運用不能になるため、内容 gate は
`pass / review_required / blocked` の 3 状態を持つ。

## 3. データフローと状態

```text
scenario + model + take plan
        |
        v
  local generation -----------------> generation_failed
        |
        v
  normalized WAV + final Opus
        |
        v
  Gate 1: mechanical ---------------> hard_rejected
        |
        v
  Gate 2: content ------------------> blocked
        |
        +---- pass or review_required
        v
  Gate 3: report-only features
        |
        v
  manifest v4 candidates -----------> R2 immutable candidate keys
        |
        v
  blind human curation
        |
        v
  curation.json --explicit apply---> tracked decision artifact + v4 curations
```

### 状態の意味

| 状態 | 意味 | manifest / R2 |
|---|---|---|
| `generation_failed` | adapter が音声を生成できなかった | 入れない |
| `hard_rejected` | 確定的な機械違反または active speech 0 | 入れない |
| `blocked` | QC runtime/error により品質が未判定 | 入れない。run 全体を非 0 終了 |
| `eligible:pass` | Gate 1 を通過し、権威ある reading と一致 | candidate に入れる |
| `eligible:review_required` | 明示 reading と ASR の不一致、または reading の権威性不足 | candidate に入れ、状態を表示する |
| `selected` | 人間が内容正しさと採用可否を確認 | `curations` から candidate を参照 |

`review_required` を `pass` と偽装しない。ただし、これは「基準未満」ではなく
人間確認が必要な候補であるため、策展用 R2 への昇格を許す。通常の benchmark
画面は `curations.decision=selected` の候補だけを表示する。全 N take が
hard reject の場合、
最もましな 1 take を自動採用せず、論理行を `no_eligible_take` とする。

## 4. Take identity と adapter 契約

### 4.1 TakeContext

runner は immutable な `TakeContext` を 1 attempt につき 1 個作り、同一 object を
adapter の入力記録と生成へ渡す。

```python
@dataclass(frozen=True)
class TakeContext:
    index: int                 # 1..N
    seed: int | None           # deterministic adapter は明示的に None
    recipe_version: str
    sampling: Mapping[str, JsonScalar]
```

adapter contract は次の形へ変更する。

```python
generation_input(job, take_context) -> Mapping[str, object]
generate(job, take_context, output_wav) -> GenerationMetadata
```

次を不変条件とする。

- `index` は表示と実行順のための値であり、単独では stable identity にしない。
- `seed` は versioned deterministic policy により
  `(seed_base, model, scenario, line, variant, index)` から導出し、adapter が受け取る
  有効範囲へ明示的に写像する。
- RNG を使用しない deterministic adapter は `seed=None` を明示し、架空の seed を
  identity に入れない。stochastic adapter では `seed=None` を拒否する。
- model revision、resolved input、reference identity/SHA、実際の seed/sampling、
  postprocess profile/version を canonical JSON 化し、その SHA-256 を
  `generation_input_sha256` とする。
- 生成成功後、`generation_input_sha256` と final Opus の完全 SHA-256 を canonical
  JSON 化して `take_id` を求める。したがって同じ入力でも音声 byte が異なれば
  別 take であり、同一 `take_id` が異なる音声を指すことはない。
- `generation_input()`、sidecar、ledger、manifest の生成条件は一致しなければならない。
- requested parameter を upstream が無視した場合、または realized metadata と
  一致しない場合は失敗する。
- postprocess toolchain/encoder の実 version と capability は generation input に
  記録する。encode 前は project profile が要求する codec/feature と ffmpeg 8.x を
  fail fast で検査する。将来 distribution digest を固定した場合だけ exact build
  一致を要求し、未定義の patch version を推測して阻断しない。
- upstream の hidden retry は無効にする。retry が必要なら新しい index/seed を持つ
  独立 take とする。
- stochastic take を実装していない adapter は `N > 1` を preflight で拒否する。
  同じ固定出力を N 個複製しない。

初版の recipe は `seed-only-v1` とする。temperature 等を変える recipe は
adapter ごとに supported field、値域、seed との組み合わせ、hash 収録をテストして
から追加する。全 model に共通の temperature default は設けない。

### 4.2 ローカル path

run 単位の root を次とする。

```text
artifacts/takes/<run-id>/
  ledger.json
  audio/<model>/<scenario>/<line>/<variant>/
    take-0001.wav
    take-0001.opus
    take-0001.json
```

`run-id` は実行を区別し、`generation_input_sha256` は生成要求を、
`take_id` は生成要求と最終音声 byte の組を区別する。
sidecar には `run_id`、`take_id`、input SHA、WAV/Opus SHA、adapter metadata、
処理時間を記録する。local path や run-id は generation identity に含めない。

## 5. Run ledger

`artifacts/takes/<run-id>/ledger.json` format v1 を、全 attempt の唯一の操作履歴とする。
git には入れない。最低限、次を持つ。

```json
{
  "format_version": 1,
  "run_id": "2026-07-29T120000Z-qwen-n3",
  "created_at": "2026-07-29T12:00:00Z",
  "source": {
    "scenario_sha256": "<64 hex>",
    "model": "qwen3-tts-12hz-1.7b",
    "takes": 3,
    "seed_base": 42,
    "recipe_version": "seed-only-v1",
    "groups": [
      {
        "model": "qwen3-tts-12hz-1.7b",
        "scenario": "chinatown-street",
        "line": "shokudo-oyaji-002",
        "variant": "dry"
      }
    ]
  },
  "attempts": [
    {
      "model": "qwen3-tts-12hz-1.7b",
      "scenario": "chinatown-street",
      "line": "shokudo-oyaji-002",
      "variant": "dry",
      "take_index": 1,
      "take_id": "<64 hex>",
      "generation_input_sha256": "<64 hex>",
      "generation": {
        "status": "succeeded",
        "seed": 123456789,
        "sampling": {},
        "rtf": 5.9
      },
      "audio": {
        "wav_path": "audio/.../take-0001.wav",
        "wav_sha256": "<64 hex>",
        "opus_path": "audio/.../take-0001.opus",
        "opus_sha256": "<64 hex>"
      },
      "gates": {
        "mechanical": "pass",
        "content": "review_required"
      },
      "features": {
        "status": "unscored"
      },
      "status": "eligible"
    }
  ]
}
```

実装時は exact-key、finite number、path containment、SHA、state transition を schema
または同等の validator で検証する。ledger は各 attempt 後に原子的に checkpoint
できるが、manifest finalize は選択範囲の全 attempt が terminal になるまで行わない。

### 5.1 attempt state machine

ledger の `status` は次の exact enum とする。

| status | terminal | 遷移元 | 意味 |
|---|---|---|---|
| `planned` | no | 初期値 | preflight 済み、未生成 |
| `generated` | no | `planned` | 音声生成済み、gate 未完了 |
| `blocked` | no | `generated` | QC/runtime error。修復後に同じ音声を再評価可能 |
| `generation_failed` | yes | `planned` | この run の attempt は生成失敗 |
| `hard_rejected` | yes | `generated` / `blocked` | Gate 1/2 により除外 |
| `eligible` | yes | `generated` / `blocked` | Gate 1/2 で hard failure なし |

`blocked` は停止状態だが terminal ではない。再実行時は audio SHA と
generation input SHA が ledger から変わっていないことを確認してから gate を
再評価する。`generation_failed` を再生成したい場合は、既存 attempt を書き換えず
新しい run を作る。manifest finalize は選択した group の全 attempt が
`generation_failed / hard_rejected / eligible` のいずれかであり、`blocked` を
含まない場合だけ許す。

ここで「選択した group」とは、run 作成時に CLI の model/scenario/line filter から
列挙して `ledger.source.groups` に固定した group 集合である。後から scenario や
manifest の内容が変わっても暗黙に範囲を増減しない。

## 6. Manifest v4

`data/manifest.json` は v4 へ一度に切り替える。pipeline と site は v4 だけを読み、
v3 dual reader、欠落 take の `take=1` 補完、`variant` への take 番号埋め込みを
実装しない。既存 381 clip には新契約が要求する完全な take provenance がないため、
値を推定して変換しない。新 harness で N=1 を再生成・gate・確認してから、
同一 cutover PR で v4 data へ置き換える。変換コードを runtime に残さない。

### 6.1 構造

```json
{
  "format_version": 4,
  "generated_at": "2026-07-29T12:34:56Z",
  "candidate_set_sha256": "<64 hex>",
  "models": [],
  "candidates": [
    {
      "model": "qwen3-tts-12hz-1.7b",
      "scenario": "chinatown-street",
      "line": "shokudo-oyaji-002",
      "variant": "dry",
      "take_index": 1,
      "take_id": "<64 hex>",
      "path": "audio/takes/qwen3-tts-12hz-1.7b/chinatown-street/shokudo-oyaji-002/dry/take-0001-<audio sha256>.opus",
      "duration_sec": 2.1,
      "sha256": "<64 hex>",
      "generation_input_sha256": "<64 hex>",
      "gen_params": {
        "seed": 123456789,
        "recipe_version": "seed-only-v1"
      },
      "rtf": 5.9,
      "loudness": {
        "source": "encoded_opus",
        "i_lufs": -18.1,
        "tp_dbtp": -1.2,
        "shortfall": false
      },
      "gate": {
        "mechanical": "pass",
        "content": "review_required",
        "policy_version": "take-gates-v2"
      }
    }
  ],
  "curations": [
    {
      "model": "qwen3-tts-12hz-1.7b",
      "scenario": "chinatown-street",
      "line": "shokudo-oyaji-002",
      "variant": "dry",
      "decision": "selected",
      "take_id": "<64 hex>",
      "curation_sha256": "<64 hex>"
    }
  ],
  "failures": [
    {
      "model": "qwen3-tts-12hz-1.7b",
      "scenario": "chinatown-street",
      "line": "missing-line",
      "variant": "dry",
      "reason": "no_eligible_take"
    }
  ]
}
```

### 6.2 不変条件

- candidate の論理 group key は `(model, scenario, line, variant)`。
- `candidate_set_sha256` は run-local `candidate-set.json` の完全 SHA-256 と一致し、
  `candidate-set.sha256` marker と browser/CLI の両方で照合する。
- attempt key は group key と `take_index` の 5-tuple。
- `take_id` は
  `(generation_input_sha256, final_opus_sha256)` の canonical hash であり、
  manifest 全体で一意な完全 SHA-256 とする。省略形を identity に使わない。
- 同一 `generation_input_sha256` の再生成で audio SHA が異なる場合は異なる
  `take_id` になる。1 run の同一 attempt slot へ両方を入れず、後の実行は別 run
  として保持する。
- 同じ group の `take_index` は重複しない。
- candidate は `mechanical=pass` かつ
  `content in {pass, review_required}` だけを許す。
- `curations` は group ごとに `decision=selected|skipped` のいずれかを持つ。
  selected shape は同一 group の既存 candidate を `take_id` で参照し、skipped shape
  は `take_id` を持たない。
- curation entry の `curation_sha256` は、判断元である
  `data/curation/<curation_sha256>.json` の完全 SHA-256 と一致する。
- 1 group の curation entry は最大 1 件。selected にされた
  `review_required` candidate は、curation 時の「内容正しい」確認を必須とする。
- `failures` は全 attempt が terminal で candidate が 0 件の group だけを持つ。
  attempt ごとの失敗理由は ledger にのみ残す。
- path は field から再構成した規定形式と完全一致し、末尾の audio SHA は
  `sha256` と一致する。
- unknown key、non-finite number、orphan selected take、candidate/failure 競合を
  拒否する。

初版は各 candidate に完全な `gen_params` を保持する。bundle size を測る前から
recipe table や継承規則を設けない。N=3 pilot で manifest がサイト build の実害に
なると判明した場合だけ、別 Issue で exact recipe reference を検討する。

### 6.3 複数 run の finalize

1 group の active candidate set は常に 1 run 由来とし、複数 run の
`take_index=1..N` を自動 merge しない。take を追加したい場合も、新しい N と recipe
で group 全体を新しい run として作る。

- manifest に group がなければ、その run の eligible candidate set または
  `no_eligible_take` を追加できる。
- manifest に同じ group があれば、通常の finalize は衝突として停止する。
- unselected group の置換には `--replace-group` を明示する。
- curation 済み group の置換には `--replace-group --drop-curation` の両方を
  明示する。candidate set と既存 curation entry は同じ atomic write で
  置換・削除する。
- 置換前の candidate と R2 object は自動削除しない。manifest から orphan になった
  object の清掃は別操作とする。
- 新 run が `no_eligible_take` の場合も同じ置換規則を適用し、既存の良い group を
  暗黙に failure へ降格しない。

### 6.4 R2 key

candidate object は immutable key を使う。

```text
audio/takes/<model>/<scenario>/<line>/<variant>/take-<index>-<full-audio-sha256>.opus
```

同じ index を別 recipe で再生成しても既存 object を上書きしない。publish は
全 candidate の local path、SHA、size、content type、key uniqueness を network call
前に preflight し、manifest の candidate だけを upload する。selected entry は既存
candidate object を参照し、旧 canonical key へ copy しない。

R2 の orphan cleanup はこの設計の実装 ticket に含めない。必要になった場合は、
対象 manifest digest と object 一覧を確認する別の明示操作として設計する。

## 7. Gate policy v2

Gate は hard reject、review、report-only を schema 上も実行上も分離する。
TTS と ASR/scorer は 12GB VRAM に同時常駐させない。

### 7.1 Gate 1: 機械品質

初版で hard reject にするのは、既存 pipeline が確定的に検証できる項目だけである。

| 項目 | v2 判定 |
|---|---|
| decode failure、空音声、NaN / Inf | hard reject |
| adapter が宣言した native format 違反 | hard reject |
| final Opus が 48kHz mono でない | hard reject |
| WAV / Opus / sidecar / ledger の SHA・provenance 不一致 | `blocked` |
| final Opus integrated loudness が `-18 ±1.5 LUFS` 外 | hard reject |
| final Opus true peak が `-0.9 dBTP` 超 | hard reject |
| active speech が 0 | hard reject |
| duration、leading/trailing silence、pause | report-only |

`shortfall` は既存どおり target `-18 LUFS` から `±0.2 LU` 超の表示であり、
hard reject は `±1.5 LUFS` である。pre-encode limiter target と final Opus gate を
混同しない。

「異常な尺」「尻切れ」「長い無音」は style に依存する。whisper、shout、
酔態、笑いを同じ固定値で落とす校正データがないため、v2 では duration/silence
feature として記録する。active speech 0 のみ hard reject とする。閾値を hard gate
へ上げる場合は、model/emotion を含む日本語 gold set の false reject を先に測る。

### 7.2 Gate 2: 内容一致

既存 `gaya qc` と Kana Whisper の固定 revision を利用する。

| 条件 | v2 判定 |
|---|---|
| 明示 `line.reading` と exact kana 一致 | `pass` |
| 明示 `line.reading` と不一致 | `review_required`、reason は `explicit_reading_mismatch` |
| reading が G2P 推定のみ、または多音語を含む | `review_required` |
| ASR が空、解析不能、入力 SHA が実行中に変化 | `blocked` |
| 未校正の小さな Kana-CER 差 | ranking に使わない |
| 欠落、反復、early stop の新 detector | 日本語 gold set 校正までは report-only |

#103 の N=3 pilot では content-correct false reject 77/159、adoptable false reject
49/97、hard reject が失った人評 winner 28/50 となった。したがって v2 では明示
reading mismatch を hard reject にせず、ASR transcript、Kana-CER、
`reading_mismatch`、review reason を report に保持したまま人間確認へ送る。
manifest/candidate-set には ASR evidence を複製せず、`review_required` と policy
version だけを固定する。

ASR Best-of-N の一次研究は silence、early stop、repetition、wrong content の
災害的失敗を N=2〜4 で減らせることを示す一方、検証は英語中心である。また
Whisper / wav2vec2 / HuBERT family により候補順位が反転する報告がある。そのため、
単一 Kana Whisper の差を take ranking に使わず、人間の `content_correct` 判定も
置き換えない。第 2 ASR を常駐させることもしない。

### 7.3 Gate 3: 表現 signal

初版は総合 score と自動順位を作らず、全 candidate を `unscored` とする。

| signal | 初期用途 |
|---|---|
| mora/s、pause、voiced ratio、F0、energy | take ごとの report |
| duration / pause / F0 / energy の群内分布 | group audit |
| DS-WED | 同一 text・speaker の take 群 diversity のみ |
| speaker embedding | clone model の soft outlier。未校正では非表示 |
| emotion2vec / EECS | 無効。hard gate / ranking ともに使用しない |
| early-500ms / full SER consistency | 仮説ログのみ |

DS-WED の人評相関 `0.77` は英語の同 text・speaker 5 take に対する
「韻律差の大きさ」であり、単一 take の正しさや品質ではない。emotion2vec の
zero-shot cosine は linguistic/speaker confound を受け、日本語 RPG 短文と
project emotion label に対する校正もない。さらに emotion2vec と DS-WED の
公式実装・weight には明確な license chain が確認できない。初版依存へ入れない。

将来 soft ranking を有効にする条件は次のすべてとする。

- 24 行以上の固定日本語 set を blind 人評する。
- `内容正しい / 意図一致 / 役として自然 / 採用可否` を記録する。
- model、emotion、character を跨ぐ leave-one-line-out で top-k hit が random を
  安定して上回る。
- exact model/code revision と license を確認する。
- `scoring_policy_version`、feature、weight を report に残す。

条件を満たさない指標は report-only のままにする。

## 8. CLI と failure semantics

実装後の責務を次のように分ける。

```console
uv run --project pipeline --locked gaya gen \
  --model qwen3-tts-12hz-1.7b \
  --scenario chinatown-street \
  --takes 3 \
  --seed-base 42

uv run --project pipeline --locked --extra qc gaya qc \
  --run-id 2026-07-29T120000Z-qwen-n3

uv run --project pipeline --locked gaya takes finalize \
  --run-id 2026-07-29T120000Z-qwen-n3

uv run --project pipeline --locked gaya publish

uv run --project pipeline --locked gaya curate apply \
  --run-id 2026-07-29T120000Z-qwen-n3 \
  --input artifacts/curation/curation.json
```

コマンド名の最終決定は実装 ticket で行うが、phase boundary は変えない。
`gaya gen` から QC runtime を直接 load せず、`gaya qc` は run ledger を入力にできる
よう拡張する。公開 manifest を候補 inventory として先に作らない。

### 失敗規則

- schema、scenario、model revision、adapter take support、seed recipe、output collision は
  model load 前に全件 preflight する。
- 1 take の generation failure は ledger に記録して残りを続け、最終 exit code は
  非 0 とする。
- hard reject は正常な品質判定として残りを続ける。
- `blocked` は低品質扱いにせず、run finalize を非 0 で止める。
- 選択範囲に 1 件でも非 terminal attempt があれば manifest を更新しない。
- 全 N take が terminal で eligible 0 件なら manifest に `no_eligible_take` を書く。
- manifest finalize は原子的に行い、candidate と failure の部分更新を残さない。
- R2 publish は全 candidate preflight 後に開始する。途中失敗は同じ manifest で
  idempotent に再実行できるが、Pages/manifest の公開は upload 完了後に行う。
- 欠けた reading、score、candidate を別値へ補完しない。最良 reject の救済もしない。

## 9. サイト策展と export

通常比較と take 策展を別 mode にする。

### 策展単位

- group は `(model, scenario, line, variant)`。
- 同一 group の eligible candidate だけを A/B/C... として比較する。
- take index、seed、model revision、自動 feature/score を blind 中は隠す。
- label と表示順は candidate set SHA と `take_id` から決定的に作る。
- 1 candidate ずつ再生し、group ごとに最大 1 件を選ぶ。全件 skip も許す。
- `review_required` を選ぶときは「内容正しい」の明示確認を要求する。
- 各 candidate の rubric は `内容正しい / 意図一致 / 役として自然 / 採用可否` を
  最低限持つ。group の selected/skipped だけを保存して rubric を捨てない。

再生 manager と安全な相対 URL 解決は既存実装を再利用できる。現在の model A/B
vote storage に take selection を混ぜず、別の versioned storage とする。

### localStorage と export

`candidate_set_sha256` は
`(format_version, scenario_sha256, lines, models, candidates, failures)` を
Python canonical JSON 化した `candidate-set.json` bytes の SHA-256 とする。
`lines` は candidate/failure が参照する全 `(scenario, line)` を一度ずつ持ち、
生成時の `scenario_title / text / delivery` を固定する。`generated_at` と既存
`curations` は含めない。draft と blind label はこの digest に束縛し、candidate set
または表示根拠が変わった場合だけ stale とする。

QC は canonical bytes、改行なし lowercase hex の `candidate-set.sha256`、同じ
digest を持つ `manifest-v4.json` の順で確定し、manifest を唯一の ready marker
とする。browser は raw bytes を SHA-256 して marker と manifest の両方へ照合する。
manifest には float が含まれ、Python と JavaScript の number serialization は
一致しない場合があるため、browser が候補集合を独自に再 canonicalize しない。
curation 済み run の QC 再実行は三者を変更する前に fail-fast し、新しい run を使う。

`curation.json` format v1 は次を持つ。

```json
{
  "format_version": 1,
  "rubric_version": "take-curation-v1",
  "candidate_set_sha256": "<64 hex>",
  "groups": [
    {
      "model": "qwen3-tts-12hz-1.7b",
      "scenario": "chinatown-street",
      "line": "shokudo-oyaji-002",
      "variant": "dry",
      "candidates": [
        {
          "take_id": "<64 hex>",
          "path": "audio/takes/...opus",
          "audio_sha256": "<64 hex>",
          "rubric": {
            "content_correct": true,
            "intent_match": 4,
            "character_naturalness": 5,
            "adoptable": true
          }
        }
      ],
      "decision": {
        "type": "selected",
        "take_id": "<64 hex>"
      }
    }
  ]
}
```

`intent_match` と `character_naturalness` は 1〜5 の整数とする。export 対象 group
では全 candidate の rubric を必須とする。`decision.type=skipped` は
`take_id` を持たず、「評価済みだが採用品なし」を表す。これにより未策展 group と
skip を区別し、pilot の raw decision を後から再解析できる。

browser は directory file input で利用者が明示選択した同一 run root の
`manifest-v4.json`、`candidate-set.json`、`candidate-set.sha256`、物理 Opus だけを
読む。台詞と演技指示は sidecar の生成時 `lines` snapshot だけを表示する。候補の
logical path から `audio/<model>/<scenario>/<line>/<variant>/take-<index>.opus` を導出し、
三者の candidate set digest と各音声の SHA-256 を検証する。Vite middleware、
`/@fs/`、filesystem write は使わない。key order と group/candidate order を固定した
JSON を download するだけで、repository や manifest を直接変更しない。時刻を
artifact 本体へ入れず、同じ判断は同じ bytes になるようにする。

`gaya curate apply --run-id <id> --input <curation.json>` は明示された run root に対し、
terminal ledger/current scenario source、三者の candidate set SHA、各 eligible take の
sidecar/WAV/Opus provenance、eligible candidate と failure group の完全な inventory、
rubric range、selected take の `content_correct/adoptable`、1 group 1 decision を全件検証する。
成功時は canonical artifact を
`data/curation/<curation_sha256>.json` へ immutable に保存し、その
`curation_sha256` を参照する selected/skipped projection を manifest の
`curations` へ原子的に更新する。tracked decision artifact が人評の真値であり、
manifest は通常表示のための小さな projection である。

分割作業の累積 export は、既存 projection が参照する immutable artifact の同一 group
と identity、全 candidate、rubric、decision が完全一致するときだけ再掲を許す。
参照中の全 artifact に重複して現れる group も、その group の projection が指す
権威 artifact と完全一致しなければならず、projection のない付随 group は拒否する。
一致する既存 group は旧 projection と旧 artifact SHA を維持し、新規 group だけを
今回の artifact SHA へ追加する。既存内容の差し替え、無参照 artifact、暗黙 merge は
拒否する。

通常比較画面は `curations(decision=selected) -> candidates` を投影し、各 group
1 clip の既存前提を維持する。skipped は策展済み未採用、entry なしは未策展として
表示する。candidate[0] へ fallback しない。

## 10. コスト

manifest と現在の artifact metadata から算出した線形の下限見積りである。
生成時間は `sum(duration_sec × rtf) × N`、R2 は現在の Opus 実容量を N 倍した。
model load、QC、reject 率、人工試聴は含まない。

| model | 現在 clip 数 | 1 sweep 生成時間 | N=5 | N=10 | N=5 Opus | N=10 Opus |
|---|---:|---:|---:|---:|---:|---:|
| Chatterbox | 12 | 3.73 分 | 0.31 h | 0.62 h | 1.40 MiB | 2.79 MiB |
| CosyVoice | 12 | 3.54 分 | 0.29 h | 0.59 h | 2.98 MiB | 5.96 MiB |
| GPT-SoVITS | 12 | 0.69 分 | 0.06 h | 0.12 h | 1.64 MiB | 3.28 MiB |
| Qwen | 160 | 44.75 分 | 3.73 h | 7.46 h | 18.40 MiB | 36.80 MiB |
| Supertonic | 12 | 0.19 分 | 0.02 h | 0.03 h | 1.21 MiB | 2.43 MiB |
| VoxCPM | 12 | 2.81 分 | 0.23 h | 0.47 h | 1.79 MiB | 3.59 MiB |
| dummy | 161 | 0.10 分 | 0.01 h | 0.02 h | 6.98 MiB | 13.96 MiB |
| **現 manifest 合計** | **381** | **55.81 分** | **4.65 h** | **9.30 h** | **34.40 MiB** | **68.81 MiB** |

Qwen 以外の実モデルは 12 行 canary だけであり、この表は全 corpus の予算ではない。
Qwen の全 sweep wall time 実測は約 46.69 分で、adapter generation 合計より約
1.94 分長い。N=5/10 の現実の wall time は load/prepare、QC RTF、gate 通過率を
別途加える必要がある。

pilot では次を model 別に記録する。

- generation failure / hard reject / review_required / eligible rate。
- 1 line あたり人間へ残る candidate 数。
- generation、QC、site build、人工試聴の wall time。
- WAV + Opus + sidecar の local bytes、eligible Opus の R2 bytes。
- N=3 から N=5 へ増やしたときの採用 take 改善率。

## 11. 実装 ticket 分割案

大きな schema cutover を 1 PR に詰めず、次の依存順で起票する。前 ticket の契約を
後 ticket が利用する。途中の main は既存 v3 公開 data を read-only で維持するが、
Ticket G の cutover 後に旧 reader、旧 generator、変換経路を残さない。

### Ticket A — Take foundation と v4 契約

範囲:

- manifest v4 exact contract/fixture、candidate / curation / logical failure。
- `TakeContext`、seed policy、`generation_input_sha256`、`take_id`、
  sidecar/ledger v1。
- 現行 single take の内部呼出しを explicit `TakeContext(index=1, ...)` へ移し、
  dummy と stochastic adapter 1 つで縦 slice を作る。
- public manifest/site の切替は Ticket G まで行わない。

受け入れ条件:

- v4 fixture は missing take、orphan selected take、duplicate identity を fail fast。
- 同一 generation input と同一 Opus byte から同一 take identity を得る。
- 同一 generation input でも異なる Opus byte は異なる take identity になる。
- rejected/blocked は candidate に構築できない。
- v4 contract fixture が exact field と identity rule を受理・拒否する。

### Ticket B — `gaya gen --takes N` と全 adapter recipe

範囲:

- `--takes` / `--seed-base`、全対象の事前 collision 検査。
- 全 stochastic adapter が明示 TakeContext を使用。
- seed-only-v1、hidden retry 無効化、実パラメータ監査。

受け入れ条件:

- N 個の path/sidecar/ledger entry が重複しない。
- seed/input hash を変えると take identity が変わる。
- unsupported N、無視された sampling、固定出力複製を開始前に拒否する。
- cache hit と `--force` の identity/provenance がテストで固定される。

この ticket から `gaya gen` は ledger だけを書き、公開 manifest v3 を更新しない。
既存公開サイトは cutover まで read-only の v3 data を使うが、旧単 take generation
branch は残さない。

`gaya gen` は run-id を自動生成する。通常実行では source、groups、N、seed-base、
recipe、全 generation input と artifact provenance が一致する完了済み run だけを
whole-run cache として返す。途中 run と `generation_failed` run は変更せず、新しい
run を作る。同一 input に異なる take identity の完了 run が複数ある場合は自動選択
しない。`--force` は cache を使わず、常に別 run へ一度ずつ生成する。

### Ticket C — Gate orchestration と v4 snapshot

範囲:

- `gaya qc --run-id`、Gate 1/2、Gate 3 report-only。
- TTS unload 後の QC、ledger join、atomic ledger -> local v4 snapshot。
- `no_eligible_take` と blocked failure semantics。

受け入れ条件:

- loudness/TP、invalid audio の fixture を hard reject し、explicit reading
  mismatch の fixture は `review_required` candidate に残す。
- review_required と blocked を pass/reject に変換しない。
- blocked または非 terminal run で snapshot を確定しない。
- reject audio が v4 snapshot に 1 件も現れない。

### Ticket D — ローカル take 策展と decision artifact

範囲:

- local v4 snapshot と local audio を読む take mode。
- blind order、candidate ごとの rubric、group の selected/skipped。
- candidate-set-bound storage と deterministic `curation.json` download。
- `gaya curate apply`、immutable `data/curation/<sha>.json`、
  local snapshot への curation projection。

受け入れ条件:

- seed/score を blind 中に表示しない。
- selected/skipped/未策展を区別し、全 candidate の rubric を artifact に残す。
- stale candidate set、orphan take、audio SHA mismatch、rubric 範囲違反を拒否する。
- review_required の選択に `content_correct=true` を要求する。
- browser から repository を直接変更しない。

### Ticket E — N=3 pilot と gate / soft signal 校正

範囲:

- 24 行固定 set に明示 reading を追加する。
- 複数 model/emotion/character の N=3 generation、gate、local curation。
- gate false reject、人評 top-k、rule 別 reject、時間、容量、試聴量の report。
- N=5 と scorer 導入の go/no-go。

受け入れ条件:

- blind rubric と raw decision artifact を保存する。
- ASR hard reject と人評を照合し、rule ごとの false reject を報告する。
- feature ごとの leave-one-line-out 結果を報告する。
- random を安定して上回らない scorer は production rank に入れない。
- N=5 へ進むか、N=3 を維持するか、model 別に根拠を残す。

### Ticket F — 公開 baseline の v4 data 準備

範囲:

- current published group を新 harness で N=1 再生成し、Gate 1/2 を通す。
- 既存公開 clip と再生成 clip の差分を local curation UI で確認する。
- 全 group の decision artifact と、selected/skipped projection を含む
  release candidate v4 snapshot を作る。

受け入れ条件:

- 既存 381 clip に未知の seed/input hash を捏造しない。
- 再生成 byte が旧公開 clip と異なる group は rubric なしに selected にしない。
- candidate 0 件、skipped、selected の全 group 数が監査できる。
- release candidate snapshot と全 decision artifact の SHA を固定する。

この ticket が完了するまで main の v3 公開 data はそのまま維持する。従来公開されて
いた事実だけを「人間選択済み」の証拠にはせず、再生成した N=1 audio を明示的に
確認する。

実装では`baseline plan`でraw v3 SHAと381 groupを固定し、`gen --selection`で
7 modelのexact N=1 runを作る。`baseline assemble`は旧公開audioをcandidateへ
変換せずreferenceとして分離し、全bundle fileをcanonical inventoryで閉包する。
`/curate/baseline`のdecisionはcandidate set SHAとreference SHAの両方に拘束する。
`baseline finalize`はinventoryと全source evidenceを再検証し、
`381 = candidate_zero + selected + skipped`、`uncurated=0`を満たすsnapshotだけを
確定する。exact schemaと運用手順は
[baseline v4 protocol](research/baseline-v4/protocol.md)を正とする。

### Ticket G — manifest/site/R2 の原子的 v4 cutover

範囲:

- Ticket F の固定 snapshot だけを入力に、immutable R2 candidate key を upload。
- candidate allow-list と全件 network preflight。
- pipeline/site の v3 reader を削除し、`data/manifest.json` と通常比較を v4 へ
  同じ PR で切り替える。
- 通常比較は `curations(decision=selected)` だけを表示する。

受け入れ条件:

- rejected/blocked/local-only path は network call 前に拒否する。
- 同 SHA は skip、異なる immutable key は overwrite しない。
- 部分 upload 後の再実行で整合する。
- cutover 後は v3、missing take、candidate[0] fallback を拒否する。
- skipped と未策展を通常画面で区別する。
- R2 完了前に Pages/manifest を公開しない。

v3 -> v4 の runtime converter や dual reader は作らず、Ticket G の merge を唯一の
切替点とする。

## 12. 拒否した案

- `variant="dry-take-3"`: variant と take の意味が混ざり、既存 query を壊れたまま
  延命するため採用しない。
- v3 と v4 の dual reader / missing take の default: 不完全 migration を隠すため
  採用しない。
- 公開 manifest に rejected を入れて site/publish で filter: consumer ごとの
  filter 漏れが起こるため採用しない。
- generation 中に ASR/scorer を load: 12GB VRAM の TTS と競合し、phase failure を
  分離できないため採用しない。
- ASR の小差で best take を選ぶ: evaluator family bias があり、内容 hard failure
  以外の順位根拠にならない。
- emotion2vec/EECS を演技 score とする: 日本語校正、label 対応、license chain、
  linguistic/speaker confound の問題を解けていない。
- DS-WED で単一 take を reject: group diversity と個別品質を混同する。
- N=10 を default にする: 現在の Qwen だけで約 7.5 時間の generation 下限となり、
  通過率や改善率が未計測である。
- 生成後に rejected を自動削除: 誤判定監査と policy 再実行ができなくなる。
- 初版で recipe table を正規化する: 実測前の複雑化であり、完全 `gen_params` の方が
  audit と実装が単純である。

## 13. 実装開始条件と未解決事項

実装開始時に確定させるもの:

- v4 JSON Schema/validator の exact field と integer range。
- seed derivation の byte-level canonicalization と adapter seed range。
- existing v3 data の一括 v4 更新手順。
- `active speech = 0` の detector と version。
- サイト build で策展用 candidate audio を解決する local/R2 mode。

pilot まで保留するもの:

- duration / leading/trailing silence の hard threshold。
- omission / repetition / early-stop detector の日本語閾値。
- temperature/top-p recipe。
- speaker similarity、emotion2vec、DS-WED、総合 score。
- N=5/10 の model 別採用判断。

未解決値を default、fallback、最良 reject で埋めない。必要な reading、model、
recipe、QC runtime、manifest version が不足した場合は該当 phase を明示的に止める。

## 14. 根拠

- [Issue #76](https://github.com/Hitsuki-Ban/gaya-bench/issues/76)
- [Gaya Bench 表現力調査の統合レポート](research/expressiveness/final.md)
- [Gaya Bench 読み・韻律 QC](reading-qc.md)
- [ITU-R BS.1770-5: Algorithms to measure audio programme loudness and true-peak audio level](https://www.itu.int/rec/R-REC-BS.1770/en)
- [Kana Whisper model card](https://huggingface.co/sbintuitions/kana-whisper)
- [Sarashina2.2-TTS: Japanese TTS with Kana-based ASR evaluation](https://arxiv.org/html/2606.25369v1)
- [ASR Self-Verification for Codec-based TTS](https://arxiv.org/html/2606.18323v1)
- [ASR Family Alignment Bias in TTS Verification](https://arxiv.org/html/2607.08256v1)
- [emotion2vec](https://aclanthology.org/2024.findings-acl.931.pdf)
- [The False Resonance: Limitations of Speech Emotion Embeddings](https://arxiv.org/abs/2604.26347)
- [DS-WED](https://arxiv.org/html/2509.19928v3)
