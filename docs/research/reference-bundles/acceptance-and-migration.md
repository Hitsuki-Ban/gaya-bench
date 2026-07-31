# Phase 2 受け入れ条件と移行

## 順序

```text
#177 役柄条件伝達
  → #174 完全 baseline release
    → #179 Phase 2 原子移行
      → #178 site 最終実装
```

Phase 1 の schema / `catalog-validate` は並行可能だが、Phase 2 は #177 と #174 の
production assignment / receipt が確定するまで開始しない。

## Phase 1 gate

- catalog root は `schema/`、`bundles/`、`assignments.yaml`,
  `synthetic-sources.yaml` だけを持つ。
- `schema/` は 5 schema file だけを持つ。
- `bundles/<id>.yaml`、assignment、synthetic policy は strict schema に通る。
- duplicate YAML key、未知 field、重複 ID、未知 bundle / policy を拒否する。
- transcript `text` と `utf8_sha256` が一致する。
- clip transcript は独立した `rights.redistribution` / `credit` / `evidence` を持つ。
- canonical clip は PCM16 / 48 kHz / mono、`general` は 10–20 秒、
  `short_clone` は厳密に 5.000 秒である。
- production assignment は `tts_reference_inference` と
  `commercial_generated_output` が `permitted` で、term が有効である。
- CLI は次の明示引数だけで公開 metadata を検証する。

```powershell
uv run --project pipeline gaya reference-bundles catalog-validate `
  --catalog F:\absolute\catalog `
  --as-of 2026-07-31
```

Phase 1 CLI は private audio、legacy runtime、adapter、`assets/voices` を読まない。

## Phase 2 fail-fast gate

### private root / asset

- `--reference-assets <absolute-path>` を必須にする。
- env、default、legacy root、download、alternate path を設けない。
- layout は `<root>/<bundle-id>/clips|derived|receipts` の一つだけ。
- symlink / junction / reparse point、path traversal、root 外解決を拒否する。
- `asset_sha256`、PCM encoding / sample rate / channels / `frame_count` を実 file と照合する。
- `publication.audio_access` と `storage.type` を一致させる。

### materialize / receipt

- 必須の `general` / `short_clone` と任意の `emotions` の明示 clip だけを入力にする。
- adapter 派生ごとに `derivative-receipt-v1` を生成し、source asset / transcript、
  operations、output asset、toolchain、input / output hash を記録する。
- receipt schema が存在することを「materializer 実装済み」と扱わない。
- 同一 output を上書きせず、不足 clip を別 clip で推測しない。

### assignment / site

- `assignments.yaml` の `subject_id` / `bundle_id` / `usage` を唯一の割当契約にする。
- scenario 明示 reference と production assignment の優先契約を #177 の最終仕様へ
  一括接続し、未割当を fail fast する。
- site は公開 YAML の deterministic projection だけを消費する。
- projection に private root、音声 byte、契約本文、法的氏名を含めない。
- transcript は `transcript.rights.redistribution: permitted` の場合だけ含める。

## Phase 2 テスト

1. 既存 5 音声の byte / SHA-256 を移行前後で一致させる。
2. 58 role の assignment と scenario explicit 5 件を照合する。
3. #177 の male 4 役が female / teen bundle に戻らない。
4. coverage が gender 51/7/0、age 21/37、gender+age exact 18、
   kind 46/12、all exact 16 と一致する。
5. missing root / clip、bad hash / PCM / frame count、symlink、unknown bundle、
   expired term、prohibited production permission を拒否する。
6. transcript hash / rights 欠落を拒否し、site projection の transcript inclusion を
   redistribution permission でテストする。
7. adapter ごとの hard duration / transcript condition と derivative receipt を検証する。
8. schema validate、pipeline tests、site `vp check` / `vp test` / `vp build` を通す。
9. repository 全体で旧 loader / path / schema 参照が 0 件である。

## 原子削除

Phase 2 の一つの PR で新 asset validator、materializer、assignment、site projection
を接続し、同じ PR で旧 `assets/voices/metadata.yaml` / schema、
`gaya_pipeline.voice_assets`、adapter 内 legacy loader、旧 CLI を削除する。
dual path、旧 ID / field / env / path alias、migration shim、silent fallback は作らない。

## run-scoped anchor

#174 の Qwen / Irodori anchor は model revision、scenario / character、role identity、
role epoch、seed、WAV SHA、人評 decision に束縛される。portable bundle の origin /
rights を持たないため catalog へ追加せず、別 run / model の素材として再利用しない。
