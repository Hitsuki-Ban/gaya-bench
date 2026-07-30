# Chatterbox Multilingual V3 日本語診断 実行記録

## 固定入力

- 実行日: 2026-07-31
- source revision:
  `7305eaca854882692f0328f325c3589da7527594`
- upstream Chatterbox revision:
  `65b18437192794391a0308a8f705b1e33e633948`
- weights revision:
  `5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18`
- seed: `42`
- language: `ja`
- device: RTX 4070 Ti 12 GB / CUDA 12.6
- runtime: Python 3.12 / Chatterbox `0.1.7` / PyTorch `2.6.0+cu126`
- diagnostic plan SHA-256:
  `a46017bbf9016c68eb4d6f967f36d79132463d5d4f05095b99bcd1edb1a79bf5`
- generator SHA-256:
  `115f27550c0ac442dd206558634a33b9abf45541bd96864f0604130f3e2a7a1c`

5参照すべてに同じ2行を与え、次の25候補を N=1 で生成した。

| line | variant | cfg | exaggeration | text form | 件数 |
| --- | --- | ---: | ---: | --- | ---: |
| `messenger-001` | current | 0.5 | 0.8 | surface | 5 |
| `messenger-001` | official | 0.3 | 0.7 | surface | 5 |
| `messenger-003` | current | 0.5 | 0.8 | surface | 5 |
| `messenger-003` | official | 0.3 | 0.7 | surface | 5 |
| `messenger-003` | official reading | 0.3 | 0.7 | explicit reading | 5 |

temperature `0.8`、repetition penalty `1.2`、min-p `0.05`、top-p `1.0`、
V3 weight、参照 WAV、PerTh watermark、後処理 v7 は全候補で固定した。
current と official の比較では cfg / exaggeration だけを変え、official surface と
official reading の比較では入力文字列だけを変えた。production adapter の既定値、
canonical scenario、公開 manifest、R2 object は変更していない。

## 生成結果

| 指標 | 結果 |
| --- | ---: |
| candidate | 25 |
| generation failure | 0 |
| raw / normalized / Opus SHA-256 unique | 25 / 25 / 25 |
| generation internal 合計 | 272.296229 s |
| generation internal 最小 / 最大 | 4.246315 / 109.777032 s |
| 最大 RTF | 29.196019 |
| Opus 音声尺合計 | 94.8025 s |
| Opus 音声尺最小 / 最大 | 2.6865 / 5.0465 s |
| runtime load peak allocated / reserved | 3074.685 / 3094 MiB |
| generation peak allocated / reserved | 3712.341 / 3878 MiB |
| encoded integrated loudness | -18.20 ～ -17.97 LUFS |
| encoded true peak | -6.51 ～ -1.42 dBTP |

生成物は `artifacts/issue-158/` のローカル証拠に限定し、音声 binary は commit
しない。

| 対象 | file 数 | byte |
| --- | ---: | ---: |
| generated Opus | 25 | 807,501 |
| raw + normalized WAV | 50 | 13,631,210 |

生成 manifest:

| artifact | record | SHA-256 |
| --- | ---: | --- |
| current manifest | 10 | `d56d6e9c98a3b892f3a409affa88e8ee8b7a7876d6595047065009a5070c4ba7` |
| official manifest | 15 | `2b75195e3e4e7c4b74a7280fec7e9429f7a7d73dd8e074a858d76b562b1fb35a` |

両 manifest は plan、generator、source revision、model / watermark file inventory、
参照音声、実入力、sampling、seed、生成時間、VRAM peak、raw / normalized / Opus
SHA-256 を記録する。

## 盲聴 bundle

25個の Opus を不透明な `candidate-001.opus` から
`candidate-025.opus` へ byte-identical に複製した。公開用設定名、cfg、
exaggeration、text form、生成元 path は blind data に含めず、answer key はページから
取得しない。

| artifact | SHA-256 |
| --- | --- |
| blind data | `e76427a7d03962742d924a695f738ac4ef451ef1d63f60230704576e107e7c05` |
| answer key | `659d62f483d1c51b38caf4e42c57ab9fad4d57c11ae527e71f7b99f57ab6511b` |
| listening page | `c6c356dce9d4455c9a6b91d1bcdd387eaa6035732c4107d4da2fd01dcd2aef22` |
| decision analyzer | `3b90d0b88fa2a5cbc5880309ad1fb82529da601b113cfcd14965aa25152d412e` |

ページには5参照と25候補、候補ごとの6軸判定、自由記述、行ごとの総合ベストを
配置した。dummy は置かない。必要入力は合計160項目で、全件入力前は完了操作を
無効化する。判断基準は次をページ上部に常時明示する。

1. reference に対する性別・年齢・声線 / speaker identity
2. 感情、強度、指定 delivery
3. 漢字・語彙の読み（`退がれ` は `さがれ`）
4. 厳密な日本語 pitch accent、重音、句調
5. 破音、ノイズ、截断などの音質
6. reference / prompt 内容の leakage

解盲 analyzer は decision、blind data、answer key の exact inventory と payload
SHA-256、25候補の全必須値、10行の総合ベスト、完了・出力 timestamp を検証する。
欠落 field、未知値、別候補集合、別行候補の選択、未完了 decision、既存 output への
上書きはすべて明示的に拒否する。正常な160/160 QA export から25 record / 10 line
decision を生成し、不完全な fixture が output を残さず失敗することを確認した。

## Browser QA

desktop `1600 × 900` と mobile `390 × 844` で次を確認した。

- 5 group、5 reference、25 candidate、160 required field が欠落なく表示される
- reference 1件と candidate 1件を実際に再生できる
- reference 5件と candidate 25件の SHA-256 が一致するまで入力を有効化しない
- answer key の network request、設定名・生成元 path の DOM 漏洩がない
- autosave と reload 復元が一致する
- 未入力箇所への移動、途中 export、160/160 後の完了 export が動作する
- data SHA-256 の不一致時は復元せず fail fast する
- desktop / mobile とも横方向 overflow、操作不能、ボタン切れがない
- console error と失敗した resource request がない

mobile QA で sticky action bar を81 pxへ縮小し、候補入力欄と重ならないように
bottom padding を維持した。

## 現在の状態

生成・盲化・機械検証・Browser QA は完了した。Owner の盲聴判断を待ち、完了後に
answer key で解盲して cfg / exaggeration、explicit reading、speaker identity、
演技を別々に集計する。人手の語彙誤読と機械 QC の見逃しは #159 の検体へ送る。
