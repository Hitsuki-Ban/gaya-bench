# Qwen3-TTS 感情参照 bank blind A/B

検証日: 2026-07-30

対象 Issue: [#96](https://github.com/Hitsuki-Ban/gaya-bench/issues/96)

## 結論

**不合格。** Qwen3-TTS の `emotion` capability は `false` のまま維持し、#10 の Qwen 全量生成は開始しない。

- neutral は旧 character-only neutral reference の A が選ばれ、感情参照 bank の B は reading 不正と判定された。
- angry は A / B が同等で、両方とも reading 不正と判定された。
- whisper は A が選ばれ、B は演技 preference を改善しなかった。両方とも reading 不正だった。
- 全6候補で prompt / reference prosody leakage は `none` だった。
- identity は全候補で `uncertain` だった。自由記述では、女衛兵という仕様に対して全候補が男性声に聞こえ、行間でも声線が一致しないと報告された。したがって「読み・声質を悪化させず angry / whisper の少なくとも一方を改善する」という合格条件を満たさない。

感情別 reference を増やす現行方式は、[公式モデルカード](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign) が案内する VoiceDesign → Base clone の再利用経路そのものではある。しかし本実験では、emotion ごとに別 reference を作ることで同一キャラクターの声を維持できる証拠が得られなかった。実験経路はコードに残すが、production capability と全量生成には使用しない。

コード上は VoiceDesign instruct に構造化された `character.gender` / `age` が含まれず、emotion ごとの reference に共有の音響的 speaker anchor もない。これは本結果から判明した別スコープの再設計課題であり、[#142](https://github.com/Hitsuki-Ban/gaya-bench/issues/142) で canary から検証する。

## 条件

A は 2026-07-28 に生成した旧 character-only neutral reference の公開音声、B は PR #95 の `(scenario, character, emotion, intensity)` bank 実装で新規生成した音声である。

- model: `qwen3-tts-12hz-1.7b`
- code base: `08f3216f1a734b8049a823dd0a80e9e2394bfbcf`
- Base revision: `fd4b254389122332181a7c3db7f27e918eec64e3`
- VoiceDesign revision: `5ecdb67327fd37bb2e042aab12ff7391903235d3`
- `qwen-tts==0.1.1`, `torch==2.11.0+cu130`
- Windows 11 / RTX 4070 Ti 12GB / BF16 / SDPA
- A / B とも実際に model へ渡した seed は `0`
- postprocess は両条件とも algorithm v7、48kHz mono PCM / 64kbps Opus、目標 -18 LUFS

対象は同じ `guard-onna` の3行である。

| line | emotion / intensity | text |
| --- | --- | --- |
| `guard-onna-001` | neutral / 2 | 身分を明かしなさい。 |
| `guard-onna-002` | angry / 2 | 怪しい動きをするな！ |
| `guard-onna-003` | whisper / 1 | ……ふぁ……いかん、いかん…… |

blind 画面には、演技 preference、文字読みと厳密な日本語 pitch accent、同一キャラクターの声線、参照尾句 / prosody leakage の4基準を明示した。左右条件は投票完了まで開示しなかった。生の投票記録は [`blind-decision.json`](blind-decision.json) に保存した。

## 解盲結果

| line | 左 | 右 | blind preference | 解釈 |
| --- | --- | --- | --- | --- |
| `guard-onna-001` | B | A | right | A 優位。B の reading が悪化 |
| `guard-onna-002` | A | B | equal | B の改善なし。両方 reading 不正 |
| `guard-onna-003` | B | A | right | A 優位。B の改善なし |

reading の判定は、文字列が概ね読めるだけでは合格とせず、発話意図と厳密な日本語の音調も含む。Kana-Whisper の機械転写は次のとおりで、文字列レベルの近似確認には使えたが、人評で検出した語調・pitch accent・演技上の誤りは検出できなかった。

| line | A transcript | B transcript |
| --- | --- | --- |
| `guard-onna-001` | ミブンオアカシナサイ | ミブンオアカシナサイ |
| `guard-onna-002` | アヤシーウゴキオスルナ | アヤシーウゴキオスルナ |
| `guard-onna-003` | ファ、イカン、イカン | フワ、イカン、イカン |

## 音声 provenance

音声 binary は repository に commit せず、ローカルの `artifacts/issue-96/` に保存した。worktree 削除前に主 checkout へコピーし、27ファイルすべてでコピー元とコピー先の SHA-256 一致を確認した。

| line | A Opus SHA-256 | B Opus SHA-256 |
| --- | --- | --- |
| `guard-onna-001` | `546d9b0e1b6f4a543566a45b93fccf8517d1d981f2af962b65d6cb81994dd85c` | `4e5809bf925d756a8645f0c3bdc434e2199ea5d1e3c50d8fdb1f36084380b6fc` |
| `guard-onna-002` | `2c4b61243b8f263854435f215b7276b5e4a7c3a045437bc9b0c46256b4e1d553` | `1c75ac05c36567171c7a606fc46c6fea94361edfe2d6e1252480225205bf7de1` |
| `guard-onna-003` | `0d09d4bd6bed63a8353ac1ef752b1367788d3d6065eed9546126768e0c1b275a` | `2a4fcbd0b25fc4456eeb5f73201ae41106d07bbb9ab61f1c2ae2c85abad55264` |

B の reference SHA-256 は neutral `0dff28b83066c67fcdb6ef204d1e9fdad19bf9ad8857a5d1778e72c7d1afce94`、angry `e06db930828ca8e636bbd78a07f6432cb68043f6454277c8fe6aee539cf7868a`、whisper `4a6541097b2ba01f92fda4612f8a5ee2905f57b2296e2f80a2f5f2601feb3fca` である。

## VRAM と時間

B の3 reference 準備は 62.228秒だった。Base の cold load を含む最初の B は38.571秒、以後の B は8.551秒 / 18.985秒だった。

| line | A generation | B generation | A peak allocated / reserved | B peak allocated / reserved |
| --- | ---: | ---: | ---: | ---: |
| `guard-onna-001` | 9.525秒 | 38.571秒 | 4,150.321 / 4,310 MiB | 4,150.200 / 4,204 MiB |
| `guard-onna-002` | 11.676秒 | 8.551秒 | 4,161.911 / 4,310 MiB | 4,157.356 / 4,204 MiB |
| `guard-onna-003` | 15.326秒 | 18.985秒 | 4,177.346 / 4,310 MiB | 4,201.430 / 4,302 MiB |

12GB VRAM gate は通過したが、品質 gate が不合格なので全量 bank の時間・容量見積もりは実行許可にならない。
