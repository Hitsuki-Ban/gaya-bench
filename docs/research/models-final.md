# TTS モデル統合選定レポート

**確定案作成日:** 2026-07-28

**対象:** RPG モブ NPC の短尺ガヤ音声ベンチ

**実行環境:** Windows 11 / RTX 4070 Ti 12GB / RAM 32GB

## 1. 推薦結論

一次情報による独立検証を統合した結果、ベンチ対象は次の **8モデル**を推薦する。

- **主力4:** Qwen3-TTS、Irodori-TTS、VoxCPM2、GPT-SoVITS
- **日本語・軽量基準線2:** AivisSpeech + 公式 ACML-1.0 モデル、Supertonic 3
- **条件付き比較2:** Chatterbox Multilingual、CosyVoice 3

Step-Audio-EditX は表現編集能力が最も魅力的だが、モデル重みのライセンスが公式に明示されていないため確定候補から外す。MOSS-TTS Local-Transformer-v1.5 は 4B 本体と必須 tokenizer の配布重みだけで12GBを超えるため、ローカル枠から外す。

ここでの推薦は音質順位ではない。アダプタを実装して同一シナリオを生成し、短文の自然さ・演技幅・声の多様性・難所耐性・速度を比較するための、役割が重ならない構成である。

## 2. 推薦マトリクス

証拠レベルは、L1 = 公式の VRAM 値または CPU 実行、L2 = 環境を特定できる第三者実測、L3 = 公式配布物容量 / パラメータ数からの見積もり、L4 = 根拠不足とする。L1 でも本機実測済みという意味ではない。G1 = 重み・生成物の商用条件不足、G2 = 公式日本語非対応、G3 = 12GB 超、G4 = 開発終了、G5 = 声設計 / クローンなし、を採否ゲートとする。

| 優先 | モデル / 役割 | 日本語 | 商用利用の一次根拠 | 12GB 根拠 | Windows | 感情・難所 | 声の多様性 | 鮮度 / リスク |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A1 | **Qwen3-TTS 12Hz 1.7B** / 声設計本命 | 公式10言語 | [コード Apache-2.0](https://github.com/QwenLM/Qwen3-TTS/blob/main/LICENSE) / [重み metadata Apache-2.0](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign) | L3: 主重み約3.83GB。ピーク未確認 | 要実機 | 自然言語指示。専用パラ言語タグなし | VoiceDesign → Base の約3秒クローン | 2026。公式 GGUF なし |
| A2 | **Irodori-TTS 600M-v3-VoiceDesign** / 日本語声設計 | 日本語専用 | [コード MIT](https://github.com/Aratako/Irodori-TTS/blob/main/LICENSE) / [モデルカード MIT](https://huggingface.co/Aratako/Irodori-TTS-600M-v3-VoiceDesign) | L3: F32 配布物約2.47GB。ピーク未確認 | 公式 Linux / Windows 手順 | テキスト声設計・スタイル。SilentCipher 透かし | VoiceDesign | 追加方針は無断なりすまし・誤情報等を禁止。商用禁止なし |
| A3 | **VoxCPM2** / 48kHz・明瞭度比較 | 公式30言語 | [コード Apache-2.0](https://github.com/OpenBMB/VoxCPM/blob/main/LICENSE) / [重み metadata Apache-2.0・商用可表示](https://huggingface.co/openbmb/VoxCPM2) | **L1: 公式 runtime 表が BF16 VRAM 約8GB** | 要実機 | 自然言語 Voice Design / 制御クローン | ゼロショットクローン | 2026。日本語固有名詞は未評価 |
| A4 | **GPT-SoVITS v2ProPlus** / 参照音声クローン基準 | 公式対応 | [コード MIT](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/LICENSE) / [重み MIT](https://huggingface.co/lj1995/GPT-SoVITS) | **L1: ローカル CPU 可** / GPUピーク L4 | **公式 Windows 10+** | 専用感情タグなし。参照音声依存 | 5秒ゼロショット | 継続更新。権利確認済み参照音声が必須 |
| B1 | **AivisSpeech + 公式 ACML-1.0 モデル** / 日本語品質基準 | 日本語専用 | [Engine LGPL-3.0](https://github.com/Aivis-Project/AivisSpeech-Engine) / [ACML-1.0 営利利用許諾](https://github.com/Aivis-Project/ACML/blob/master/ACML-1.0.md) | **L1: CPU可、RAM 1.5GB** | **公式 Windows 10/11** | モデルごとの固定スタイル | 固定話者。クローンなし | 公式「コハク」「まお」に限定 |
| B2 | **Supertonic 3** / 超軽量固定声基準 | 公式31言語 | [コード MIT](https://github.com/supertone-inc/supertonic/blob/main/LICENSE) / [重み OpenRAIL-M](https://huggingface.co/Supertone/supertonic-3/blob/main/LICENSE) | **L1: 公式 GPU不要、99M** | ONNX クロスプラットフォーム | 10 expression tags | open-weight は固定声。公式クローンなし | **アーカイブ・サポート終了予告** |
| C1 | **Chatterbox Multilingual** / 透かし付き多言語比較 | 公式23言語（日本語 `ja` を含む） | [コード MIT](https://github.com/resemble-ai/chatterbox/blob/master/LICENSE) / [重み MIT・言語 metadata](https://huggingface.co/ResembleAI/chatterbox) | L3: v3主要重み約3.2GB。ピーク未確認 | 要実機 | `exaggeration`。Multilingual のパラ言語タグは未確認 | ゼロショットクローン | PerTh 透かし |
| C2 | **CosyVoice 3 0.5B** / Apache 軽量比較 | 公式9言語 | [コード Apache-2.0](https://github.com/FunAudioLLM/CosyVoice/blob/main/LICENSE) / [重み Apache-2.0](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512) | L3: 0.5B 配布物。ピーク未確認 | 要実機 | 指示文で感情・速度・音量 | ゼロショット / クロスリンガル | 2025-12。日本語個別評価不足 |

### 共通の商用判定

Apache-2.0 / MIT はモデルやコードの利用を妨げないが、生成音声の第三者権利を保証しない。クローン候補では [参照音声方針](ja-ecosystem-rights.md) に従い、許諾済み素材または自前録音だけを使用する。VoiceDesign の生成声が特定の実在人物・声優に似ていると制作担当が認識した場合は、その声 ID を不採用として再生成し、実在人物名をプロンプトへ入れない。Supertonic の OpenRAIL-M、Irodori の追加利用方針、Aivis の ACML 禁止用途は、最終的にクレジット / ライセンスページへ記録する。

## 3. 実装順

| 波 | 対象 | 目的 | 着手ゲート |
| --- | --- | --- | --- |
| 1 | Qwen3-TTS / Irodori-TTS | 声設計本命を先に比較し、架空モブ声の生成方式を決める | 1文生成、ピーク VRAM、Windows 導入を記録 |
| 2 | AivisSpeech / GPT-SoVITS | ネイティブ Windows で日本語品質と参照音声クローンの基準線を確立 | 公式 ACML モデルと許諾済み参照音声のみ |
| 3 | VoxCPM2 / Chatterbox | 多言語クローンの品質・透かし・48kHz の差を測る | 12GB と Windows の実機通過 |
| 4 | CosyVoice 3 / Supertonic 3 | 軽量 Apache 比較と CPU 固定声ベースライン | Supertonic は固定資産を保存し、外部 Voice Builder に依存しない |

固定声の AivisSpeech / Supertonic は全モデル共通の代表短文で自然さ・難所耐性・速度を比較するが、声の多様性軸は「対象外」とし、クローン / VoiceDesign モデルと同じ総合点へ混ぜない。

実機アダプタは、各モデルで次を同じ形式で記録する。

- モデルとリビジョン
- OS、Python、PyTorch、CUDA、精度 / 量子化
- アイドル時、ロード後、生成時ピーク VRAM と RAM
- 1〜3秒の日本語短文の成否、RTF、出力サンプル
- Windows ネイティブで失敗した具体的依存
- 透かし、クレジット、参照音声 ID

L3 / L4 のモデルに silent fallback は設けない。12GB OOM または Windows 非対応ならアダプタは明示的に失敗させ、WSL / RunPod へ自動退避しない。

## 4. 保留・落選

| モデル | 判定 | 理由 | 再評価条件 |
| --- | --- | --- | --- |
| **Step-Audio-EditX** | 保留 | コード Apache-2.0 は確認できるが、通常版・AWQ 重みのライセンスと生成物商用条件が未記載。公式検証 OS は Linux | StepFun が重み利用条件を一次情報で明記し、AWQ 8〜10GB を Windows または承認済み単一実行環境で通す |
| **MOSS-TTS Local-Transformer-v1.5** | ローカル落選 | 4B 本体約8.48GiB + 必須 F32 tokenizer 約7.91GiB。公式標準例は両方を GPU に載せ、低メモリ経路なし。多話者対話は別モデル | 公式の量子化 / offload 経路、または RunPod 枠の承認 |
| **MioTTS-2.6B** | 次点 | LFM Open License の企業規模条件を継続管理する必要があり、既存8本に対する役割差が小さい | 日本語品質が上位候補を明確に上回る一次・再現可能評価 |
| **ZONOS2** | 次点 | v2 でガヤ向け感情制御の公式根拠が弱い | v2 の感情・非言語制御と12GB実測が公式化 |
| **Style-Bert-VITS2 単体** | 落選 | Engine の AGPL-3.0 と個別モデル権利管理を増やす一方、AivisSpeech 基準線と役割が重複 | Aivis で必要な品質・モデルが得られない場合 |
| **Fish Audio S2 Pro / Higgs Audio V3 / Sarashina2.2-TTS** | 落選 | 非商用または別途商用契約が必要 | Owner が有料契約 / API アンカーを承認 |
| **日本語非対応群** | 落選 | 必須条件 G2 を満たさない | 公式日本語モデルの公開 |

## 5. Director 判断が必要な変更

暫定ショートリストから次の変更を提案する。

1. Issue #25 Step-Audio-EditX を、重みライセンス明示までブロックする。
2. Issue #29 の Local-Transformer-v1.5 4B というモデル名は維持し、12GB 不適合と多話者機能の取り違えを反映してローカル着手を止める。
3. Issue #31 Supertonic 3 を継続モデルから凍結ベースラインへ変更する。
4. Step の代替として CosyVoice 3 のアダプタ Issue を追加する。

この仕様差分は本レポートだけで既存 Issue を暗黙に変更せず、[question Issue #32](https://github.com/Hitsuki-Ban/gaya-bench/issues/32) で Director の判断対象にする。

## 6. 参照レポートとの統合結果

Claude 側調査からはモデル探索の広さ、日本語音声素材の権利実務、ガヤ用途の表現軸を採用した。Codex 独立検証では一次ライセンス、公式 VRAM 根拠、Windows 記載、モデルの現行バージョンを再確認した。

統合によって変わった重要点は次のとおり。

- Qwen の VoiceDesign → Base 固定化は本命として維持するが、公式 GGUF と 12GB 実測は未確認とした。
- Irodori は本命として維持するが、MIT 以外の利用方針・透かし・L3 VRAM を明示した。
- Step は能力評価ではなく G1 ライセンス不足で保留した。
- Aivis は任意のコミュニティモデルではなく、公式 ACML-1.0 モデルに限定した。
- MOSS は 4B という版情報を確認した一方、8GB適合と多話者対話の前提を撤回した。
- Chatterbox の言語数とパラ言語機能を公式範囲へ縮めた。
- Supertonic は供給終了を反映し、固定声の凍結ベースラインとした。

各訂正の証拠と一次 URL は [`models-codex.md`](models-codex.md) に集約している。
