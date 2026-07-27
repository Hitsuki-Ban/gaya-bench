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

## 確定ショートリスト (2026-07-28、#32でDirector承認)

正典は [`research/models-final.md`](research/models-final.md) (Codex独立検証 #1 とClaude調査の統合)。
実装は「波」順で進める (エピック [#8](https://github.com/Hitsuki-Ban/gaya-bench/issues/8))。

| 波 | モデル | 役割 | Issue |
| --- | --- | --- | --- |
| 1 | Qwen3-TTS 12Hz-1.7B (Base+VoiceDesign) | 声設計本命。架空モブ声の設計→クローン固定化 | #23 |
| 1 | Irodori-TTS 600M-v3-VoiceDesign | 日本語専用の声設計。かな化前処理必須 | #24 |
| 2 | AivisSpeech + ACML-1.0モデル | 日本語アクセント基準線 (固定声) | #26 |
| 2 | GPT-SoVITS v2ProPlus | 参照音声クローン基準 (MIT×MIT) | #28 |
| 3 | VoxCPM2 | 48kHz・明瞭度比較 | #27 |
| 3 | Chatterbox Multilingual v3 | 透かし付き多言語比較・exaggeration制御 | #30 |
| 4 | CosyVoice 3 0.5B | Apache軽量比較 | #35 |
| 4 | Supertonic 3 | 凍結ベースライン (固定声・CPU/ONNX。開発終了のため資産ローカル保存) | #31 |

固定声モデル (AivisSpeech / Supertonic) は「声の多様性」軸を評価対象外とし、クローン/声設計系と総合点を混ぜない。

### 保留・除外 (主なもの)

- **Step-Audio-EditX**: コードはApache-2.0だが重み・生成物の利用条件が一次情報に存在せず**ブロック** ([#25](https://github.com/Hitsuki-Ban/gaya-bench/issues/25)残置、明記され次第再開)
- **MOSS-TTS v1.5**: 4B本体+必須F32 tokenizerで12GB超のためローカル落選 (#29クローズ。RunPod枠 M6 で再評価可)
- 次点: MioTTS-2.6B / ZONOS2 (詳細は models-final.md「保留・落選」)

### APIサービスの扱い (Owner決定 2026-07-28)

**完全ローカルで動く無料モデルを最優先。オンライン有料サービス (Aivis Cloud API / ElevenLabs等) は当面お預け。**
将来アンカーが必要になった場合に改めてOwner判断を仰ぐ。

### 参照音声の方針 (詳細: `research/ja-ecosystem-rights.md`)

- 主力: **あみたろの声素材工房 + つくよみちゃんコーパス** (AI学習・商用を規約明示許諾、クレジット表記)
- 不足域 (男性声・老人声) は自前録音で補完
- JVS/JSUT は非商用限定のため**使用禁止**。COEIROINK生成音声の学習利用も禁止規約あり
