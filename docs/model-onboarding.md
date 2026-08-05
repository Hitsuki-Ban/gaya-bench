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
