# システム構成

## 全体データフロー

```
scenarios/*.yaml (真実の源)
    │  validate (JSON Schema + 相互参照チェック)
    ▼
pipeline (Python/uv) ── モデル別アダプタ ──▶ artifacts/audio/<model>/... (wav, git外)
    │  ラウドネス正規化 (-18 LUFS mono, peak -1 dBTP)
    │  opusエンコード (libopus 64kbps mono 48kHz)
    ▼
`gaya publish` ──▶ Cloudflare R2 (公開バケット gaya-bench-audio)
    │
    └──▶ data/manifest.json (リポジトリにコミット)
              ▼
         site (Vite) ── ビルド時にmanifest取込み、音声はR2から再生
              ▼
         Cloudflare Pages (GitHub Actions で main → 本番デプロイ)
```

## 生成パイプライン (`pipeline/`)

- Python 3.12 + uv のCLIプロジェクト。エントリ: `uv run gaya <subcommand>`
  - `gaya validate` — シナリオのスキーマ検証 + 相互参照チェック (character参照、ID一意性)
  - `gaya gen --model <id> [--scenario <id>] [--line <id>]` — 生成実行
  - `gaya publish` — エンコード → R2アップロード → manifest更新
- **アダプタインターフェース**: `LineJob { scene, character, line, locale }` を受け取り音声を返す。モデル固有の入力形式 (スタイルプロンプト / 感情タグ / 参照音声) への変換はアダプタが担う
- **capability profile**: アダプタごとに「スキーマのどのフィールドを解釈できるか」を宣言 (emotion対応 / voice記述対応 / クローン対応 / 非言語音対応...)。manifestに含め、サイトでバッジ表示する
- **冪等性**: 生成済みクリップは (line内容 + 生成パラメータ) のハッシュでスキップ。`--force` で再生成
- **正規化の方針**: 全クリップを -18 LUFS mono に正規化する。モデル間の音量差による印象バイアスを除くため。囁き/叫びの意図的な音量差が失われる副作用はドキュメントに明記し、+αの scene バリアント (距離感シミュレーション) で補う
- **バリアント**: v1は `dry` (正規化のみ) 単一。`scene` (EQ+リバーブ+減衰の中距離シミュレーション) は+α

## manifest 形式 (v1)

```jsonc
{
  "format_version": 1,
  "generated_at": "...",
  "models": [ { "id": "...", "name": "...", "version": "...", "license_note": "...",
                 "capabilities": { "emotion": true, "voice_prompt": false, "clone": true, "nonverbal": false } } ],
  "clips": [ { "model": "...", "scenario": "...", "line": "...", "variant": "dry",
                "path": "audio/<model>/<scenario>/<line>-dry.opus",
                "duration_sec": 1.8, "sha256": "...", "gen_params": {}, "rtf": 0.4 } ]
}
```

## ストレージ

- **音声**: R2バケット `gaya-bench-audio` (公開読み取り)。パス: `audio/<model>/<scenario>/<line>-<variant>.opus`
- **manifest**: `data/manifest.json` をリポジトリにコミット (ビルドの決定性とPRレビュー可能性のため)
- **ローカル開発fallback**: `site/public/audio/` に同一パス構造で置き、`VITE_AUDIO_BASE` で切替

## サイト (`site/`)

- Vite Plus + React + TypeScript + Tailwind + shadcn/ui の静的SPA
- 環境変数: `VITE_AUDIO_BASE` (音声配信のベースURL)
- UX仕様は [ux-spec.md](ux-spec.md)

## CI/CD (GitHub Actions)

- PR: `gaya validate` + lint + typecheck + site build
- main push: 上記 + Cloudflare Pages デプロイ (wrangler)。プレビューデプロイはPR単位
- 音声生成・R2アップロードはCIでは行わない (生成は開発機ローカルで実行し、manifestのみPRに乗る)
