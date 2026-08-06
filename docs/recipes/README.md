# モデル別調理レシピ

各モデルを **公式配布物 (pip / GitHub / 公式アプリ) のみで、何もない環境から0からローカルで試す**ためのA4×1枚レシピ集。本リポジトリのパイプラインには依存しない。構成は全レシピ共通:

1. **0からのセットアップと試し生成** — 公式の入手先・インストール・最小生成コード
2. **入力の用意** — そのモデルに何を渡すか (見本音声の仕様 / captionの書き方 / 読み指定 など)
3. **最適化アドバイス** — 実測で効いた順のノブと落とし穴
4. **本ベンチでの成績** — 公開161行での実績と特記事項

| レシピ | 主方式 | VRAM実測 |
| --- | --- | --- |
| [AivisSpeech コハク](recipe-aivisspeech-kohaku.pdf) | プリセット | Engine側で軽量 |
| [Supertonic 3](recipe-supertonic-3.pdf) | プリセット | 軽量 (ONNX) |
| [Chatterbox Multilingual V3](recipe-chatterbox-multilingual-v3.pdf) | クローン | 4.0GB |
| [CosyVoice 3 (0.5B)](recipe-cosyvoice3-0.5b-2512.pdf) | クローン | 5.3GB |
| [GPT-SoVITS v2ProPlus](recipe-gpt-sovits-v2-pro-plus.pdf) | クローン | 1.8GB |
| [Irodori-TTS v3 (600M)](recipe-irodori-tts-600m-v3-voicedesign.pdf) | テキスト指示 | 6.0GB |
| [Irodori-TTS v4-Small](recipe-irodori-tts-v4-small.pdf) | テキスト指示 | 4.0GB |
| [Qwen3-TTS (12Hz 1.7B)](recipe-qwen3-tts-12hz-1.7b.pdf) | テキスト指示 | 5.8GB |
| [VoxCPM 2](recipe-voxcpm2.pdf) | テキスト指示 | 11.0GB |

- 全体像 (3方式・早見表・量産パイプライン・権利注意) は [../production-adoption-guide.pdf](../production-adoption-guide.pdf)
- 数値・パラメータの出典は公開 `data/manifest.json` の provenance (1,449音声)
- 再生成: `uv run --with reportlab python docs/recipes/build.py` (要 Meiryo フォント / Windows)
