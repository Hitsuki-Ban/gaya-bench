# モデル追加手順 (増分オンボーディング)

#194 (Irodori-TTS v4-Small、9モデル目) の実地検証で確立した、公開済みbaselineへ
新モデルを1つ増分追加する標準手順。凍結済みの8モデルplan/anchor機構
(`completion_*` の凍結定数・公開監査証跡) には一切触れない。

## 前提

- 公開baselineは immutable (R2 objectは上書きしない。増分は新規objectの条件付きPUTのみ)
- 選抜は新既定契約: 機械選抜 (`auto-selected-v1`) + F0系機械スクリーニング +
  soft signal はUI品質注記バッジへ。Owner聴取は公開ゲートに含めない
- Director承認: ライセンス一次情報 (重み+生成出力の商用条件) と VRAM実測の合否判定

## 手順

### 1. adapter実装 (worktree)

- `pipeline/src/gaya_pipeline/adapters/<model>.py` を #177 契約準拠で実装:
  明示reference優先 / 役ごと単一anchor / realized conditioning receipt完備 /
  reading対応の明示 / 旧モデルとのcache非互換 (`generation_input_sha256` に model id+version)
- 登録点: adapter registry / anchor系gate (`generation.py`・`take_ledger.ANCHOR_MODELS`・cli) /
  `qc_report._validate_realized_anchor_receipt` / site `gaya-data-plugin.ts` のprovenance抽出 /
  site credits。**capabilities宣言 (voice_prompt/clone) だけで #178 方式バッジは自動編入される**
- 凍結モデルのadapter sourceは変更禁止 (公開監査SHAが壊れる)。共通化はsubclass側で行う
- 長時間run対策: take境界で `gc + cuda.synchronize + empty_cache` (WDDMでは
  reserved poolがhost commit chargeを直接消費するため必須)

### 2. 増分経路 (`gaya increment` CLI)

順番に実行 (詳細は pipeline/README.md の#194 runbook):

1. `anchor-bootstrap` → 明示reference役を除く役のanchor候補plan (N=4決定論的seed)
2. `anchor-generate` (GPU) → `anchor-select` (qc env、16kHz正規化F0で機械選抜。
   不合格役は有界top-up round 1..2)
3. `plan-build` → `generate` (GPU、161×N=4)
4. `completion qc` → `auto-decide` (PASQA) → `finalize` → `verify`
5. `publish` (Owner権限。inheritedはHEAD検証のみ、新規のみupload、activation生成)
6. site: 件数系テストの期待値はmodel数から導出されているか確認 → `vp check/test/build` +
   public-bundle検査 → 実ブラウザQA → PR → CI green → merge

## 条件バリアント追加 (`--ref` / `--text`)

テキスト指示型モデルは「明示reference 5役 + anchor 53役」の混合条件になるため、
#201 で **列内の条件を揃えた2列** へ分離する:

- `<model-id>--ref` (見本あり): 全58役を人間収録素材へ。明示referenceのある5役はそれを、
  残り53役は clone系と同じ `CLONE_REFERENCE_ASSIGNMENTS` を共用 (未割当役は fail fast)
- `<model-id>--text` (見本なし): 全58役をモデル自作の見本へ。明示referenceは無視する

manifest v4 の `models[]` に optional field
`conditioning: {base_model, mode}` が付き、`name` は `（見本あり）`/`（見本なし）` 付きになる。
既存の単方式モデルは field を持たないので公開済み manifest の canonical bytes は不変。

### 手順 (`gaya variant` CLI)

1. **anchor 補完** — `--text` 側で anchor を消費するモデル (Irodori v3/v4・Qwen3) は
   明示reference 5役ぶんの anchor が無いので新規に機械選抜する:
   `gaya increment anchor-bootstrap --role-scope explicit-reference-roles-v1`
   → `anchor-generate` (GPU) → `anchor-select`。seed base は 201 (既定53役は 194)
2. `gaya variant anchor-compose` — 既存53役 selection (#174 人手選抜 / #194 機械選抜) と
   新規5役 selection を **SHA束縛したまま** 58役の `role-anchor-variant-selection-v1` へ合成
3. `gaya variant plan-build` — base release の realized receipt から161行を
   `inherit` (条件一致 = 公開済みテイクをbyte不変で継承) と `generate` (新規生成) に機械分割。
   base release の5 SHAをplanにpinする
4. `gaya variant generate` (GPU) → `completion qc` → `gaya variant auto-decide` (PASQA)
5. `gaya variant finalize` — 8列ぶんの入力を1つの
   `role-conditioning-variant-finalize-spec-v1` にまとめて渡し、13列 release を確定
6. `gaya variant verify` → `gaya variant publish`

### 注意点

- **VoxCPM2 の `--text` は anchor 不要**。adapter 内蔵の voice design (自己参照) が
  text-only 経路そのものなので、anchor plan/run/selection を作らない
  (`requires_anchor_authority("voxcpm2--text") == False`)
- `role_scope` は plan → `run_anchor_bootstrap_generation` → adapter の
  `role_anchor_generation_input` / `generate_role_anchor` まで引数で流れる。
  adapter 側の「明示reference役は anchor 対象外」guard はこの scope でのみ緩む
  (既定scopeでは凍結契約のまま拒否)。新規 anchor adapter を足すときは
  この2メソッドに `role_scope` キーワードを実装すること
- **`--ref` 列は anchor 権限を持たない**。`--anchor-selection` を渡すと拒否される
- 継承テイクは `take_id` / `audio SHA` / `generation_input_sha256` が不変。
  `path` だけが model id を含むため列 id に追従する
  → **R2 object key が変わるので継承テイクも新規keyへuploadされる** (bytes は同一)
- 旧混合列 (base id) は `models[]` / selection から消える。系譜は release provenance の
  `superseded_by` に `{model: <base id>, replaced_by: [--ref, --text]}` として残る
- 列内の条件は `verify` が realized receipt で機械確認する
  (`--ref` は全行 human reference、`--text` は全行 model generated)

## 実地で踏んだ罠 (再発防止)

| 罠 | 対処 |
| --- | --- |
| torchcodecがFFmpeg shared DLLを要求 | winget `Gyan.FFmpeg.Shared` のbinをPATH先頭へ (README記載) |
| worktreeに `assets/voices/<id>/` の実体がない | 実コピーが必要 (junction/symlinkは参照検証のresolveで拒否される) |
| GPU長時間runのhost commit枯渇 | 上記empty_cache + 他セッションのRAM圧に注意。free RAM/RSSの分次ログを推奨 |
| ツールの実行timeoutでrun中断 | 生成はdetachedプロセスで実行。anchor runは `.pending` が残ると再実行拒否 (決定論的seedなので破棄→再生成で同一結果) |
| `pasqa-ranking/.venv` がworktree掃除で消える | `uv sync --locked` で再構築してからauto-decide |
| finalizeのresolveが凍結既定に落ちる | `anchor_bound_models` と `expected_group_count` を増分plan由来で明示 (auto-decide側と対称に) |
| site固定値テスト (1288 slot / 8 model / 120 pair) | model数×161から導出する形に置換済み。新規テストで件数を直書きしない |

## フォローアップ既知課題

- モデル詳細ページの「生成条件」がanchorモデルでは役ごとに分裂し設定1..161と表示される
  (profile groupingのkeyが細かすぎる)。表示側の集約改善が必要
