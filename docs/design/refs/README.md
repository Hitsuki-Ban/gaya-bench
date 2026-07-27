# デザイン参考画像

Issue #2 の UI 方向性比較用に、Codex built-in image generation で 2026-07-28 に生成した。いずれも 16:9 desktop の高忠実度 UI mock であり、既存サービスの画面を合成していない。

| file | prompt direction | signature |
| --- | --- | --- |
| `01-tactical-console.png` | RPG tactical console × 8-model audio QA matrix | active 行を横断する amber scan line |
| `02-stage-mixer.png` | broadcast / stage mixer | 4 model channel と単一 SOLO |
| `03-archive-ledger.png` | research archive ledger × audio catalogue | 展開 record の waveform contact sheet |
| `04-spectral-map.png` | spectral cartography × blind A/B comparison | 匿名候補と mint-green contour |

## 共通 prompt 制約

- 製品: 日本語 RPG モブ NPC 音声の TTS 比較ベンチ `GAYA BENCH`
- 内容: コンテンツ計画を題材にした例示 scenario、character、dialogue。実シナリオの内容検証には使用しない
- モデル: `Model A`–`H` と capability 状態も情報設計・密度確認用の placeholder。確定モデルや対応機能の根拠には使用しない
- UI: 通常比較は行 = セリフ、列 = 4 または 8 model。A/B は匿名 2 候補。単一再生、bottom transport、keyboard 導線
- 品質: 実装可能な React / shadcn UI、正面 desktop viewport、長い日本語を読めること
- 禁止: browser chrome、watermark、marketing copy、glassmorphism、無意味な装飾 control、既存 logo

方向別の最終 prompt は、上表の signature を 1 つだけ強い視覚要素として指定し、色・type・layout をそれぞれ変えた。評価と推奨 token は [UX 調査](../../research/ux-survey.md#方向性比較) を参照。
