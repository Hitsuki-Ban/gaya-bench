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
