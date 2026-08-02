# Issue #190 公開ベースライン品質追加調査

- **調査日:** 2026-08-02
- **対象 release:** candidate set `5287cee156f8212c8249f202931717dc4ef448410fc3c83371650b6d5ff28fdd`
- **状態:** 定量調査・blind listening 完了。契約変更は未採用。

## 結論

1. **Irodori は全体として速すぎるのではなく、他 model より長い。** 161 行の
   overall mora/sec 中央値は `3.664`、同一行の他 model 中央値に対する話速比は
   `0.802`、duration 比は `1.246` だった。一方、speaker 内正規化後の intensity と
   duration / F0 / energy の相関はすべて `|r| <= 0.061` であり、強度指定を一律に
   弱めれば直る現象ではない。
2. **Irodori caption の全体短縮は採用根拠がない。** same-seed 12 行で current から
   role+emotion または role-only へ短縮しても、duration 比中央値はそれぞれ
   `0.994` / `0.983` に留まり、F0 range と energy の変化方向は行ごとに反転した。
   role-only は末端 F0 interval も最大 `+5.65 st` 動いた。短縮を global contract に
   すると別の演技崩れを導入しうる。
3. **Supertonic は相対的に速い。** 161 行の mora/sec 中央値は `5.247`、同一行の
   他 model に対する話速比中央値は `1.191`、161 行中 52 行で最速だった。adapter は
   全行で公式例・SDK default と同じ `speed=1.05` を実現しており、設定漏れではない。
   `1.00` A/B は 10 style すべてで duration を増やし、比の中央値は `1.051` だった。
   それでも単純推定の相対話速比は約 `1.13` であり、1.00 は現象を軽減するが消さない。
4. **旧 release の Irodori / VoxCPM 全文カタカナ経路と、現行 release の問題は分離済み。**
   前者は #177 で除去済み。現行 AivisSpeech は明示 reading 25 行だけを
   `accent_phrases` として渡し、残り 136 行は surface text から engine G2P を使う。
   CosyVoice は明示 reading 25 行と自動かな 136 行の双方を全文かなとして渡す。
5. **AivisSpeech の reading A/B は engine 自身の非決定性と分離できない。** explicit
   reading 同条件3回でも duration 比は最小 `0.897`、末端 F0 interval は
   `-2.0..+2.55 st` 動いた。surface G2P と explicit reading の集計中央値はほぼ同じで、
   reading 起因の一方向の悪化は示せなかった。
6. **CosyVoice は入力表記で発話軌跡が大きく変わる。** explicit reading 25 行で
   surface text / full-kana の duration 比は `0.578..1.493`、F0 p10-p90 range 差は
   `-34.0..+29.55 st`、末端 F0 interval 差は `-6.00..+5.60 st` だった。方向が一様で
   ないため、自動的に surface text へ戻す根拠にも全文かなを維持する根拠にもならず、
   同一行 blind listening が必要である。
7. **blind listening は global contract の変更を支持しなかった。** CosyVoice は
   10行中9行で current explicit reading が選ばれ、surface textへの変更を棄却した。
   Irodori caption短縮とSupertonic `speed=1.00` もcurrentを上回らなかった。

## Track A: Irodori

### 全量分布

| 指標 | n | 中央値 | p10 | p90 |
| --- | ---: | ---: | ---: | ---: |
| active mora/sec | 161 | 5.335 | 3.943 | 6.836 |
| active mora duration proxy (ms) | 161 | 187.429 | 146.286 | 253.600 |
| duration / 同一行の他 model 中央値 | 161 | 1.246 | 1.084 | 1.538 |
| F0 p10-p90 range (st) | 161 | 10.660 | 5.300 | 32.960 |
| F0 semitone std | 161 | 4.537 | 2.261 | 13.017 |
| energy p95-median (dB) | 161 | 11.754 | 6.183 | 27.034 |

末端 F0 は 145/161 行で測定でき、interval 中央値は `-0.45 st`、p10/p90 は
`-4.82 / +2.38 st`、2 st rise anchor 該当は18行だった。無声終止等の16行は
`unavailable` のまま保持した。これは report-only で、行ごとの期待終止を無視した
quality gate には使わない。

### 母音持続時間の証拠境界

`active_mora_duration_ms` は active speech / mora 数の平均であり、**母音持続時間ではない**。
公開 QC は phoneme boundary を持たないため、本調査では母音外れ値を実測済みとは扱わない。

日本語 phone alignment の本命は Japanese MFA acoustic model である。公式用途は日本語
forced alignment で、MFA 3.0 の2026年比較は日本語を含む benchmark で平均 boundary
error 15 ms 未満を報告している。ただし現行公式 Windows installation は Conda/Kaldi を
要求し、本 repository の `uv only / conda禁止` と両立しない。また model card は成人音声で
学習され、子供声への精度を保証しない。したがって、未固定 runtime で得た境界を
「実測母音」として混入せず、別途 runtime 固定と層別 spot check を完了してから追補する。
この追補は [#191](https://github.com/Hitsuki-Ban/gaya-bench/issues/191) で追跡する。

### 提案

- current caption contract は維持する。
- blind listening で短縮案の優位性が出なかったため、global短縮を棄却する。
- 問題行は caption global変更ではなくN>=3のtargeted再生成と人選で扱う。

## Track B: Supertonic

adapter は `speed=1.05`、`lang=ja`、`total_steps=8` を明示し、10 preset style に hidden
speed はない。公式 SDK は `0.7..2.0` を許容し、`1.0` を normal と説明する一方、example と
default は `1.05` である。1.00 A/B の duration 比は `1.029..1.065`、中央値 `1.051`。
末端 F0 interval 差中央値は `+0.15 st` で、rise anchor は測定可能な9行すべて false のまま
だったが、F0 range と energy は style ごとに変化した。

### 提案

- 単純な adapter bug として #177 release を差し替えないという Director 判断を維持する。
- blind listening で `1.00` の優位性が出なかったためcurrent `1.05`を維持する。
- time-stretch 後処理は採用しない。

## Track C: reading 適用

| model | 現行 transport | 定量結果 | 暫定判断 |
| --- | --- | --- | --- |
| Irodori | surface text | 旧全文かな経路は #177 で除去 | 変更不要 |
| VoxCPM2 | surface text | 旧全文かな経路は #177 で除去 | 変更不要 |
| AivisSpeech | explicit 25行だけ accent_phrases、他136行は engine G2P | condition 差が同条件揺らぎと重なる | 現契約維持、blind listening で再確認 |
| CosyVoice | explicit 25行 + 自動かな136行を full-kana text として入力 | duration / F0 trajectory が大幅に変化、方向不定 | blind listening の主対象 |

PASQA は同一行・同一 model・**同一 variant 内の N take ranking 専用**として固定されている。
絶対閾値、model 間、variant 間の score 比較は禁止であり、今回の surface/full-kana A/B の
効果量には使えない。アクセント正解率を偽装せず、人評と局所 F0 を併用する。

### 提案

- AivisSpeech は current `accent_phrases` contract を維持する。
- CosyVoice は10行中9行で選ばれたcurrent explicit reading contractを維持し、surface text案を
  棄却する。
- 未校正のPASQA絶対値やvariant間比較を追加判断に使わない。

## Blind listening

`baseline-quality-ab-v1` workflow に32 group / 76 clipを固定した。

- Irodori caption: 12 group、各3候補
- Supertonic speed: 10 group、各2候補
- CosyVoice reading: 10 group、各2候補

AivisSpeech は非決定性が condition 差を交絡するため、この最小 blind bundle から除外した。
候補順は study ID
`4e6e905713d9ddf1c501294970a288c66b6b22ae263ed205e797488fc4f61be9`
から決定的に blind 化し、全候補の完全再生後だけ判断を受理する。

### 結果

| track | current側 | 変更側 | 差なし | 全候補不可 |
| --- | ---: | ---: | ---: | ---: |
| Irodori caption (12) | current 3 | role-only 2 / role+emotion 0 | 6 | 1 |
| Supertonic speed (10) | 1.05: 3 | 1.00: 1 | 3 | 3 |
| CosyVoice reading (10) | explicit reading: 9 | surface text: 0 | 0 | 1 |

結果 file SHA-256 は
`426e0c75bbbf7924df52af4e6e4c152bd7bbfedc2af3b17c412f943b0bf72efe`、
bundle SHA-256 は
`0507b232396aa9396567c85a39cb1300e2b0e34f6e36f1b2233e2a2be74def2d`。
32/32 groupで全候補の完全再生を検証し、final resultをnative listening appが確定した。

Irodori の全候補不可1件は `festival-night/yatai-obasan-003` で、先頭に意図しない
笑い声があるという人評だった。`market-day/fruit-vendor-002` では「姐さん」を
「あねえさん」と読む誤読も記録された。いずれもglobal caption/read contractの変更理由には
せず、対象行のN>=3再生成と人選を別Issueで扱う。
このtargeted replacementは
[#192](https://github.com/Hitsuki-Ban/gaya-bench/issues/192) で追跡する。

### Director承認用の最終提案

1. Irodori current caption contractを維持し、global短縮案を棄却する。
2. Supertonic current `speed=1.05` を維持し、`1.00`へのglobal変更を棄却する。
3. CosyVoice current full-kana explicit reading contractを維持し、surface textへの変更を棄却する。
4. AivisSpeech current `accent_phrases` contractを維持する。
5. 上記2件の個別不良だけをN>=3のtargeted replacementへ送る。
6. 母音持続時間は #191 のfixed aligner reportを待ち、未校正値をgateにしない。

## 再現物

- `pipeline/tools/issue190_baseline_analysis.py`
- `pipeline/tools/issue190_ab_generate.py`
- `pipeline/tools/issue190_ab_measure.py`
- `pipeline/tools/issue190_listening_bundle.py`
- `pipeline/tools/issue190_listening_results.py`
- `artifacts/issue-190/baseline-analysis-v2.json`
- `artifacts/issue-190/ab-v1/*/index.json`
- `artifacts/issue-190/ab-v1/*/metrics-v2.json`
- `artifacts/issue-190/listening-bundle-v2/`
- `artifacts/issue-190/listening-results-v2/baseline-quality-ab-result-v1.json`
- `artifacts/issue-190/listening-analysis-v1.json`

## 一次資料

- [Supertonic speech speed control](https://supertone-inc.github.io/supertonic-py/quickstart/#speech-speed-control)
- [Irodori-TTS-600M-v3-VoiceDesign model card](https://huggingface.co/Aratako/Irodori-TTS-600M-v3-VoiceDesign)
- [CosyVoice official repository](https://github.com/FunAudioLLM/CosyVoice)
- [AivisSpeech Engine official repository](https://github.com/Aivis-Project/AivisSpeech-Engine)
- [Japanese MFA acoustic model](https://mfa-models.readthedocs.io/en/latest/acoustic/Japanese/Japanese%20MFA%20acoustic%20model%20v2_0_1a.html)
- [Japanese MFA v3.3.0 model card](https://huggingface.co/MontrealCorpusTools/japanese_mfa)
- [Montreal Forced Aligner and the state of speech-to-text alignment in 2026](https://arxiv.org/abs/2606.18466)
