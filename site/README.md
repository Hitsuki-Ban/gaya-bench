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

公開サイトは比較マトリクス、シナリオ、モデル、A/B、クレジットだけを含む。
音声選定と事前確認は production module graph に含めず、ローカル専用 entry を
明示コマンドで開く。

```powershell
$env:VITE_AUDIO_BASE = "/"
vp run dev:internal
```

ローカルツールは `internal.html#/curate` と `internal.html#/pilot` を提供する。
通常の `vp build` は `index.html` だけを entry とし、内部 page/module が公開 chunk
へ到達した場合は build 自体が失敗する。build 後は次の検査も実行する。

```console
vp run check:public-bundle
```

本番は R2 custom domain を指定する。

```dotenv
VITE_AUDIO_BASE=https://audio.gaya-bench.hitsuki.space/
```

## 公開データ契約

サイトは `data/manifest.json` の `format_version=4` だけを読み込む。
`curations[].decision=selected` の `take_id` と同一 group の
`candidates[].take_id` が完全一致した候補だけが再生可能で、A/B 比較にも selected
だけを渡す。先頭候補への暗黙の置換は行わない。

比較用投影は group を次の四態で保持する。

- `selected`: 策展で選ばれた再生可能 candidate
- `skipped`: 策展で公開対象外になった candidate group
- `uncurated`: candidate はあるが策展判断がない group
- `failure`: candidate が成立しなかった logical failure

group 自体が存在しない場合だけ cell は未定義になる。manifest v4 契約、参照、
selected join が不正な場合は起動時に失敗する。

`/credits` は同じ manifest の model エントリと固定 provenance、および
`assets/voices/metadata.yaml` の全 reference voice を表示する。model ごとの
repository/revision が candidate 間で一致しない場合、voice metadata が schema
相当の exact contract を満たさない場合、scenario が未登録の reference voice を
参照する場合も起動時に失敗する。

## ローカル音声

manifest v4 の candidate path は take identity に固定された
`audio/takes/<model>/<scenario>/<line>/<variant>/take-<index>-<sha256>.opus`
である。ローカル検証では selected Opus をこの immutable path のまま
`site/public/` 配下へ配置し、`VITE_AUDIO_BASE=/` で参照する。

`site/public/audio/` はローカル検証専用で、音声バイナリはコミットしない。
サイト起動のために `data/manifest.json` を生成・更新する手順はない。

## 検証

```console
vp check
vp test
vp build
vp run check:public-bundle
```
