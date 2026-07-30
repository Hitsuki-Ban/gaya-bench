# Chatterbox Multilingual V3 日本語診断プロトコル

対象 Issue: [#158](https://github.com/Hitsuki-Ban/gaya-bench/issues/158)

## 目的

公開済み Chatterbox Multilingual V3 の日本語出力について、次の三つを混同せずに
判定する。

1. 日本語参照音声から性別・年齢感・声線を保持できるか
2. 高強度 `shout` を棒読みではなく指定どおり演じられるか
3. 漢字 surface text の上流 G2P 誤りを明示 reading 入力で隔離できるか

本診断は公開データを直接差し替えない。人手判定後に、設定調整で全量再生成するか、
現状ベストを QC 注記付きで維持するかを決める。音声として成立しない場合だけ非掲載を
検討する。これは Issue 本文作成後の最新 Director / Owner コメントで承認された
公開後方針であり、本文当初の「再生成または除外」二択を更新する。

## 上流根拠

- 使用実装は Chatterbox の
  [V3 multilingual 公開 commit](https://github.com/resemble-ai/chatterbox/commit/65b18437192794391a0308a8f705b1e33e633948)
  に固定する。
- [固定 README](https://github.com/resemble-ai/chatterbox/blob/65b18437192794391a0308a8f705b1e33e633948/README.md)
  の既定値は `cfg_weight=0.5 / exaggeration=0.5` であり、dramatic / expressive
  speech には `cfg_weight≈0.3 / exaggeration≈0.7+` が案内されている。
- 同 README が `cfg_weight=0` を案内するのは参照音声と生成言語が異なる場合である。
  本診断の5参照はすべて日本語なので、最初の候補には採用しない。
- 固定実装の
  [日本語 tokenizer](https://github.com/resemble-ai/chatterbox/blob/65b18437192794391a0308a8f705b1e33e633948/src/chatterbox/models/tokenizers/tokenizer.py)
  は `pykakasi==2.3.0` で漢字を平仮名化する。実測では
  `退がれ！全員、今すぐ退がれ！` が
  `たいがれ！ぜんいん、いますぐたいがれ！` になるため、この誤読は音響生成前に
  決まる。
- [V3 公式評価](https://www.resemble.ai/resources/chatterbox-multilingual-v3-tts-with-embedded-watermarking-for-25-languages)
  の日本語 CER は 21.87% で `usable with caveats` とされる。公式説明自身が、
  CER は prosody、speaker similarity、expressive delivery を測らないとしている。

したがって、ASR の文字一致だけで本診断を合格にせず、speaker identity、演技、
厳密な日本語韻律を人手で独立評価する。

## 診断集合

固定 seed は全候補で `42`。5参照に同一の2行を与え、合計25候補を N=1 で生成する。

| line | text form | cfg | exaggeration | 5参照での件数 |
| --- | --- | ---: | ---: | ---: |
| `messenger-001` | surface | 0.5 | 0.8 | 5 |
| `messenger-001` | surface | 0.3 | 0.7 | 5 |
| `messenger-003` | surface | 0.5 | 0.8 | 5 |
| `messenger-003` | surface | 0.3 | 0.7 | 5 |
| `messenger-003` | explicit reading | 0.3 | 0.7 | 5 |

台詞:

- `messenger-001`: `伝令、前線の守りが崩れます！`
- `messenger-003` surface: `退がれ！全員、今すぐ退がれ！`
- `messenger-003` explicit reading: `サガレ！ゼンイン、イマスグサガレ！`

二つの比較だけを行う。

1. current → official candidate は `cfg_weight / exaggeration` だけを変える
2. official surface → official reading は入力文字列だけを変える

temperature `0.8`、repetition penalty `1.2`、min-p `0.05`、top-p `1.0`、
`language_id=ja`、V3 weight、PerTh watermark、後処理 v7 は全候補で同一とする。
実際の参照 WAV、入力文字列、sampling、seed、生成時間、VRAM peak、raw / normalized /
Opus SHA-256 を manifest に記録する。

参照声源:

| id | 期待 |
| --- | --- |
| `amitaro-countdown` | 明るく高めの若い女声 |
| `hadou-emotion-11` | 落ち着いた成人男性声 |
| `lux-emotion-76` | 明瞭な若い女声 |
| `sayoko-emotion-75` | 穏やかで明瞭な高齢女性声 |
| `tsukuyomi-corpus-94` | 高音ウィスパー系の10代女性キャラクター風ボイス |

## 生成境界

- canonical scenario、voice assignment、公開 manifest、R2 object は変更しない。
- production adapter の既定値は変更しない。診断では検証済みの明示 generation
  settings を渡し、欠落した設定を環境変数や既定値から補完しない。
- reading は production adapter の暗黙 fallback にしない。
  `official-reading` の synthetic `LineJob.text` 自体を明示 reading にする。
- 参照 WAV と生成音声 binary は `artifacts/issue-158/` にだけ保存し、commit /
  公開しない。
- Chatterbox 生成環境と Kana ASR 環境は dependency conflict のため分離する。
  本 Issue では QC threshold を変更せず、ASR 漏検の校正は #159 で行う。

## 盲聴

各 voice group で reference を先に提示し、生成候補の設定と text form は匿名化する。
候補音声は不透明なファイル名へ複製し、ページ読込時に SHA-256 を全件検証する。
dummy は置かない。

各候補を次の独立軸で記録する。

1. reference に対する性別・年齢・声線 / speaker identity
2. `shout` の感情、強度、指定 delivery
3. 漢字・語彙の読み（`退がれ` は必ず `さがれ`）
4. 厳密な日本語 pitch accent、重音、句調
5. 破音、ノイズ、截断などの音質
6. reference 台詞、語気、prompt 内容の leakage

演技改善で speaker mismatch を相殺しない。音素列が理論上正しくても pitch accent /
句調が不自然なら prosody は不合格にする。各行で総合ベスト、または
「無可採用候補」を選ぶ。

## 判定

- `cfg=.3 / exaggeration=.7` が複数参照・両行で speaker identity を悪化させず、
  演技を一貫して改善した場合だけ全量再生成候補にする。
- explicit reading が `たいがれ` を `さがれ` に直しても、production へ暗黙 reading
  fallback は追加しない。採用するなら別途、入力 contract を明示する設計 Issue を起こす。
- 人手で語彙誤読、機械 QC で `reading_mismatch=false` または gate pass の組を
  #159 の ASR miss 検体にする。
- 設定調整が不成立でも、ベンチの失敗特性として現状ベストを QC 注記付きで維持する。
