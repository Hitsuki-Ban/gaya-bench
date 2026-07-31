# 参照バンドル v1

最終確認日: 2026-07-31

Issue [#179](https://github.com/Hitsuki-Ban/gaya-bench/issues/179) で承認する
参照バンドルの公開 metadata 契約と、Phase 2 で導入する非公開 asset 経路を定義する。
1 persona = 1 bundle とし、assignment は `bundle_id` だけを参照する。

## 完全な公開 catalog root

validator が受理する完全な catalog の構造は次の一つだけである。集約 JSON や
bundle ごとの JSON metadata は設けない。

```text
assets/reference-bundles/
├── schema/
│   ├── assignments-v1.schema.json
│   ├── derivative-receipt-v1.schema.json
│   ├── recording-request-v1.schema.json
│   ├── reference-bundle-v1.schema.json
│   └── synthetic-sources-v1.schema.json
├── bundles/
│   └── <bundle-id>.yaml
├── assignments.yaml
└── synthetic-sources.yaml
```

Phase 1 の repository root `assets/reference-bundles/` に置くのは `schema/` の
5 file だけであり、まだ完全な catalog ではない。正例は test fixture 内で schema
を組み合わせて検証する。実 bundle、assignment、synthetic policy は Phase 2 の
原子移行で初めて配置するため、それまでは repository root 自体を
`catalog-validate` の成功対象として扱わない。空または仮の catalog data も置かない。

`bundles/<bundle-id>.yaml` は公開可能な metadata だけを持つ。
`assignments.yaml` は `subject_id`、`bundle_id`、`usage` を持ち、
`synthetic-sources.yaml` は合成参照として承認した生成経路を列挙する。

## `reference-bundle-v1` の実フィールド

### persona / origin

- `schema_version: reference-bundle-v1`
- `bundle_id`, `display_name`
- `persona.kind`, `locale`, `perceived_gender`, `perceived_age`,
  `voice_characteristics`
- `origin.type`: `public_corpus` / `commissioned_recording` / `synthetic`

origin の required fields は type ごとに異なる。

- `public_corpus`: `corpus_name`, `source_item_id`, `source_url`,
  `acquired_on`, `source_file_sha256`
- `commissioned_recording`: `provider_display_name`, `provider_url`,
  `contract_reference_id`, `recorded_on`, `source_file_sha256`
- `synthetic`: `synthetic_policy_id`, `generated_on`,
  `generation_input_sha256`, `generation_receipt_sha256`,
  `selection_receipt_sha256`

### rights / publication

`rights.permissions` は次の 4 項目を `permitted` / `prohibited` で個別に記録する。

- `tts_reference_inference`
- `training_or_finetuning`
- `commercial_generated_output`
- `audio_redistribution`

`rights.term` は `perpetual` または `fixed`。`rights.credit` は
`required` または `not_required`、`rights.evidence` は origin に対応する
`public_license` / `contract` / `model_terms` である。

`publication.metadata_visibility` は `public`。`publication.audio_access` は
`public` / `private` で、各 clip の `storage.type` と一致しなければならない。

### clips

`clips.general` と `clips.short_clone` は必須、`clips.emotions[]` は任意である。
各 clip は以下を持つ。

- `clip_id`
- `transcript.text`, `transcript.utf8_sha256`
- `transcript.rights.redistribution`, `credit`, `evidence`
- `asset_sha256`
- `pcm.encoding`, `sample_rate_hz`, `channels`, `frame_count`
- `storage.type`, `storage.object_key`

canonical clip はすべて PCM16 / 48 kHz / mono とする。`general` は 10–20 秒、
`short_clone` は厳密に 5.000 秒である。

台本文字と音声の権利は独立している。音声の `audio_redistribution` から
`transcript.rights.redistribution` を推定しない。

## Phase 1 の実装範囲

Phase 1 は docs-only ではない。上記 5 schema と、公開 metadata だけを検証する
隔離 CLI を含む。

```powershell
uv run --project pipeline gaya reference-bundles catalog-validate `
  --catalog F:\absolute\path\to\reference-bundles `
  --as-of 2026-07-31
```

`--catalog` と `--as-of` は必須。catalog root は絶対 path でなければならない。
validator は root entries、5 schema、YAML duplicate key、strict schema、bundle /
policy / subject の一意性、transcript UTF-8 SHA-256、origin と evidence の対応、
synthetic policy、production assignment の権利と期限を検証する。

この command は音声 file、private asset root、legacy `assets/voices`、
adapter runtime を読まない。asset hash の実 file 照合、PCM inspection、
materialization、production assignment の runtime 接続、site projection は
Phase 2 gate である。

## Phase 2 の非公開 asset root

Phase 2 CLI は private root を必須の絶対引数で受ける。

```text
--reference-assets <absolute-path>

<reference-assets>/
└── <bundle-id>/
    ├── clips/
    ├── derived/
    └── receipts/
```

環境変数、既定値、repository 内探索、legacy path、download、別 root fallback は
設けない。契約書、提供者の法的氏名、連絡先は別の契約管理系に置き、
private asset root や公開 YAML へ複製しない。

Phase 2 の `asset-validate` は `storage.object_key` をこの root の一意な file へ解決し、
`asset_sha256`、PCM、`frame_count` を照合する。`materialize` は adapter ごとの
派生 file と `derivative-receipt-v1` を `derived/` / `receipts/` に生成する。
これは承認後に実装・テストする gate であり、Phase 1 の実装済み機能ではない。

## drop-in から site まで

```text
drop bundles/<id>.yaml
  → catalog-validate                         (Phase 1 implemented)
  → asset-validate --reference-assets ...    (Phase 2 gate)
  → materialize --reference-assets ...       (Phase 2 gate)
  → assignments.yaml を production 接続      (Phase 2 gate)
  → 公開 metadata の deterministic projection (Phase 2 gate)
  → #178 site
```

site projection は公開 YAML だけから決定的に生成し、private root、音声 byte、
契約文書、法的氏名を含めない。transcript は
`transcript.rights.redistribution == permitted` の clip だけに含める。

## 原子移行

Phase 2 は #177 → #174 release の後に一つの PR で切り替える。新 schema /
asset validator / materializer / assignment / site projection を揃え、同じ PR で
旧 metadata、旧 loader、旧 CLI 経路を削除する。dual read、ID alias、path alias、
migration shim、silent fallback は作らない。詳細は
[受け入れ条件](acceptance-and-migration.md)を正とする。

## bundle ではないもの

#174/#177 の Qwen / Irodori selected anchor は run、model revision、role identity、
role epoch、人評 decision に束縛された生成証跡である。portable asset の rights /
origin を持たないため bundle へ昇格させない。VoxCPM2 の run 内 design reference
も自動的に bundle catalog へ登録しない。
