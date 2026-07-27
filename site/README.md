# Gaya Bench Site

Vite Plus + React + TypeScript + Tailwind CSS + shadcn/ui で構築する静的 SPA。

## セットアップ

依存関係は `site/` から Vite Plus 経由で同期する。

```console
vp install
```

`VITE_AUDIO_BASE` は必須。ローカル音声を使う場合は `/` を指定する。

```powershell
$env:VITE_AUDIO_BASE = "/"
vp dev
```

## ダミー音声

リポジトリルートで既存の dummy adapter を実行し、追跡対象の
`data/manifest.json` と、git 管理外の Opus を生成する。

```powershell
uv run --project pipeline --locked gaya gen --model dummy --scenario tavern-night
uv run --project pipeline --locked gaya gen --model dummy --scenario market-day
New-Item -ItemType Directory -Path site/public/audio -Force
Copy-Item artifacts/audio/dummy site/public/audio -Recurse -Force
```

`site/public/audio/` はローカル検証専用で、音声バイナリはコミットしない。
manifest の `path` は `audio/...` なので、`VITE_AUDIO_BASE=/` で
`site/public/audio/...` を参照する。

## 検証

```console
vp check
vp test
vp build
```
