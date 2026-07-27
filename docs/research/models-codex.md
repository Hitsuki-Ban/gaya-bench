# TTS モデル独立検証レポート

**検証日:** 2026-07-28

**対象環境:** Windows 11 / RTX 4070 Ti 12GB / RAM 32GB / Python 3.12

**比較対象:** [`open-weight-tts.md`](open-weight-tts.md)、[`ja-ecosystem-rights.md`](ja-ecosystem-rights.md)

## 1. 結論

Claude 側調査の方向性は概ね妥当だが、モデルの利用可否と実装順を変える重要な訂正があった。

1. **Step-Audio-EditX は現時点で候補確定できない。** 公式に Apache-2.0 と明記されるのはコードだけで、Hugging Face の通常版・AWQ 版にはライセンス表示も LICENSE ファイルもない。VRAM は AWQ なら公式目安 8〜10GBだが、Linux のみ検証済みである。
2. **MOSS-TTS Local-Transformer-v1.5 は 4B で、旧 Local 1.7B とは別チェックポイント。** v1.5 は約8.48GiBの BF16 本体に約7.91GiBの F32 Audio Tokenizer v2 を必要とし、公式標準例は両方を同じ GPU に載せる。公式の低 VRAM 経路がないため 12GB 候補にはできない。
3. **Supertonic 3 は継続開発候補ではなく凍結ベースライン。** 2026-07-23 の公式告知でリポジトリのアーカイブとサポート終了、Voice Builder の 2026-08-31 終了が予告された。オープンウェイト側に公式クローン実装はない。
4. **Irodori-TTS を「MIT なので無条件に商用可」とだけ記すのは不十分。** モデルカードに追加利用方針と SilentCipher 透かしがあり、公式 VRAM 値はない。一方、公式リポジトリは Windows 用 CUDA 12.8 セットアップを明記している。
5. **Qwen3-TTS の 97ms は 0.6B の値で、1.7B は 101ms。** 公式 GGUF は確認できない。12GB 適合は重みファイル容量からの事前見積もりであり、Windows 実測ではない。
6. **AivisSpeech + ACML-1.0 は日本語基準線として強い。** CPU 実行が公式対応され、ACML は営利利用と生成を明示的に許諾する。ただし禁止用途と話者への配慮を守り、公式配布モデルに限定する。

このため、確定候補は「ライセンス上の商用利用を妨げる条項がない」「日本語が公式対応」「12GB 以下の根拠がある」の三条件を分離して評価し、Windows 未検証モデルはアダプタ実機試験を通過するまで**条件付き**とする。

## 2. 検証方法

### 2.1 証拠レベル

| レベル | 意味 | 本レポートでの扱い |
| --- | --- | --- |
| L1 | 公式 README / モデルカードに VRAM、CPU 実行、量子化時メモリが明記 | 12GB 適合の直接根拠 |
| L2 | GPU、精度、バージョンを特定できる第三者実測 | 補助根拠。公式値より優先しない |
| L3 | 公式配布物の容量またはパラメータ数×精度から導出 | 重みが収まる根拠。実行時ピークの保証ではない |
| L4 | 根拠不足 | アダプタ実機検証まで適合未確定 |

VRAM と Windows 対応は別判定とした。Linux で 8GB と書かれていても、CUDA 拡張や FlashAttention が Windows で導入できるとは限らない。

### 2.2 ライセンス判定

コード、モデル重み、生成音声を分けて確認した。Apache-2.0 / MIT はソフトウェアや重みの利用条件であり、第三者の声・参照音声・学習データに関する権利非侵害を保証しない。本レポートの「商用可」は、**公式契約に商用ゲーム収録を妨げる用途制限が見当たらない**という意味で、法的保証ではない。

採否ゲートは次のとおり。

- G1: モデル重みの利用条件または生成物の商用利用可否が不明 → 候補確定しない
- G2: 日本語が公式対応に含まれない → 落選
- G3: 公式または再現可能な実測で 12GB 超 → ローカル枠から外し、必要なら RunPod
- G4: 公式開発終了・アーカイブ → 継続候補からベースラインへ降格
- G5: 声デザインもクローンもない → 多様性候補ではなく固定声ベースライン

## 3. 候補別の独立検証

### 3.1 Qwen3-TTS 12Hz 1.7B

- **日本語・機能:** 公式 10 言語に日本語を含む。VoiceDesign は自然言語による声設計、Base は約3秒の参照音声によるクローンを提供する。架空声を設計して Base で固定する案は、実在人物の参照音声を避ける運用として合理的である。
- **ライセンス:** コードは Apache-2.0。公式モデルカードの metadata も `license: apache-2.0` である。モデルリポジトリ内に独立した LICENSE ファイルはない。ライセンス自体は出力に追加の用途制限を置かないが、入力・参照音声と第三者権利は利用者責任。
- **12GB 根拠:** 1.7B VoiceDesign の主 safetensors は約 3.83GB、リポジトリ総量は約 4.52GBであるため L3。ランタイムのピーク VRAM は公式未記載。
- **Windows:** 公式手順に Windows の明記なし。FlashAttention 2 は任意だが公式推奨であり、Windows はアダプタ試験が必要。
- **訂正:** 公式論文の first-packet latency は 0.6B が 97ms、1.7B が 101ms。公式 GGUF 配布は確認できない。
- **一次情報:** [コード](https://github.com/QwenLM/Qwen3-TTS)、[コード LICENSE](https://github.com/QwenLM/Qwen3-TTS/blob/main/LICENSE)、[VoiceDesign 重み・Apache-2.0 metadata](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign)、[Base 重み](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base)、[論文](https://arxiv.org/html/2601.15621)

**判定:** 推薦。VRAM L3、Windows 要実機確認。

### 3.2 Irodori-TTS 600M-v3-VoiceDesign

- **日本語・機能:** 日本語専用。テキストによる声設計とスタイル表現を持つ。
- **ライセンス:** コードは MIT、モデルカードも MIT 表示。ただしモデルカードは無断の声真似・誤情報などを禁じる追加利用方針を掲げ、SilentCipher 透かしを統合している。生成物の権利非侵害保証はない。
- **12GB 根拠:** 617.1M の F32 配布物が約 2.47GBで L3。ピーク VRAM の公式値はない。
- **Windows:** 公式 README が NVIDIA 向け `cu128` セットアップを Linux / Windows 対応として明記する。
- **訂正:** 「約2GB VRAM」「RTF 0.13」「日本語品質首位」は公式の本環境実測ではない。候補理由に使えても 12GB 適合の直接証拠にはしない。
- **一次情報:** [コード](https://github.com/Aratako/Irodori-TTS)、[コード LICENSE](https://github.com/Aratako/Irodori-TTS/blob/main/LICENSE)、[モデルカード](https://huggingface.co/Aratako/Irodori-TTS-600M-v3-VoiceDesign)

**判定:** 推薦。VRAM L3、Windows 公式手順あり。透かしと追加利用方針をクレジット台帳に記録する。

### 3.3 VoxCPM2

- **日本語・機能:** 公式 30 言語に日本語を含む。ゼロショットクローン、Voice Design、Controllable Cloning を提供する。
- **ライセンス:** コードは Apache-2.0。公式モデルカードの metadata も `license: apache-2.0` で、本文は commercial-ready / free commercial use と明記する。モデルリポジトリ内に独立した LICENSE ファイルはないため、一次根拠はモデルカード自体である。
- **12GB 根拠:** 公式モデルカードの runtime 表が BF16 推論 VRAM を約 8GB と明記するため L1。これは配布ファイル容量ではなく公式実行目安だが、本機実測ではない。
- **Windows:** Python、PyTorch、CUDA 要件はあるが Windows の公式手順・検証記録はない。
- **訂正:** 48kHz と多言語対応は確認できるが、日本語品質や固有名詞耐性を示す公式個別評価はない。
- **一次情報:** [コード](https://github.com/OpenBMB/VoxCPM)、[コード LICENSE](https://github.com/OpenBMB/VoxCPM/blob/main/LICENSE)、[モデルカード](https://huggingface.co/openbmb/VoxCPM2)

**判定:** 推薦。VRAM L1、Windows 要実機確認。

### 3.4 GPT-SoVITS v2ProPlus

- **日本語・機能:** 日本語を公式対応し、5秒のゼロショットクローンを提供する。感情タグはなく、演技別の権利確認済み参照音声で補う。
- **ライセンス:** コードは MIT。公式配布先 `lj1995/GPT-SoVITS` も MIT 表示。
- **12GB 根拠:** 公式 README が CPU モードを提供し、Windows 10 以降の統合パッケージと `install.ps1` を案内するため、ローカル実行可否は L1。ただし GPU 推論のピーク VRAM は L4 であり、速度比較に使う GPU 経路はアダプタで確認する。
- **Windows:** 公式対応。
- **訂正:** 「推論6GB / 学習12GB」は今回確認した一次情報にないため採用しない。
- **一次情報:** [コード](https://github.com/RVC-Boss/GPT-SoVITS)、[コード LICENSE](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/LICENSE)、[公式重み](https://huggingface.co/lj1995/GPT-SoVITS)

**判定:** 推薦。ネイティブ Windows / CPU フォールバックを持つクローン基準線。

### 3.5 AivisSpeech Engine + ACML-1.0 公式モデル

- **日本語・機能:** Style-Bert-VITS2 系の固定話者・スタイル型。ゼロショットクローンはなく、多様性の本命ではなく日本語アクセント基準線。
- **ライセンス:** Engine は LGPL-3.0。ACML-1.0 はモデルの実行と音声生成、法人・営利利用を明示的に許可し、クレジットは任意。なりすまし、誹謗中傷、虚偽情報、政治・宗教プロパガンダ等は禁止される。公式配布の「コハク」「まお」は ACML-1.0 表示。
- **12GB 根拠:** 公式 Engine が CPU 実行可、最低 RAM 1.5GB と記載するため L1。GPU 不要。
- **Windows:** Windows 10 / 11 を公式対応。
- **訂正:** AivisHub の全モデルを一括で安全とはみなさない。商用ベンチでは公式 ACML-1.0 モデルに限定する。
- **一次情報:** [Engine](https://github.com/Aivis-Project/AivisSpeech-Engine)、[Engine LICENSE](https://github.com/Aivis-Project/AivisSpeech-Engine/blob/master/LICENSE)、[ACML-1.0 本文](https://github.com/Aivis-Project/ACML/blob/master/ACML-1.0.md)、[コハク](https://hub.aivis-project.com/aivm-models/22e8ed77-94fe-4ef2-871f-a86f94e9a579)、[まお](https://hub.aivis-project.com/aivm-models/a59cb814-0083-4369-8542-f51a29e72af7)

**判定:** 推薦。固定声の日本語品質アンカー。

### 3.6 Chatterbox Multilingual

- **日本語・機能:** 公式 23 言語に日本語を含む。ゼロショットクローンと `exaggeration` 制御を持つ。全出力に PerTh 透かしを付与する。
- **ライセンス:** コードと公式モデルは MIT。参照音声の権利は別途必要。
- **12GB 根拠:** v3 の主要配布重みは T3 約2.14GB + S3Gen 約1.06GBで合計約3.2GBのため L3。ただしピーク VRAM 値は未記載。
- **Windows:** 公式検証環境は Debian 11 / Python 3.11。CUDA / CPU デバイスは選べるが Windows の記載はない。
- **訂正:** `[laugh]` 等のパラ言語タグは English Turbo の説明であり、Multilingual に同じ機能があるとは公式に確認できない。
- **一次情報:** [コードとモデル説明](https://github.com/resemble-ai/chatterbox)、[コード LICENSE](https://github.com/resemble-ai/chatterbox/blob/master/LICENSE)、[公式重み](https://huggingface.co/ResembleAI/chatterbox)

**判定:** 条件付き推薦。VRAM L3、Windows と日本語短文品質を実機確認する。

### 3.7 Supertonic 3

- **日本語・機能:** 31 言語に日本語を含む 99M の固定声モデル。10種の expression tag を持つが、オープンウェイトリポジトリに公式クローン実装はない。
- **ライセンス:** コードは MIT、重みは OpenRAIL-M。生成物に対する権利を提供者が取得するとする条項はない一方、禁止用途と AI 生成物であることの開示条件を満たす必要がある。
- **12GB 根拠:** 公式が CPU / ブラウザ / edge 実行と GPU 不要を明記するため L1。
- **Windows:** ONNX Runtime のクロスプラットフォーム実装で、Python、C#、C++ 等のローカル例を公式提供する。
- **供給リスク:** 公式 README はリポジトリのアーカイブと今後のサポート終了、Voice Builder の 2026-08-31 終了を予告している。
- **訂正:** 高速なクローン候補ではなく、固定プリセットか期限前に正当に取得した Voice Builder JSON を使う凍結ベースラインである。
- **一次情報:** [コードと終了告知](https://github.com/supertone-inc/supertonic)、[コード LICENSE](https://github.com/supertone-inc/supertonic/blob/main/LICENSE)、[公式重み](https://huggingface.co/Supertone/supertonic-3)、[重み LICENSE](https://huggingface.co/Supertone/supertonic-3/blob/main/LICENSE)

**判定:** 条件付き推薦。継続開発対象ではなく、超軽量・固定声の凍結ベースライン。

### 3.8 MOSS-TTS Local-Transformer-v1.5

- **日本語・機能:** 2026-06-18 公開の v1.5 は Qwen3-4B backbone、48kHz stereo、日本語を含む31言語、ゼロショットクローンを掲げる。旧 `MOSS-TTS-Local-Transformer` 1.7B とは別チェックポイントである。多話者対話は別モデル MOSS-TTSD の機能で、Local-v1.5 の選定理由にはできない。
- **ライセンス:** 公式 README は MOSS-TTS Family のモデルを Apache-2.0 と明記し、モデルカードにも Apache-2.0 表示がある。
- **12GB 根拠:** HF API の現行チェックポイントは 4,550,403,584 BF16 parameters、モデルファイル約8.48GiB。必須の MOSS-Audio-Tokenizer-v2 は 2,123,701,248 F32 parameters、約7.91GiB。公式 Transformers 例は tokenizer と本体を同じ CUDA device に載せる。これは VRAM 実測ではないが、序列化重みだけで12GBを超える L3 の不適合証拠である。
- **Windows:** 公式に native Windows 対応を明記せず、SGLang-Omni の参照構成も本プロジェクト環境より大きい。MossTTSDelay 8B 向け llama.cpp 低メモリ説明を Local-v1.5 に外挿できない。
- **訂正:** 暫定ショートリストの「4B」は現行 v1.5 について正しい。「8GBに収まる」「多話者対話」は誤りであり、旧1.7Bを v1.5 として扱うこともできない。
- **一次情報:** [コードとモデル一覧](https://github.com/OpenMOSS/MOSS-TTS)、[コード LICENSE](https://github.com/OpenMOSS/MOSS-TTS/blob/main/LICENSE)、[Local-v1.5 重み](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Local-Transformer-v1.5)、[Tokenizer-v2](https://huggingface.co/OpenMOSS-Team/MOSS-Audio-Tokenizer-v2)、[SGLang-Omni 手順](https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/moss_tts_local.md)

**判定:** G3 でローカル候補から除外。公式の量子化・offload 経路が公開された場合だけ再評価し、現状は必要なら RunPod 枠とする。

### 3.9 CosyVoice 3

- **日本語・機能:** `Fun-CosyVoice3-0.5B-2512` は日本語を含む9言語、ゼロショット・クロスリンガルクローン、自然言語の感情・速度・音量指示を掲げる。
- **ライセンス:** コードと公式重みは Apache-2.0。
- **12GB 根拠:** 0.5B 本体の公式配布サイズから L3。付随モジュールを含むピーク VRAM は未記載。
- **Windows:** 公式手順は Linux 系依存を前提とし、Windows の記載はない。
- **一次情報:** [コード](https://github.com/FunAudioLLM/CosyVoice)、[コード LICENSE](https://github.com/FunAudioLLM/CosyVoice/blob/main/LICENSE)、[公式重み](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512)

**判定:** 次点。Step のライセンス解消がなければ、Apache-2.0 の比較候補として昇格させる価値がある。

### 3.10 Step-Audio-EditX

- **日本語・機能:** 日本語タグを公式対応し、感情・話し方・パラ言語の編集語彙はガヤ用途に最も近い。
- **ライセンス:** GitHub README は「このオープンソースリポジトリのコード」を Apache-2.0 とするだけである。Hugging Face の通常版・AWQ 版にはライセンス表示も LICENSE ファイルもない。したがって重みの利用条件と生成音声の商用利用可否を確定できない。
- **12GB 根拠:** 公式値は標準 12〜15GB、memory-efficient 6〜8GB、AWQ 4bit 8〜10GB。AWQ は L1 で 12GB内。ただし 12GB は critical value、16GB が safer と明記される。
- **Windows:** 公式検証 OS は Linux。Windows ネイティブ手順はない。
- **訂正:** 「コード・重みとも Apache-2.0」「11.5GB 実測」は採用しない。
- **一次情報:** [コードと要件](https://github.com/stepfun-ai/Step-Audio-EditX)、[コード LICENSE](https://github.com/stepfun-ai/Step-Audio-EditX/blob/main/LICENSE)、[通常版重み](https://huggingface.co/stepfun-ai/Step-Audio-EditX)、[AWQ 重み](https://huggingface.co/stepfun-ai/Step-Audio-EditX-AWQ-4bit)

**判定:** G1 で保留。StepFun が重みの利用条件を明示するまで商用ベンチの確定候補にしない。

## 4. Claude 調査との主な相違・訂正

| 項目 | Claude 側記載 | 独立検証結果 | 影響 |
| --- | --- | --- | --- |
| Qwen3-TTS latency | 1.7B を 97ms と読める | 0.6B 97ms、1.7B 101ms | 数値訂正 |
| Qwen3-TTS GGUF | GGUF 量子化あり | 公式配布を確認できない | 12GB 根拠に使わない |
| Irodori VRAM | 約2GB | 約2.47GB は配布物容量。ピーク VRAM 不明 | L3 に格下げ |
| Irodori 利用条件 | MIT で可 | 追加利用方針と透かしあり | 台帳・表示設計に反映 |
| Step ライセンス | コード・重み Apache-2.0 | Apache-2.0 明記はコードのみ | G1 保留 |
| Step VRAM | 11.5GB / 4bit 6〜8GB | 公式は標準12〜15、memory-efficient 6〜8、AWQ 8〜10GB | AWQ 前提でも Linux のみ |
| VoxCPM2 VRAM | 約8GB | 公式 BF16 約8GB | L1 として確認 |
| GPT-SoVITS VRAM | 推論6GB | 公式値を確認できない。ただし CPU / Windows 対応あり | GPU値を削除、L1 CPU |
| MOSS Local-v1.5 | 4B、8GB最適化、多話者対話 | 4B は正しいが、必須 tokenizer と合計した配布重みだけで12GB超。多話者は別モデル | ローカル候補から除外 |
| Chatterbox v3 | 25言語、パラ言語タグあり | 現行公式は23言語。タグは English Turbo の説明 | v3機能として断定しない |
| Supertonic | 軽量クローン候補 | open-weight は固定声。開発・サポート終了予告 | 凍結ベースラインへ降格 |
| Aivis | モデルごと要確認 | 公式 ACML-1.0 モデルなら営利利用明記 | 公式モデル限定で確定 |

## 5. 未解決事項

1. StepFun に Step-Audio-EditX 通常版・AWQ 版の重みライセンスと生成物利用条件を確認する。
2. Qwen、VoxCPM2、Irodori、Chatterbox、CosyVoice は実機アダプタで `torch.cuda.max_memory_allocated()` とプロセスピークを記録する。
3. Windows ネイティブで失敗するモデルを WSL に自動退避させない。プロジェクトが WSL を採用すると決めた場合だけ別 Issue で単一経路として設計する。
4. Supertonic を現行ショートリストに残すか、Step と MOSS を外して CosyVoice を昇格するかは Director 判断を question Issue に分離する。
