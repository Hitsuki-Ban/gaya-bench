# Gaya Pipeline

Gaya Bench のシナリオ検証・音声生成を担う Python 3.12 / uv プロジェクト。

`pipeline/` から実行する:

```console
uv run gaya validate
```

リポジトリルートから全シナリオを検証する:

```console
uv run --project pipeline gaya validate
```

テストを実行する:

```console
uv run --project pipeline pytest pipeline/tests
```

## ダミー音声生成

リポジトリルートからダミーアダプタで生成する:

```console
uv run --project pipeline gaya gen --model dummy
```

対象を絞る場合は scenario を指定する。line は scenario 内だけで一意なため、必ず両方を指定する:

```console
uv run --project pipeline gaya gen --model dummy --scenario tavern-night
uv run --project pipeline gaya gen --model dummy --scenario tavern-night --line barmaid-001
```

生成物は `artifacts/audio/`、公開用メタデータは `data/manifest.json` に出力される。`ffmpeg` と `ffprobe`（libopus encoder を含む）が必須。正規化後の48kHz PCMは再測定され、-18 ±0.2 LUFS / peak -0.9 dBTP以下を満たさない場合はlookahead limiterで最大2回補正し、それでも範囲外なら生成を失敗させる。

`data/manifest.json` は format v2 を使用し、各 `(model, scenario, line, variant)` の最新結果を成功 (`clips`) または失敗 (`failures`) のどちらか一方に記録する。失敗したキーは次回実行時にキャッシュを使わず再生成し、成功すれば `clips` に戻る。公開 manifest の失敗理由は `generation_failed` のみで、例外の詳細は保存しない。

## Qwen3-TTS 12Hz-1.7B

`qwen3-tts-12hz-1.7b` は、VoiceDesign でキャラクターごとの参照音声を設計し、Base の reusable voice clone prompt で全セリフへ同じ声を適用する。

実行環境は Windows 11 / NVIDIA CUDA / BF16 / SDPA の単一経路で、Python 依存関係は Qwen 専用 extra として同期する。

```console
uv sync --project pipeline --locked --extra qwen
```

固定する上流は以下のとおり。

- `qwen-tts==0.1.1`
- `torch==2.11.0` / `torchaudio==2.11.0` (`cu130`)
- Base: `Qwen/Qwen3-TTS-12Hz-1.7B-Base@fd4b254389122332181a7c3db7f27e918eec64e3`
- VoiceDesign: `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign@5ecdb67327fd37bb2e042aab12ff7391903235d3`

初回実行時は Hugging Face の完全な revision snapshot をローカルキャッシュへ取得する。モデル読み込み中に別 revision へ追従しない。キャラクター参照音声は `artifacts/voices/qwen3-tts-12hz-1.7b/<scenario>/<character>/` に保存し、入力と WAV hash が一致するときだけ再利用する。

まず1行で CUDA・BF16・12GB VRAM の gate を確認する。

```console
uv run --project pipeline --locked --extra qwen gaya gen --model qwen3-tts-12hz-1.7b --scenario tavern-night --line barmaid-001
```

gate 通過後、受け入れ確認用の2シナリオを生成する。

```console
uv run --project pipeline --locked --extra qwen gaya gen --model qwen3-tts-12hz-1.7b --scenario tavern-night
uv run --project pipeline --locked --extra qwen gaya gen --model qwen3-tts-12hz-1.7b --scenario market-day
```

2026-07-28 に RTX 4070 Ti 12GB で上記2シナリオ（12行）を実測し、失敗0件、最大 4,193 MiB allocated / 4,296 MiB reserved、warm RTF 5.52〜9.81（cold canary 42.79）だった。

Base の現行 API は line ごとの `instruct` を受け取らないため、この adapter は `character.voice` / `personality` / `scene.setting` を参照音声設計に使い、`emotion` capability は `false` と宣言する。未対応の `emotion` / `delivery` を生成入力へ偽装しない。

依存欠落、CUDA unavailable、BF16 非対応、モデル取得・読み込み失敗、OOM はその場で失敗する。CPU、GGUF、WSL、クラウド、別 attention backend への自動切替は行わない。
