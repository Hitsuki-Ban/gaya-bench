# Kana ASR 漏検校正（#159）

## 結論

#158 で人手確認した Chatterbox 冒頭7件を、同じ公開 Opus と固定 Kana Whisper
revision で再実行した。Issue に残る人手証拠は「冒頭7件の語彙誤読、うち自動検出
2件・差分5件」というバッチ集計で、逐条 decision export は残っていない。

production policy は変更しない。明示 reading と Kana ASR の normalized exact
match は引き続き監査可能な soft review signal とし、hard reject、別 ASR、閾値、
自動読み替えを追加しない。自動判定が pass だった5件は ASR transcript 自体が
期待 reading と一致し、Kana-CER も0なので、距離閾値を変えても検出できない。

## 再現資料

- [`cases.json`](cases.json): 公開 candidate identity、音声 SHA-256、期待 reading、
  Chatterbox 上流 G2P、固定 Kana ASR transcript、現行判定、人手のバッチ集計
- [`report.json`](report.json): fixture から再計算した normalized reading、Kana-CER、
  判定整合性、漏検、意味変更 target と集計
- 音声 binary は commit しない。`audio_sha256` は `data/manifest.json` の公開 Opus
  とローカル再取得物を照合済み

次のコマンドは入力を厳密検証し、新規 output に決定的な JSON report を作る。
既存 output、未知 field、重複 identity、判定矛盾、不正 hash は fail-fast する。

```console
uv run --project pipeline --locked python -m gaya_pipeline.kana_asr_calibration \
  --input docs/research/kana-asr-calibration/cases.json \
  --output docs/research/kana-asr-calibration/report-check.json
```

## 観測結果

| line | expected | 上流 G2P | Kana ASR | 現行判定 |
| --- | --- | --- | --- | --- |
| `medic-001` | アッパクシテ… | あっぱくして… | アッパクシテ… | pass |
| `medic-002` | オネガイ… | おねがい… | オネガイ… | pass |
| `medic-003` | ホータイガ… | ほうたいが… | ホータイガ… | pass |
| `medic-004` | ダイジョーブ… | だいじょうぶ… | ンダイジョーブスバ… | review |
| `messenger-001` | デンレー… | でんれい… | デンレー… | pass |
| `messenger-002` | カズガオー… | かずがおお… | カズガオー… | pass |
| `messenger-003` | サガレ… | **たいがれ…** | シリソガレ… | review |

`退がれ` は語義を保つ期待読みが `サガレ` なのに、固定 upstream tokenizer が
`pykakasi==2.3.0` の出力 `たいがれ` をモデルへ送る。これは生成前に確定する
漢字誤読であり、fixture は `退がれ: サガレ -> タイガレ` の target だけを明示的に
`meaning_changed` と注記する。一般の Kana 差分から語義変更を自動推定しない。

## 原因と境界

現行 content gate は normalized Kana の完全一致であり、調整対象となる数値閾値は
ない。Kana Whisper は片仮名 transcript と集計 Kana-CER の評価用モデルであり、
1音声ごとの人手判断を置き換える保証はない。強い演技や縮約では、人には理解できる
音声を Kana ASR が別表記にすることも、文脈から期待語へ戻して漏検することもある。

Chatterbox の固定 tokenizer は `language_id="ja"` で漢字を pykakasi により平仮名化
する。production adapter は契約どおり `line.text` を渡し、`line.reading` を暗黙利用
しない。#158 の診断では explicit-reading 入力が `退がれ` を直せることを確認したが、
production の入力契約変更は #159 の gate 校正とは別スコープである。

本 fixture は人手証拠をバッチ集計としてだけ保持し、個別 case に人手 outcome を
割り当てない。したがって5件が具体的に何と発音されたか、逐条でどの誤読だったかは
主張しない。また同じ Kana 列でも誤り得る日本語 pitch accent は本監査の対象外で、
独立した聴取・音響評価が必要である。

## 出典

- [Kana Whisper model card](https://huggingface.co/sbintuitions/kana-whisper)
- [Chatterbox 固定 tokenizer 実装](https://github.com/resemble-ai/chatterbox/blob/65b18437192794391a0308a8f705b1e33e633948/src/chatterbox/models/tokenizers/tokenizer.py)
- [Chatterbox 固定生成経路](https://github.com/resemble-ai/chatterbox/blob/65b18437192794391a0308a8f705b1e33e633948/src/chatterbox/mtl_tts.py)
- [Joyo Kanji Yomi Benchmark](https://github.com/sbintuitions/Joyo-Kanji-Yomi-Benchmark)
- [Sarashina2.2-TTS](https://arxiv.org/abs/2606.25369)
- [Whisper](https://arxiv.org/abs/2212.04356)
- [#158 人手聴取差分の記録](https://github.com/Hitsuki-Ban/gaya-bench/issues/158#issuecomment-5131323334)
