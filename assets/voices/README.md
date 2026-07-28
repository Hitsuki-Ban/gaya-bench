# 参照音声キット

音声クローン対応モデルへ渡す、権利確認済みのローカル参照音声を管理する。
Git が管理するのは `metadata.yaml`、スキーマ、手順だけであり、`reference.wav`
は管理しない。第三者素材の原音・加工原音を GitHub や公開 R2 へ再配布しては
ならない。

## 配置と検証

各素材を公式配布元から取得し、台帳の `processing.source_member` を
`assets/voices/<id>/reference.wav` へ次の形式で変換する。

```powershell
ffmpeg -hide_banner -loglevel error -y -i <source.wav> `
  -ac 1 -ar 48000 -c:a pcm_s16le `
  assets/voices/<id>/reference.wav
uv run --project pipeline gaya voices validate-local
```

検証はメタデータの strict schema、ID 重複、固定パス、SHA-256、
PCM 16-bit、mono、48 kHz、10〜20秒、記録 duration をすべて照合する。
ファイルがなければ失敗し、取得や代替素材への自動フォールバックは行わない。

## 素材一覧

| ID | 話者属性 | 取得元 | 再配布 |
| --- | --- | --- | --- |
| `amitaro-countdown` | female / young_adult（聴感分類） | [あみたろの声素材工房](https://amitaro.net/voice/voice_dl/) | 条件付き。公開 R2 には置かない |
| `hadou-emotion-11` | male / adult（聴感分類） | [Hadou Voice Dataset](https://huggingface.co/datasets/hadou1225/Hadou-Voice-Dataset) | 禁止 |
| `lux-emotion-76` | female / young_adult（聴感分類） | [Lux Japanese Speech Corpus](https://huggingface.co/datasets/Lami/Lux-Japanese-Speech-Corpus) | CC BY 4.0。リポジトリ方針で非公開 |
| `sayoko-emotion-75` | female / elderly（収録時81歳） | [サヨ子音声コーパス](https://huggingface.co/datasets/bandad/sayoko-tts-corpus) | CC BY 4.0。リポジトリ方針で非公開 |
| `tsukuyomi-corpus-94` | female / teen（公式14歳前後） | [つくよみちゃんコーパス](https://tyc.rei-yumesaki.net/material/corpus/) | 禁止 |

権利根拠、検証日、配布ファイルと変換前後のハッシュ、台本、表示用
クレジット全文は `metadata.yaml` を正とする。音声の `rights` と台本文字の
`transcript_rights` は別の権利として扱う。ITA コーパスの台本は
パブリックドメイン（Unlicense）、`tsukuyomi-corpus-94` の
`transcript` は CC BY-SA 4.0 であり、当該台本文字には台帳記載の表示・
継承条件が適用される。利用規約は変更され得るため、素材を再取得・公開利用
する時点でも一次配布ページを再確認する。
台帳のクレジットは v1 の生成音声公開用である。音声合成ソフト・モデル・API
自体を配布する場合は用途が異なるため、各規約の指定文と利用制限を改めて確認する。

現時点では male / adult と female / teen・young_adult・elderly をカバーする。
権利と 10〜20秒の要件を同時に満たす child、neutral、middle_aged、
male elderly の素材は確認できていないため、属性を推測して水増ししない。

## シナリオからの参照

`metadata.yaml` の `id` を character の `reference_voice` に記述する。
`gaya validate` は未知 ID を拒否する。参照 WAV を実際に消費し、その SHA を
生成キャッシュへ含める責務は、クローン対応モデルアダプタ側にある。
