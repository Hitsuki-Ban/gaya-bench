# システム構成

## 全体データフロー

```
scenarios/*.yaml (真実の源)
    │  validate (JSON Schema + 相互参照チェック)
    ▼
pipeline (Python/uv) ── モデル別アダプタ ──▶ artifacts/takes/<run-id>/ (wav/opus/ledger, git外)
    │  ラウドネス正規化 (-18 LUFS mono, エンコード前 peak -1.75 dBTP)
    │  opusエンコード (libopus 64kbps mono 48kHz)
    │  gaya qc --run-id (Gate 1/2、Gate 3 report-only)
    ├──▶ run-local manifest-v4.json (eligible take のみ)
    │
    ├──▶ data/manifest.json (公開切替までは既存 v3 を維持)
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
  - `gaya gen --model <id> --takes <N> --seed-base <S> [--scenario <id>] [--line <id>]` — run ledger 単位の候補生成
  - `gaya qc --run-id <id>` — run ledger の全 take を gate し、terminal run の local manifest v4 snapshot を確定
  - `gaya publish` — manifest/hash検証 → エンコード済みOpusをR2へ差分アップロード
- **アダプタインターフェース**: `LineJob { scene, character, line, locale }` を受け取り音声を返す。モデル固有の入力形式 (スタイルプロンプト / 感情タグ / 参照音声) への変換はアダプタが担う
- **capability profile**: アダプタごとに「スキーマのどのフィールドを解釈できるか」を宣言 (emotion対応 / voice記述対応 / クローン対応 / 非言語音対応...)。manifestに含め、サイトでバッジ表示する
- **冪等性**: source、group、N、seed-base、recipe、全 generation input と artifact provenance が一致する完了済み run だけを whole-run cache として再利用する。同じ input に異なる take identity の run が複数ある場合は自動選択しない。`--force` は既存 run を書き換えず、常に新しい run を生成する
- **ledger 更新**: `gaya gen` は `artifacts/takes/<run-id>/ledger.json` だけを操作履歴として原子的に checkpoint し、公開 manifest は更新しない。1 attempt の失敗は `generation_failed` として記録して残りを続行し、最後に非ゼロ終了を返す。再生成は既存 slot を変更せず新しい run で行う
- **take gate**: `gaya qc` は別 process で run ledger、sidecar、最終 Opus を再検証する。mechanical reject は content `not_run`、明示 reading mismatch は content reject、推定 reading は `review_required`、環境・provenance・解析不能は `blocked` とする。韻律値は report-only で、blocked/non-terminal run には v4 snapshot を作らない
- **正規化・エンコードの方針**: 後処理 algorithm v7 は全クリップを2-pass loudnormで -18 LUFS / mono / 48kHz に正規化し、`pre_encode_true_peak_target_dbtp` を -1.75 dBTP に固定する。落盤後のPCMを再測定し、ピーク制約で目標LUFSに届かない場合は最大2回のlookahead limiter補正を行う。各 WAV は libopus 64kbps VBR / application audio で1回だけエンコードする。公開 Opus をデコードして再測定し、Integrated Loudness が -18 ±1.5 LUFS を外れるか、True Peak が `distribution_true_peak_max_dbtp` の -0.9 dBTP を上回る場合は生成を失敗させる。±0.2 LUFSを外れるが硬い許容範囲内にあるクリップは manifest の `loudness.shortfall` を `true` にする。エンコード前目標は最終配信上限の代替ではなく、codec overshoot を抑えるためのヘッドルームであり、最終 Opus gate は常に fail-fast で適用する。候補値の根拠は [Opus配信用True Peakエンコード前シーリング実測](research/opus-true-peak-ceiling.md) に記録する。モデル間の音量差による印象バイアスを抑える一方、囁き/叫びの意図的な音量差が失われる副作用は +α の scene バリアント (距離感シミュレーション) で補う。Opus は Ogg muxer の format bitexact を有効にして、同一ツールチェーン・同一PCMから同一ファイルhashを生成する
- **現行 N-take sidecar / ledger (v1)**: 各 take の sidecar は run/slot、明示的 seed/sampling、`generation_input_sha256`、最終 Opus に拘束した `take_id`、WAV/Opus hash、実行した ffmpeg/ffprobe version と libopus capability、algorithm v7 の loudness provenance を保持する。ledger は sidecar 自体の SHA-256 も固定し、QC 後の parameter 改変を拒否する。toolchain identity も generation input hash に含め、異なる encoder build の artifact を cache に再利用しない。format v3 public manifest は v4 cutover まで read-only とする
- **バリアント**: v1は `dry` (正規化のみ) 単一。`scene` (EQ+リバーブ+減衰の中距離シミュレーション) は+α

## manifest 形式 (v3)

```jsonc
{
  "format_version": 3,
  "generated_at": "...",
  "models": [ { "id": "...", "name": "...", "version": "...", "license_note": "...",
                 "capabilities": { "emotion": true, "voice_prompt": false, "clone": true,
                                   "nonverbal": false, "reading": true } } ],
  "clips": [ { "model": "...", "scenario": "...", "line": "...", "variant": "dry",
                "path": "audio/<model>/<scenario>/<line>-dry.opus",
                "duration_sec": 1.8, "sha256": "...", "gen_params": {}, "rtf": 0.4,
                "loudness": { "source": "encoded_opus", "i_lufs": -18.57,
                              "tp_dbtp": -0.94, "shortfall": true } } ],
  "failures": [ { "model": "...", "scenario": "...", "line": "...", "variant": "dry",
                   "reason": "generation_failed" } ]
}
```

`(model, scenario, line, variant)` は `clips` と `failures` を通して一意で、各キーの最新結果をどちらか一方だけに記録する。成功またはスキップは同じキーの失敗を置換し、失敗は古い成功を置換する。

`clip.loudness` は最終 Opus をデコードして測定した値で、`source` は `encoded_opus` のみを許可する。`failure.reason` は公開安全な低基数 enum で、現在許可する値は `generation_failed` のみ。例外本文、モデル出力、ローカルパスなどの詳細は manifest に保存しない。manifest は `format_version: 3` と上記の完全な項目だけを受理する。

## ストレージ

- **音声**: R2バケット `gaya-bench-audio` (公開読み取り)。パス: `audio/<model>/<scenario>/<line>-<variant>.opus`
- **公開URL**: custom domain `https://audio.gaya-bench.hitsuki.space/`。本番 `VITE_AUDIO_BASE` もこの値を使う。rate limit 付き開発用 `r2.dev` は有効化しない
- **CORS**: [infra/r2-cors.json](../infra/r2-cors.json) を正とし、Pages 本番 origin とローカル開発 origin の `GET` / `HEAD` を許可する
- **差分公開**: `gaya publish` は manifest の全 Opus をローカルで先に検証し、R2 `HEAD` の `sha256` metadata・サイズ・HTTP metadata が一致するものをスキップする。object key は再生成時に再利用するため、`Cache-Control: public, max-age=0, must-revalidate` として古い音声の長期固定を避ける
- **manifest**: `data/manifest.json` をリポジトリにコミット (ビルドの決定性とPRレビュー可能性のため)
- **生成メタ**: 入力hash・WAV/Opus hash・生成時間・RTF・後処理結果を `artifacts/takes/<run-id>/audio/<model>/<scenario>/<line>/<variant>/take-<index>.json` に保存し、run root の `ledger.json` から参照する (git管理外)
- **ローカル開発fallback**: `site/public/audio/` に同一パス構造で置き、`VITE_AUDIO_BASE` で切替

## サイト (`site/`)

- Vite Plus + React + TypeScript + Tailwind + shadcn/ui の静的SPA
- 環境変数: `VITE_AUDIO_BASE` (音声配信のベースURL)
- UX仕様は [ux-spec.md](ux-spec.md)

## CI/CD (GitHub Actions)

- PR: `gaya validate` + lint + typecheck + site build
- main push: 上記 + Cloudflare Pages デプロイ (wrangler)。プレビューデプロイはPR単位
- 音声生成・R2アップロードはCIでは行わない (生成は開発機ローカルで実行し、manifestのみPRに乗る)
