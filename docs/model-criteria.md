# ベンチ対象モデル選定基準

## 必須条件

1. **日本語対応**: 日本語テキストから自然な音声を生成できる
2. **商用利用可能**: 生成音声を商用ゲームに収録できるライセンス (コード・重み・生成物を区別して確認)
3. **ローカル実行**: RTX 4070 Ti (VRAM 12GB) + RAM 32GB / Windows 11 で動作 (量子化可)
   - 例外: 品質アンカーとしてAPI型サービスを1〜2個含めてよい (要Owner承認: 課金が発生するため)
   - VRAM超過の有望モデルはRunPod枠として別トラック (M6)

## 評価優先度 (ガヤ用途)

1. 短い一言 (1〜3秒) での自然さ — 頭切れ・尻伸び・棒読みが出ないか
2. 感情・演技の振れ幅 (12 emotion × intensity への追従)
3. モブらしい声の多様性 (性別×年齢帯を出し分けられるか)
4. 難所耐性 (叫び・笑い・囁き・酔い・子供声・フィラー)
5. 生成速度 (RTF) — 大量ガヤ生成の実用性
6. 鮮度 — 2025H2以降のリリース・大型更新を優遇

## 選定プロセス

1. Claude側リサーチ (`docs/research/open-weight-tts.md`, `ja-ecosystem-rights.md`)
2. Codex独立検証 (対照調査) → 統合レポート `docs/research/models-final.md`
3. Director が6〜10モデルのショートリストを確定 → モデル別アダプタIssueを起票

## 暫定ショートリスト (2026-07-27 Director決定、#1の独立検証で最終確定)

Claude側2調査 (`research/open-weight-tts.md`, `research/ja-ecosystem-rights.md`) に基づく。
**各アダプタIssueは起票済みだが、着手は `models-final.md` (#1) の検証通過後。**

### Tier 1 (本命)

| モデル | ライセンス | 採用理由 | 検証ポイント |
| --- | --- | --- | --- |
| Qwen3-TTS 12Hz-1.7B (Base+VoiceDesign) | Apache-2.0 | VoiceDesignで権利クリーンな架空モブ声を設計→クローン固定化。ガヤ最有力 | 短文での安定性 |
| Irodori-TTS 600M-v3-VoiceDesign | MIT | 日本語専用Prosody 1位、RTF 0.13、絵文字で非言語音 | 学習データ非開示のリスク評価、かな化前処理 |
| Step-Audio-EditX | Apache-2.0 | shout/murmur/laugh等ガヤ直結タグ最強 | 日本語訛り (中国語風)、VRAM 11.5GB→4bit前提 |
| AivisSpeech (SBV2系) + ACML-1.0モデル | LGPL-3.0 + ACML | 日本語アクセント基準線。ACMLモデル (コハク/まお) は商用自由・クレジット任意 | クローン不可のためモブ多様性は他モデル頼み |

### Tier 2 (比較用)

| モデル | ライセンス | 採用理由 | 検証ポイント |
| --- | --- | --- | --- |
| VoxCPM2 | Apache-2.0 | ASR一致率1位 (=セリフ明瞭)、48kHz、声デザイン | 日本語G2P・固有名詞、抑揚の違和感 |
| GPT-SoVITS v2ProPlus | MIT | 最クリーンライセンス、5秒クローン。参照音声キット (#7) と組む | 感情タグなし→演技別参照音声で補う |
| MOSS-TTS v1.5 (4B) | Apache-2.0 | pause制御・多話者対話 (ガヤの重なり) | 日本語品質が完全未知数 |
| Chatterbox Multilingual v3 | MIT | exaggerationスカラで機械的な振れ幅生成 | 日本語★3/5、PerTh透かし |

### 次点 (Codex検証 #1 で昇格ありうる)

CosyVoice 3 / MioTTS-2.6B (年商上限Lic) / ZONOS2 (感情制御の存否要確認) / Supertonic 3 (OpenRAIL-M要法務確認)

### APIアンカー (任意・課金発生のためOwner承認待ち)

Aivis Cloud API (440円/1万文字) / ElevenLabs 有料プラン (無料枠は非商用)。承認され次第アダプタ起票。

### 参照音声の方針 (詳細: `research/ja-ecosystem-rights.md`)

- 主力: **あみたろの声素材工房 + つくよみちゃんコーパス** (AI学習・商用を規約明示許諾、クレジット表記)
- 不足域 (男性声・老人声) は自前録音で補完
- JVS/JSUT は非商用限定のため**使用禁止**。COEIROINK生成音声の学習利用も禁止規約あり
