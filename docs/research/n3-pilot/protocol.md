# N3 pilot calibration protocol v1

## 固定デザイン

自動品質 gate の誤棄却と、未校正の韻律 feature が人評選抜に使える可能性を
独立した blind pilot で探索する。

- scenario: `battlefield-camp`、`dungeon-entrance` の全 24 line
- model: `qwen3-tts-12hz-1.7b`、`irodori-tts-600m-v3-voicedesign`、`voxcpm2`
- 各 model × line で N=3、seed base `103`、variant `dry`

結果は **24-line exploratory** であり、production scorer は
**no-go without independent confirmation** とする。take 4/5 の paired data が
存在しないため各 model は N3 を維持し、この pilot から N5 の判断を行わない。

## Bundle

```console
uv run --project pipeline --locked gaya pilot build \
  --run-id <qwen-battlefield-run> \
  --run-id <qwen-dungeon-run> \
  --run-id <irodori-battlefield-run> \
  --run-id <irodori-dungeon-run> \
  --run-id <voxcpm2-battlefield-run> \
  --run-id <voxcpm2-dungeon-run> \
  --output <new-bundle-directory>
```

builder は 3 model × 2 scenario × 12 line × 3 take、`takes=3`、
`seed_base=103`、ledger / QC report / v4 snapshot / sidecar / WAV / Opus の
provenance と SHA-256 を検証する。`blocked`、`generation_failed`、`planned`、
`generated` が一件でもあれば作らない。

bundle は canonical bytes の `pilot-set.json` と
`audio/<opaque-candidate-id>.opus` だけで構成する。candidate と group の提示順は
opaque ID 順で、model block や take index を提示順から推測できない。
JSON 内の model / scenario / line / take、status、gate、feature は監査用であり、
blind UI は表示しない。`generated_at` は 6 QC report の値の最大値なので、
同じ run からの再構築で bytes SHA は変わらない。

## Pilot set v1 exact contract

全 object は追加 field を拒否する。

```ts
type FeatureName =
  | "duration_sec"
  | "mora_per_second"
  | "pause_sec"
  | "voiced_ratio"
  | "f0_semitone_std"
  | "energy_median_dbfs"

type PilotSetV1 = {
  format_version: 1
  protocol: "n3-pilot-v1"
  generated_at: string
  design: {
    models: [
      "qwen3-tts-12hz-1.7b",
      "irodori-tts-600m-v3-voicedesign",
      "voxcpm2",
    ]
    scenarios: ["battlefield-camp", "dungeon-entrance"]
    line_count: 24
    takes_per_group: 3
    seed_base: 103
    feature_specs: Array<{ name: FeatureName; source: string }>
  }
  lines: Array<{
    scenario: string
    line: string
    scenario_title: string
    text: string
    reading: string
    delivery: string
  }>
  groups: Array<{
    group_id: string
    model: string
    scenario: string
    line: string
    variant: "dry"
    candidate_ids: [string, string, string]
  }>
  candidates: Array<{
    candidate_id: string
    model: string
    scenario: string
    line: string
    variant: "dry"
    take_index: 1 | 2 | 3
    take_id: string
    status: "eligible" | "hard_rejected"
    gates: {
      mechanical: "pass" | "reject"
      content: "pass" | "review_required" | "reject" | "not_run"
      policy_version: string
      primary_reject_rule:
        | "mechanical_audio"
        | "active_speech_nonpositive"
        | "explicit_reading_mismatch"
        | null
      reject_reason: string | null
    }
    features: Record<FeatureName, number | null>
    audio: { path: string; sha256: string }
  }>
}
```

mechanical reason が `active_speech_sec が 0 または不正です。` と完全一致するとき
だけ `active_speech_nonpositive` とする。それ以外は細分を推測せず
`mechanical_audio` とし、reason をそのまま保持する。content reject は
`reading_mismatch=true` を検証して `explicit_reading_mismatch` とする。terminal
再検証 report が元の reason を保持していない場合、`reject_reason` は null とする。

## Decision v1 exact contract

`pilot_set_sha256` は解析対象 `pilot-set.json` の raw bytes SHA-256 である。

```ts
type DecisionV1 = {
  format_version: 1
  rubric_version: "n3-pilot-human-v1"
  pilot_set_sha256: string
  groups: Array<{
    group_id: string
    candidates: Array<{
      candidate_id: string
      rubric: {
        content_correct: boolean
        intent_match: 1 | 2 | 3 | 4 | 5
        character_naturalness: 1 | 2 | 3 | 4 | 5
        adoptable: boolean
      }
    }>
    decision:
      | { type: "selected"; candidate_id: string }
      | { type: "skipped" }
  }>
}
```

各 rubric 軸と decision は独立して解釈する。

- `content_correct`: 台詞内容だけでなく、厳密な日本語の音調・アクセントまで含む。
  語の読みが理論上正しくても、音調・アクセントが不正確なら `false` とする。
- `intent_match`: 演技指示と感情への一致度を 1..5 で記録する。
- `character_naturalness`: 役としての自然さを 1..5 で記録する。
- `adoptable`: 感情、役としての自然さ、音質などを含む総合的な利用可能性を記録する。
  厳密な音調・アクセントの誤りがあっても `true` になり得るため、
  `adoptable=true` は `content_correct=true` を含意しない。
- `selected`: A/B/C 内の相対的な最良候補を記録する。絶対的な合格や
  `content_correct=true`、`adoptable=true` を含意しない。全候補から相対的な
  winner を選べない場合だけ `skipped` とする。

この独立性は #103 の owner calibration で確定した。raw decision には
`content_correct=false && adoptable=true` と、非 adoptable の selected winner が
意図的に含まれるため、validator は cross-field 制約を追加しない。

## 事前登録解析

```console
uv run --project pipeline --locked gaya pilot analyze \
  --bundle <bundle-directory> \
  --decision <decision-v1.json> \
  --output <new-report-directory>
```

JSON/Markdown に content-correct と adoptable の raw 2×2、二種の FRR、
bad-content recall、reject precision、rule 別と model × rule 別の reject、
human selected/adoptable/skip、hard reject された selected winner (`lost_winner`)、
eligible-only の単一 feature LOLO Hit@1/2 と group-size random baseline を保存する。
rule FRR の分母は全 candidate の content-correct / adoptable 件数、
model × rule FRR の分母は当該 model の content-correct / adoptable 件数とする。
rule reject 内の比率は別名 `share_of_rule_rejects` で報告する。

feature は上記六項目だけとし、ASR は rank に使用しない。各 fold で held-out line
以外の group を使い ascending / descending を選ぶ。training の Hit@1、Hit@2、
MRR の順に辞書式比較し、完全同率は ascending とする。skip、selected winner が
hard reject の group、eligible candidate の当該 feature に null がある group は
その feature から除外し、除外数を報告する。

## Evidence と限界

- [ITU-T P.808](https://www.itu.int/rec/T-REC-P.808) は crowd listening test の
  material、実験設計、手順、screening、統計解析を扱う。本 pilot は single owner
  の校正であり、その listener 設計を満たす標準 MOS ではない。
- [Asaria et al., arXiv:2606.18323](https://arxiv.org/abs/2606.18323) は
  ASR round-trip による self-verification で catastrophic failure を大幅に減らす
  結果を示す。一方、この pilot が必要とする「良い音声を誤って落とす」
  false-positive floor は同論文の報告から確定できないため、人評 2×2 で別に測る。
- [Yu & Kang, arXiv:2607.08256](https://arxiv.org/abs/2607.08256) は
  verifier と evaluator の ASR family alignment で BoN ranking が反転し得ることを
  報告する。この交絡を避けるため、ASR は本 pilot の feature ranking に使わない。
- [scikit-learn LeaveOneGroupOut](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.LeaveOneGroupOut.html)
  の「特定 group 以外を training、当該 group を test」とする定義に合わせ、
  line を group として方向選択と評価を分離する。
