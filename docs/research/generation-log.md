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

## 2026-07-30 — 全量7モデル / 後処理 v7

- 関連: #10、#146、#148、#150、#155
- 対象: 15シナリオ、161行、`dry` variant
- 実行環境: Windows 11、NVIDIA GeForce RTX 4070 Ti 12GB、
  FFmpeg `8.1.1`
- 後処理: algorithm v7、48kHz mono、Opus 64kbps VBR、
  distribution true peak上限 -0.9 dBTP
- 自動QC環境: PyTorch `2.11.0+cu130`、CUDA 13.0、
  Transformers `5.3.0`、librosa `0.11.0`

各wall timeはledgerの`created_at`から最後の生成audio artifactのmtimeまでを
集計した。モデル初期化を含むが、後続の一括QC時間とモデル間の準備時間は含まない。
7本のmain runは合計1,121 groupを処理し、1,116件を生成、5件を
`generation_failed`として記録した。5件は独立runで再試行し、すべて生成に成功した。

### main run結果

| モデル | run id | 成功 | 失敗 | wall time | 音声尺合計 | 生成時間合計 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Chatterbox Multilingual v3 | `20260730T025550491401Z-chatterbox-multilingual-v3-n1` | 160 | 1 | 923.717秒 | 436.520秒 | 805.926秒 |
| CosyVoice3 0.5B 2512 | `20260730T031948916654Z-cosyvoice3-0.5b-2512-n1` | 159 | 2 | 1,036.375秒 | 434.754秒 | 873.575秒 |
| GPT-SoVITS v2 Pro Plus | `20260730T034754379749Z-gpt-sovits-v2-pro-plus-n1` | 161 | 0 | 400.118秒 | 505.666秒 | 292.268秒 |
| Supertonic 3 | `20260730T035734579144Z-supertonic-3-n1` | 161 | 0 | 264.672秒 | 402.009秒 | 153.872秒 |
| VoxCPM2 | `20260730T044327885372Z-voxcpm2-n1` | 159 | 2 | 2,231.379秒 | 602.473秒 | 2,002.567秒 |
| Irodori TTS 600M v3 VoiceDesign | `20260730T053323378341Z-irodori-tts-600m-v3-voicedesign-n1` | 161 | 0 | 662.595秒 | 651.567秒 | 532.169秒 |
| AivisSpeech / Kohaku | `20260730T054806556612Z-aivisspeech-kohaku-n1` | 161 | 0 | 337.341秒 | 458.615秒 | 219.784秒 |

### 性能・資源

| モデル | RTF 最小 / 中央値 / 平均 | RTF P95 / 最大 | peak allocated / reserved |
| --- | ---: | ---: | ---: |
| Chatterbox | 1.543 / 1.774 / 1.894 | 2.088 / 17.822 | 3,734.342 / 3,968 MiB |
| CosyVoice3 | 1.693 / 1.962 / 2.057 | 2.703 / 3.718 | 4,289.491 / 5,256 MiB |
| GPT-SoVITS | 0.457 / 0.566 / 0.591 | 0.722 / 2.345 | 1,745.761 / 1,794 MiB |
| Supertonic | 0.304 / 0.383 / 0.388 | 0.465 / 0.541 | CPU / ONNX Runtime |
| VoxCPM2 | 2.802 / 3.226 / 3.342 | 4.046 / 6.325 | 6,645.411 / 8,696 MiB |
| Irodori | 0.457 / 0.800 / 0.864 | 1.270 / 6.102 | 2,983.994 / 5,446 MiB |
| AivisSpeech | 0.390 / 0.468 / 0.484 | 0.586 / 0.655 | CPU、Engine working set最大2,556.8 MiB |

peak値は各sidecarの`phase_peak_vram_mib`の最大値。Chatterboxと
GPT-SoVITSはそれぞれの隔離runtime、CosyVoice3はCUDA 12.1、
VoxCPM2はPyTorch `2.10.0+cu130`、IrodoriはPyTorch `2.10.0`を使用した。
AivisSpeech Engineは`--no-use_gpu`で起動し、終了後にport 10101が閉じたことを
確認した。

### 固定したupstream

- Chatterbox:
  `resemble-ai/chatterbox@65b18437192794391a0308a8f705b1e33e633948`、
  weights `ResembleAI/chatterbox@5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18`
- CosyVoice3:
  `QwenAudio/CosyVoice@074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc`、
  weights `FunAudioLLM/Fun-CosyVoice3-0.5B-2512@29e01c4e8d000f4bcd70751be16fa94bf3d85a18`
- GPT-SoVITS:
  `RVC-Boss/GPT-SoVITS@d523079fc05d9a8028d6085bffe4a2757c32abb6`、
  weights `lj1995/GPT-SoVITS@336b2ec4e8d4ac74740798dd40af44e74659ecaf`
- Supertonic:
  `supertone-inc/supertonic@7e2804f96016a7028cb1ed627353c61c1e9dd281`、
  weights `Supertone/supertonic-3@724fb5abbf5502583fb520898d45929e62f02c0b`
- VoxCPM2:
  `OpenBMB/VoxCPM@616d3d3e630a9c96c2853250eef91b0f39dcd5fa`、
  weights `openbmb/VoxCPM2@bffb3df5a29440629464e5e839f4d214c8714c3d`
- Irodori:
  upstream `eaf74d6a19138f743acb5b71a445fd25a57db987`、
  checkpoint `e863a3a93e652e09afeff3e84823a206a0a60314`
- AivisSpeech Engine `1.2.0`、Kohaku model UUID
  `22e8ed77-94fe-4ef2-871f-a86f94e9a579`、model version `1.1.0`

### 自動QC

すべてのmain / retry runでhard reject 0、blocked 0、pending 0だった。
main runの`content_review_required`はChatterbox 149、CosyVoice3 154、
GPT-SoVITS 156、Supertonic 149、VoxCPM2 157、Irodori 153、
AivisSpeech 155。これは自動読みによる要確認フラグであり、人手策展の
合否そのものではない。

Opusのsoft loudness target外はmain run合計33件
（Chatterbox 8、CosyVoice3 4、GPT-SoVITS 6、Supertonic 13、
VoxCPM2 0、Irodori 1、AivisSpeech 1）。すべてhard gate内で、
`shortfall: true`として保持した。

### 失敗と独立再試行

| モデル | main runの失敗group | retry run | 結果 |
| --- | --- | --- | --- |
| Chatterbox | `chinatown-street/tenshin-okami-002` | `20260730T031215286743Z-chatterbox-multilingual-v3-n1` | eligible |
| CosyVoice3 | `battlefield-camp/wounded-001` | `20260730T033921577682Z-cosyvoice3-0.5b-2512-n1` | eligible |
| CosyVoice3 | `west-crowd/isogi-shinshi-002` | `20260730T034055511382Z-cosyvoice3-0.5b-2512-n1` | eligible |
| VoxCPM2 | `goblin-camp/goblin-cook-001` | `20260730T052150169309Z-voxcpm2-n1` | eligible |
| VoxCPM2 | `spirit-forest/pixie-003` | `20260730T052357036742Z-voxcpm2-n1` | eligible |

production releaseはmodelごとに単一terminal runを権威入力とするため、retry runを
main runへ暗黙合成しない。main runの5件は明示的なfailureとして残し、retryは
再現・診断用の独立evidenceとして保持する。

### Qwen保持投影・R2 publish

Qwenは`20260729T113009679952Z-qwen3-tts-12hz-1.7b-n1`の確定済み
baselineを再生成せず使用する。現行161行のうち旧sourceにない
`spirit-forest/spring-sprite-002`だけを`no_eligible_take`として明示する
projection planを固定した。人手策展、aggregate release確定、R2 publishと
公開URLのhash照合は未完了であり、完了後に本節へ実測結果を追記する。
