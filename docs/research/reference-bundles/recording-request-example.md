# 契約収録依頼の例

この例は `assets/reference-bundles/schema/recording-request-v1.schema.json` に通る
機械可読な依頼書である。収録対象は架空の child / creature persona であり、
演者は 18 歳以上の成人とする。実在人物・既存キャラクターの声まねは依頼しない。

```json
{
  "schema_version": "recording-request-v1",
  "request_id": "commissioned-child-creature-01",
  "target_bundle_id": "commissioned-child-creature-01",
  "persona": {
    "kind": "creature",
    "locale": "ja-JP",
    "perceived_gender": "neutral",
    "perceived_age": "child",
    "voice_characteristics": "小柄な森の魔物。甲高くざらつくが、無理な喉締めを避けた声"
  },
  "requested_rights": {
    "tts_reference_inference": "requested",
    "training_or_finetuning": "not_requested",
    "commercial_generated_output": "requested",
    "audio_redistribution": "not_requested",
    "transcript_redistribution": "requested"
  },
  "scripts": {
    "general": {
      "script_id": "child-creature-general",
      "transcript": {
        "text": "ギィッ、火のにおいだ。人間どもが、また森へ入ってきたぞ。枝を鳴らせ、仲間に知らせろ。",
        "utf8_sha256": "688a9baccf9f4c2ba8542bd45688b34233b9e0d4530c2320c82a91f786955eb9"
      },
      "duration_ms": {
        "minimum": 10200,
        "maximum": 14000
      },
      "takes": 3
    },
    "short_clone": {
      "script_id": "child-creature-short-clone",
      "transcript": {
        "text": "ギィッ、人間どもが森へ入ってきたぞ。",
        "utf8_sha256": "9af066b42e0c4eb62dfe16def917a6285f3c10dda642d2f91c342d8e46df764e"
      },
      "duration_ms": {
        "minimum": 5000,
        "maximum": 5000
      },
      "takes": 3
    }
  },
  "delivery_pcm": {
    "encoding": "pcm_s16le",
    "sample_rate_hz": 48000,
    "channels": 1
  },
  "recording_quality": {
    "max_noise_floor_dbfs": -60,
    "max_peak_dbfs": -3,
    "room_tone_seconds": 0.2,
    "post_processing": "raw_only"
  },
  "due_on": "2026-09-30"
}
```

## 納品条件

- `general` と `short_clone` は別台本・別 take とし、前者から後者を切り出さない。
- `general` は 10–20 秒、`short_clone` は厳密に 5.000 秒とする。
- 各 clip は PCM16 / 48 kHz / mono の dry WAV とし、BGM、効果音、残響、
  denoise、limiter、time stretch を加えない。
- 台本を一字一句変えずに読み、採用 take ごとに音声と transcript の組を維持する。
- 苦痛や喉締めを伴う発声は行わず、安全に再現できる演技だけを採用する。

`general` と `short_clone` は異なる model 条件を満たす独立 clip である。
Chatterbox の現 adapter は 10 秒以上を要求し、GPT-SoVITS 用 clip は厳密に
5.000 秒でなければならない。Phase 2 の asset validator は採用音声を
PCM16 / 48 kHz / mono、尺、hash まで検証する。

音声利用権と台本利用権は別契約である。`audio_redistribution` を
`not_requested` としても、公開 site に台本を掲載するなら
`transcript_redistribution` を明示的に `requested` とする。収録完了後の bundle
では、契約証跡に基づいて音声の `rights.permissions` と各 clip の
`transcript.rights` を別々に記録する。
