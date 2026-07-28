# バッチ生成ログ

モデル別の全シナリオ生成について、実行環境、性能、失敗、R2公開結果を記録する。

## 2026-07-28 — Qwen3-TTS 12Hz 1.7B / 後処理 v4

- 関連: #10、#71
- 対象: 15シナリオ、161行、`dry` variant
- モデル: `qwen-tts 0.1.1`
  - Base revision: `fd4b254389122332181a7c3db7f27e918eec64e3`
  - VoiceDesign revision: `5ecdb67327fd37bb2e042aab12ff7391903235d3`
- 実行環境: Windows 11、NVIDIA GeForce RTX 4070 Ti 12GB、PyTorch `2.11.0+cu130`、FFmpeg `8.1.1`
- attention backend: `sdpa`（`flash-attn` なし）
- 後処理: algorithm v4、48kHz mono、Opus 64kbps VBR、format bitexact
- 実行時刻: 2026-07-28 17:44:09–18:30:51 JST

### 結果

| 指標 | 実測 |
| --- | ---: |
| 成功 | 160 / 161 |
| 失敗 | 1 / 161（0.62%） |
| wall time | 2,801.512秒（46分41.5秒） |
| 成功音声の合計尺 | 453.601秒 |
| 生成処理時間の合計 | 2,679.042秒 |
| RTF 最小 / 中央値 / 平均 | 4.58 / 6.01 / 5.95 |
| RTF P95 / 最大 | 7.07 / 9.32 |
| PyTorch CUDA peak allocated | 4,286.782 MiB |
| PyTorch CUDA peak reserved | 4,674 MiB |

VRAM値は各 sidecar の `phase_peak_vram_mib` を集計した。最大値はいずれも
`voice_clone_generate` で記録された。補助的な `nvidia-smi` の定点確認では、
GPU全体の使用量は最大 8,115 MiB だった。

成功160件の sidecar はすべて後処理 algorithm v4 で生成された。manifest の
Qwen結果は成功160件と失敗1件で全161行を過不足なく覆う。soft target
（-18±0.2 LUFS）を外れた成功クリップは `castle-gate/guard-onna-003`
（I=-18.57 LUFS、TP=-0.94 dBTP）の1件で、`shortfall: true` として記録した。

### 失敗と再試行

`spirit-forest/spring-sprite-002` は全量ランと独立再試行の両方で同じ品質門に
失敗した。

```text
I=-18.12 LUFS (target=-18±1.5), TP=-0.89 dBTP (max=-0.9)
```

seed固定で同じ結果を再現したため瞬発的な失敗ではない。許容値を緩めず、
manifest には公開安全な `generation_failed` として残した。

### R2 publish・公開確認

- publish: アップロード160件、スキップ161件
  - Qwen: 160件アップロード
  - dummy: 161件を遠端hash一致でスキップ
- アップロード量: 3,859,329 bytes
- 公開確認:
  `https://audio.gaya-bench.hitsuki.space/audio/qwen3-tts-12hz-1.7b/castle-gate/guard-onna-003-dry.opus`
  - HTTP 200
  - `Content-Type: audio/ogg`
  - 21,551 bytes
  - SHA-256:
    `01727182ea75fdabd688e16be81cb375b018d23357d2473b62b909c364e82ae9`
  - manifest のSHA-256と一致
