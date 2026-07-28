# システム構成

## 全体データフロー

```
scenarios/*.yaml (真実の源)
    │  validate (JSON Schema + 相互参照チェック)
    ▼
pipeline (Python/uv) ── モデル別アダプタ ──▶ artifacts/audio/<model>/... (wav/opus, git外)
    │  ラウドネス正規化 (-18 LUFS mono, peak -1 dBTP)
    │  opusエンコード (libopus 64kbps mono 48kHz)
    ├──▶ data/manifest.json (リポジトリにコミット)
    ▼
`gaya publish` ── manifest/hash検証・差分アップロード ──▶ Cloudflare R2
              ▼
         site (Vite) ── ビルド時にmanifest取込み、音声はR2から再生
              ▼
         Cloudflare Pages (GitHub Actions で main → 本番デプロイ)
```

## 生成パイプライン (`pipeline/`)

- Python 3.12 + uv のCLIプロジェクト。エントリ: `uv run gaya <subcommand>`
  - `gaya validate` — シナリオのスキーマ検証 + 相互参照チェック (character参照、ID一意性)
  - `gaya gen --model <id> [--scenario <id>] [--line <id>]` — 生成実行
  - `gaya publish` — manifest/hash検証 → エンコード済みOpusをR2へ差分アップロード
- **アダプタインターフェース**: `LineJob { scene, character, line, locale }` を受け取り音声を返す。モデル固有の入力形式 (スタイルプロンプト / 感情タグ / 参照音声) への変換はアダプタが担う
- **capability profile**: アダプタごとに「スキーマのどのフィールドを解釈できるか」を宣言 (emotion対応 / voice記述対応 / クローン対応 / 非言語音対応...)。manifestに含め、サイトでバッジ表示する
- **冪等性**: 生成済みクリップは (アダプタが解釈する入力 + モデルversion + 生成パラメータ + 後処理profile) のハッシュと成果物hashが一致した場合のみスキップ。`--force` で再生成。manifest 上の最新結果が失敗の場合はキャッシュを使わず実生成を再試行する
- **manifest 更新**: 各ジョブの成功・スキップ・失敗をその場で原子的に反映する。変更がある場合だけ、同一ディレクトリの一時ファイルを `flush` / `fsync` してから `os.replace` する。バッチ全件が成功した後にだけ selector scope 内の古い結果を整理し、scope 外の結果は保持する
- **正規化の方針**: 全クリップを2-pass loudnormで -18 LUFS / peak -1 dBTP / mono / 48kHz に正規化する。落盤後のPCMを再測定し、ピーク制約で目標LUFSに届かない場合は最大2回のlookahead limiter補正を行う。それでも -18 ±0.2 LUFS / peak -0.9 dBTP以下を満たさなければ生成を失敗させる。モデル間の音量差による印象バイアスを除くため。囁き/叫びの意図的な音量差が失われる副作用はドキュメントに明記し、+αの scene バリアント (距離感シミュレーション) で補う
- **バリアント**: v1は `dry` (正規化のみ) 単一。`scene` (EQ+リバーブ+減衰の中距離シミュレーション) は+α

## manifest 形式 (v2)

```jsonc
{
  "format_version": 2,
  "generated_at": "...",
  "models": [ { "id": "...", "name": "...", "version": "...", "license_note": "...",
                 "capabilities": { "emotion": true, "voice_prompt": false, "clone": true,
                                   "nonverbal": false, "reading": true } } ],
  "clips": [ { "model": "...", "scenario": "...", "line": "...", "variant": "dry",
                "path": "audio/<model>/<scenario>/<line>-dry.opus",
                "duration_sec": 1.8, "sha256": "...", "gen_params": {}, "rtf": 0.4 } ],
  "failures": [ { "model": "...", "scenario": "...", "line": "...", "variant": "dry",
                   "reason": "generation_failed" } ]
}
```

`(model, scenario, line, variant)` は `clips` と `failures` を通して一意で、各キーの最新結果をどちらか一方だけに記録する。成功またはスキップは同じキーの失敗を置換し、失敗は古い成功を置換する。

`failure.reason` は公開安全な低基数 enum で、現在許可する値は `generation_failed` のみ。例外本文、モデル出力、ローカルパスなどの詳細は manifest に保存しない。manifest は `format_version: 2` と上記の完全なトップレベル項目だけを受理する。

## ストレージ

- **音声**: R2バケット `gaya-bench-audio` (公開読み取り)。パス: `audio/<model>/<scenario>/<line>-<variant>.opus`
- **公開URL**: custom domain `https://audio.gaya-bench.hitsuki.space/`。本番 `VITE_AUDIO_BASE` もこの値を使う。rate limit 付き開発用 `r2.dev` は有効化しない
- **CORS**: [infra/r2-cors.json](../infra/r2-cors.json) を正とし、Pages 本番 origin とローカル開発 origin の `GET` / `HEAD` を許可する
- **差分公開**: `gaya publish` は manifest の全 Opus をローカルで先に検証し、R2 `HEAD` の `sha256` metadata・サイズ・HTTP metadata が一致するものをスキップする。object key は再生成時に再利用するため、`Cache-Control: public, max-age=0, must-revalidate` として古い音声の長期固定を避ける
- **manifest**: `data/manifest.json` をリポジトリにコミット (ビルドの決定性とPRレビュー可能性のため)
- **生成メタ**: 入力hash・WAV/Opus hash・生成時間・RTF・後処理結果を `artifacts/audio/.../<line>-<variant>.json` に保存 (git管理外)
- **ローカル開発fallback**: `site/public/audio/` に同一パス構造で置き、`VITE_AUDIO_BASE` で切替

## サイト (`site/`)

- Vite Plus + React + TypeScript + Tailwind + shadcn/ui の静的SPA
- 環境変数: `VITE_AUDIO_BASE` (音声配信のベースURL)
- UX仕様は [ux-spec.md](ux-spec.md)

## CI/CD (GitHub Actions)

- PR: `gaya validate` + lint + typecheck + site build
- main push: 上記 + Cloudflare Pages デプロイ (wrangler)。プレビューデプロイはPR単位
- 音声生成・R2アップロードはCIでは行わない (生成は開発機ローカルで実行し、manifestのみPRに乗る)
