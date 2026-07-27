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

生成物は `artifacts/audio/`、公開用メタデータは `data/manifest.json` に出力される。`ffmpeg` と `ffprobe`（libopus encoder を含む）が必須。
