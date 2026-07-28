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

## R2 への公開

リポジトリルートの `.env` に、`gaya-bench-audio` だけを対象にした R2 Object Read & Write credential を設定する。`.env` と `.env.*` は git 管理外であり、API token / access key / secret をコミットしない。

```dotenv
CLOUDFLARE_ACCOUNT_ID=<Cloudflare account ID>
R2_ACCESS_KEY_ID=<R2 access key ID>
R2_SECRET_ACCESS_KEY=<R2 secret access key>
```

プロセス環境に同名の値がある場合はそちらを優先し、不足時は明示的に失敗する。Wrangler の OAuth / API token はバケット設定用、上記 S3 credential は Opus アップロード用であり、用途を混在させない。

生成後、manifest と全 Opus の path / SHA-256 を先に検証してから差分アップロードする。

```console
uv run --project pipeline --locked gaya publish
```

R2 の `HEAD` で `sha256` metadata、サイズ、`Content-Type`、`Cache-Control` が一致するオブジェクトはスキップする。同一生成物に対する2回目の実行はアップロード0件になる。公開 URL は `https://audio.gaya-bench.hitsuki.space/`、サイト本番値は `VITE_AUDIO_BASE=https://audio.gaya-bench.hitsuki.space/` とする。

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

## Irodori-TTS 600M-v3-VoiceDesign

`irodori-tts-600m-v3-voicedesign` は、日本語テキスト、自然言語 caption、任意の参照音声を3分岐の条件として扱う。`character.reference_voice` が voice ID の場合は登録済み WAV を使う clone、`null` の場合は明示的な no-ref VoiceDesign を実行する。参照音声が未登録、欠落、または metadata の SHA-256 と不一致なら生成前に失敗する。

Qwen の PyTorch 2.11 / cu130 と Irodori の PyTorch 2.10 / cu128 は同居させず、相互排他的な uv extra として同期する。Irodori 上流が固定する `sentencepiece==0.1.99` は Python 3.12 Windows wheel がなく、source build が必要になる。検証環境は CMake 4.3.3 と Visual Studio Build Tools 2022 17.14.31（`Microsoft.VisualStudio.Component.VC.Tools.x86.x64`）を使用した。同期前に次で両方を確認する。

```powershell
cmake --version
& "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" `
  -latest -products * `
  -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
  -property installationVersion
```

`pyproject.toml` はその package の隔離 build だけに CMake policy minimum を指定する。CMake または MSVC C++ toolchain がなければ `uv sync` は明示的に失敗し、システム設定の変更や別 package への切替は行わない。

```console
uv sync --project pipeline --locked --extra irodori
uv run --project pipeline --locked --extra irodori gaya voices validate-local
```

固定する上流は以下のとおり。

- Code: `Aratako/Irodori-TTS@eaf74d6a19138f743acb5b71a445fd25a57db987`
- Model: `Aratako/Irodori-TTS-600M-v3-VoiceDesign@e863a3a93e652e09afeff3e84823a206a0a60314`
- Codec: `Aratako/Semantic-DACVAE-Japanese-32dim@47376ee24834d7a05a48ebabfe3cde29b3c5e214`
- DACVAE code: `facebookresearch/dacvae@414c20785fc3a28373073ea8ef7a1316eeeaca6e`
- Tokenizer: `llm-jp/llm-jp-3-150m@b112feef602fff752e4dac4c30af6a2c2fa41c7a`
- SilentCipher model: `sony/silentcipher@a1c4d021905e0dc5b24be5f68db5fc4dba410ee1`
- SilentCipher code: `SesameAILabs/silentcipher@d46d7d0893a583d8968ab3a6626e2289faec9152`
- `torch==2.10.0` / `torchaudio==2.10.0` (`cu128`)、`torchcodec==0.10.0`
- `pyopenjtalk-plus[onnxruntime]==0.4.1.post8`

読みは `line.reading` が non-empty string なら原文どおり優先し、それ以外は `pyopenjtalk.g2p(text, kana=True)` で片仮名化する。これはベンチの読み再現性を揃える方針であり、Irodori 作者が全入力のかな化を必須としているという意味ではない。固定 model card は通常の漢字入力を受け付け、複雑な漢字でかな変換が必要になる場合があると説明している。

`character.voice`、`line.delivery`、`line.emotion`、`line.intensity` は caption に反映する。emotion は固定 model revision の[公式 emoji 表](https://huggingface.co/Aratako/Irodori-TTS-600M-v3-VoiceDesign/blob/e863a3a93e652e09afeff3e84823a206a0a60314/EMOJI_ANNOTATIONS.md)に従い、次を入力テキストの先頭へ付ける。

| emotion | emoji |
| --- | --- |
| `neutral` | なし |
| `cheerful` | 😊 |
| `angry` | 😠 |
| `sad` | 😭 |
| `fearful` | 😰 |
| `surprised` | 😲 |
| `tired` | 😪 |
| `drunk` | 🥴 |
| `whisper` | 👂 |
| `shout` | 😱 |
| `laughing` | 🤭 |
| `pain` | 😖 |

まず clone を含む1行で CUDA・BF16・12GB VRAM・参照音声・watermark の gate を確認する。

```console
uv run --project pipeline --locked --extra irodori gaya gen --model irodori-tts-600m-v3-voicedesign --scenario tavern-night --line barmaid-001
```

gate 通過後、受け入れ確認用の2シナリオを生成する。

```console
uv run --project pipeline --locked --extra irodori gaya gen --model irodori-tts-600m-v3-voicedesign --scenario tavern-night
uv run --project pipeline --locked --extra irodori gaya gen --model irodori-tts-600m-v3-voicedesign --scenario market-day
```

2026-07-28 に Windows 11 / RTX 4070 Ti 12GB で上記2シナリオ（12行）を BF16 / 40 steps / seed 0 で実測し、失敗0件、最大 2,493 MiB allocated / 3,696 MiB reserved、warm RTF 0.552〜1.093（平均 0.800）だった。12出力はすべて48kHz monoで、固定 SilentCipher snapshot による payload `IRDTS` の埋め込み stage を実行した。最終 Opus の独立 decode では 8/12 が payload と完全一致し、1件は不一致、3件は未検出だったため、後処理後の検出可能性は保証しない。段階別の残存率調査は [#65](https://github.com/Hitsuki-Ban/gaya-bench/issues/65) で扱う。

Code、model weight、codec は MIT。学習データの詳細と生成物の独立ライセンスは公開されていない。参照音声は `assets/voices/metadata.yaml` の権利条件に従う。実在人物・声優の無断模倣、誤認を招く deepfake は禁止する。上流 SilentCipher は model load 失敗時に未透かし音声へ進むが、この adapter は固定 snapshot、`watermarker.ready`、埋め込み stage の実行を必須とする。manifest には stage の実行事実を記録し、最終 Opus からの検出成功とは区別する。

依存欠落、Windows native 以外、CUDA unavailable、BF16 非対応、cu128 wheel 不一致、固定 revision の取得失敗、参照 WAV 不備、SilentCipher 不可、OOM は明示的に失敗する。CPU、WSL、別 CUDA wheel、量子化、クラウド、無透かし音声への自動切替は行わない。
