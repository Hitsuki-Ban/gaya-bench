# 全モデル完全基準線・役柄／reading固定プロトコル

Issue #174 / #177 では、公開版の選択済み音声だけでなく全 8 model × 161 line を
再監査する。役柄または日本語 reading の生成入力を現行 adapter 契約で証明できない
597 slot はすべて再生成し、入力一致を証明できる 691 slot だけを継承する。最終集合は
exact 1,288 slot とする。

## 権威入力

唯一の Phase B 計画は [`plan.json`](plan.json) の canonical JSON bytes である。

- format: `2`
- protocol: `role-baseline-plan-v2`
- base manifest SHA-256:
  `f9dfda542fd1120fe0f74daae3036eab5211d7394d155f7b9953978e59bbe89d`
- base manifest Git blob: `44061fafe330a9bebfed7a97a0b69ebe234c8724`
- base candidate set SHA-256:
  `91913e08f97497f1f7604f109a6d0f7308742237277f6bbc5483678ac9858cc2`
- base aggregate selection SHA-256:
  `629cc80346160eb8e687757e6f792ef519da9a4fb74f79bdf97eb4d00f56126e`
- inherited: 691
- final: 1,288

loader は plan、base manifest、15 scenario file、voice metadata、8 model revision、
58 role snapshot を実ファイルから再計算する。古い protocol、非 canonical JSON、相対
path、未知／欠落 field、revision 差異、既定 plan や latest run の探索は生成前に拒否
する。

## role anchor authority

Phase A は旧 plan の下で既に生成した 106 group を人が選定する一度限りの外部権威で
あり、Phase B plan へ生成手順を複製しない。v2 plan の `anchor_authority` は次を exact
に拘束する。

- source plan SHA-256:
  `f21f7ffa598c38b24f345b8c05f4d18fe3073618deaa742bb55ff30e0a26a0e5`
- current candidate set SHA-256:
  `67fa107310069af37089d09172e1403a375f210461b945c50f88d18ac5fde444`
- top-up lineage の initial candidate set SHA-256:
  `9ff3bb11452ca80899944121edaba5e9a361a1cd8000a1ef716375e673062765`
- owner が 106 group を確認して export／finalize した selection の実 SHA-256

selection は `role-anchor-selection-v1` と隣接 `.sha256` marker を持つ。selection root
の `plan_sha256` は旧 source plan SHA であり、新しい Phase B `plan_id` へ書き換えない。
Qwen3-TTS / Irodori の参照音声なし 53 role は、model revision、role identity、選択
WAV、group decision から導出した selected role epoch を使用する。明示 reference voice
を持つ role は固定の権利確認済み WAV を使う。

## 日本語 reading transport

scenario の `line.text` は公開表示と表層発話、`line.reading` は明示的な読みの metadata
である。adapter はモデルの実 API が提供する経路だけを使い、reading や役柄 caption を
表層本文へ連結しない。

| model | 実生成へ渡す reading 契約 |
| --- | --- |
| AivisSpeech | `line.text` で `/audio_query` を作り、明示 `line.reading` がある場合だけ `/accent_phrases` で得た phrase を query へ置換する |
| CosyVoice3 | 明示 reading をカタカナ正規化した `tts_text` として渡し、未指定時はモデル必須の auto-kana を使う |
| Chatterbox | `line.text` のみ。per-request reading API はない |
| GPT-SoVITS | `line.text` のみ。表層文を reading に置換しない |
| Irodori | emotion emoji を含む表層 `line.text`。reading metadata を本文へ混ぜない |
| Qwen3-TTS | `line.text` のみ。role anchor / clone prompt は話者制御専用 |
| Supertonic | `line.text` のみ。reading metadata を無視する |
| VoxCPM2 | `line.text` のみ。voice design prompt と本文を分離する |

明示 reading 25 line × 8 modelを含む全1,288件の`generation_input` receiptに加え、
同じ全1,288件を実際の`adapter.generate()`へ通すruntime transport probeを作る。
target textだけでなくCosyVoice instruction、Irodori caption、Qwen clone promptなどの
モデル入力fieldも`generation_input`とexact比較する。adapter test、runtime probe、
realized receiptの検証を組み合わせ、
`generation_input`だけ正しく実生成時に別の値を渡す差分、readingの誤置換、prompt
leakage、未知のtransportをfail fastする。

## Phase B generation

Phase B target は exact 597 slot である。

| model | target | takes | minimum eligible | seed policy |
| --- | ---: | ---: | ---: | --- |
| AivisSpeech | 25 | 1 | 1 | `none` / `null` |
| Chatterbox | 13 | 4 | 3 | `derived-sha256-v1` / 104 |
| CosyVoice3 | 14 | 4 | 3 | `derived-sha256-v1` / 104 |
| GPT-SoVITS | 37 | 4 | 3 | `derived-sha256-v1` / 104 |
| Irodori | 161 | 4 | 3 | `derived-sha256-v1` / 104 |
| Qwen3-TTS | 161 | 4 | 3 | `derived-sha256-v1` / 104 |
| Supertonic | 25 | 4 | 3 | `derived-sha256-v1` / 104 |
| VoxCPM2 | 161 | 4 | 3 | `derived-sha256-v1` / 104 |

8 model はすべて新しい primary run として生成する。VoxCPM2 の legacy reuse はない。
AivisSpeech はモデルが stochastic seed を持たないため N=1／seed null とし、topup を
禁止する。他 7 model は eligible が 3 未満の exact group だけ、異なる明示 seed base
の topup run で整組置換する。primary と topup の候補を拼接しない。

ledger / QC / manifest / candidate set は `phase-b-generation-v2` を使い、次の二つの
plan identity を混同しない。

- `plan_sha256`: 現在の Phase B v2 plan
- `anchor_plan_sha256`: Qwen / Irodori だけが持つ旧 anchor source plan。それ以外は null

さらに anchor selection SHA、run kind、supersedes run、全 target role epoch、各 attempt
の generation input SHA、seed、gate、audio SHA を exact に拘束する。

## 連続生成の境界

モデル process の再起動を role ごとの必須条件にはしない。代わりに、各 request の
seed、本文、reading transport、role identity、reference／anchor、caption、sampling を
content-addressed input として固定する。Qwen の generation cache、Irodori の
`context_kv_cache` と `torch.Generator` は request 内に閉じる。adapter test は前の
request の本文、reading、role、seed が次の runtime call へ残らないことを検証する。

## listening と decision

listening bundle は 8 primary run と明示 topup だけから作る。source map は 597 group
すべてに model policy 由来の `minimum_eligible_candidates` を持つ。Aivis の単一候補も
自動採用せず、owner が明示確認する。

ページでは全候補について次を常時表示し、選択候補を最後まで再生してから確定する。

- 内容の欠落／追加／反復と prompt leakage
- 漢字の文脈上の読み
- 厳密な日本語 pitch accent
- gender / age / archetype / voice identity
- delivery
- naturalness / audio quality

decision は `role-baseline-decision-v1` の canonical bytes とし、plan SHA、anchor
selection SHA、candidate-set SHA、597 group SHA、各 rubric、owner selection を exact
に拘束する。

## final release と publish

source audit は旧公開版を次の exact partition に分ける。

- replacement 597: mismatch 357 + unverifiable 145 + match 89 + failure 6
- inherited 691: match 691、unverifiable 0、failure 0
- final: inherited 691 + replacement 597 = selected 1,288、failure 0

旧版で match だった 89 件も、新しい全モデル入力基準線へ揃えるため replacement に
含める。release は 691 inherited receipt を逐条再検証し、597 decision と重複／欠落
なく overlay する。

R2 publish は全 object を preflight HEAD し、新しい immutable audio だけを
`If-None-Match: *` で upload する。最終 manifest の全 candidate を再度 HEAD してから
だけ activation と persistent receipt を書く。

## 完了ゲート

1. v2 plan、scenario、voice、8 model revision、58 role snapshot が再計算で一致する
2. owner の anchor selection 106 group と全 WAV SHA が旧 authority 下で再検証できる
3. 8 primary の計 597 target が terminal で、reading / role / plan receipt が一致する
4. owner の line decision が 597 group の exact contract を通る
5. final release が 691 + 597 = 1,288 selected、failure 0 となる
6. pipeline / site の全 test、format、lint、typecheck、build が成功する
7. R2 / Pages 公開後の GET と audio decode が成功する
8. Issue report、merge、remote sync、worktree / branch cleanup が完了する
