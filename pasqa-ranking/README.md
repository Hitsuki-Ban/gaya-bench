# PASQA take ranking

同じ `scenario_id / line_id / model_id / variant` の N テイクだけを、
PASQA の予測値で相対順位付けする独立 Python 3.10 / uv プロジェクト。
通常の `pipeline/`、`gaya gen`、`gaya qc` からは呼び出さない。

## セットアップ

リポジトリルートから実行する。

```console
uv sync --project pasqa-ranking --locked
uv run --project pasqa-ranking --locked gaya-pasqa prepare \
  --model-dir artifacts/models/pasqa
```

`prepare` は次を固定 revision から取得し、SHA-256 を検証する。

- PASQA code:
  `bdbd3f84049b1ff3925e27888949831fc1977413`
- Hugging Face weights:
  `ly-corporation/PASQA@7fe0bfc7dff16991599043bcafb886c7d597419a`
- checkpoint:
  `03c9e8880a28f65fd9b8611f3fe3e179020b067d892cd6f6a4c311572b8a8bc7`

既存ファイルの hash が異なる場合は上書きせず失敗する。モデルは
`artifacts/` 配下に置き、Git には追加しない。

## 入力

1ファイルは1つの同一行・同一モデル・同一 variant だけを表す。
`mora_tokens` は明示的なカタカナモーラ列であり、自動推定しない。

```json
{
  "format_version": 1,
  "group": {
    "scenario_id": "castle-gate",
    "line_id": "guard-onna-002",
    "model_id": "qwen3-tts-12hz-1.7b",
    "variant": "dry"
  },
  "mora_tokens": ["ト", "マ", "レ"],
  "takes": [
    {
      "take_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "audio_path": "audio/take-0001.wav"
    },
    {
      "take_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "audio_path": "audio/take-0002.wav"
    }
  ]
}
```

音声パスは入力 JSON の親ディレクトリ基準。入力は次をすべて満たす必要がある。

- 2テイク以上
- mono / 16 kHz
- 1,040 sample 以上、160,000 sample（10秒）以下
- take ID、音声パスとも重複なし
- 全 mora token が固定 vocab に存在

PASQA 本体の未知 token → ID 0、リサンプル、短音声 padding、10秒超の truncation
はこのラッパーで事前に拒否する。

## 順位付け

```console
uv run --project pasqa-ranking --locked gaya-pasqa rank \
  --model-dir artifacts/models/pasqa \
  --input artifacts/pasqa-inputs/castle-gate-guard-onna-002.json \
  --output artifacts/pasqa-reports/castle-gate-guard-onna-002.json
```

出力は同一 group 内の score 降順ランキングと、入力音声・モデル・runtime の
provenance を含む。出力先が既に存在する場合は上書きしない。

## 用途制限

[日本語ネイティブ性調査](../docs/research/expressiveness/ja-nativeness.md)に記録した
著者注意事項どおり、PASQA は絶対値の較正器ではない。

- 同一行・同一モデル・同一 variant の N テイク内順位付け専用
- hard/soft gate、絶対閾値、モデル間・行間比較は禁止
- 文末イントネーションの判定には使わない
- pipeline 3.12 への import、未導入時の fallback、自動 mora 推定は禁止
