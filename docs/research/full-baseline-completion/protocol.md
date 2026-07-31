# 全モデル完全基準線の補録プロトコル

Issue #174 では、2026-07-30 公開版に残る非 selected 45 スロットだけを再生成し、
8 model × 161 line の全 1,288 スロットを selected にする。既存 selected 1,243
スロットは再生成も再選定もせず、公開済みの判断権限と候補をそのまま継承する。

## 固定入力

補録対象は [`plan.json`](plan.json) の canonical bytes だけを権威入力とする。

- base manifest SHA-256:
  `f9dfda542fd1120fe0f74daae3036eab5211d7394d155f7b9953978e59bbe89d`
- base manifest Git blob:
  `44061fafe330a9bebfed7a97a0b69ebe234c8724`
- base candidate set SHA-256:
  `91913e08f97497f1f7604f109a6d0f7308742237277f6bbc5483678ac9858cc2`
- base aggregate selection SHA-256:
  `629cc80346160eb8e687757e6f792ef519da9a4fb74f79bdf97eb4d00f56126e`
- plan SHA-256:
  `a3d9480a6f38b7fdce3ae96f23d3382bc41e9d7be66caca1634eead16e148bb9`
- target: Qwen 40、Chatterbox 1、CosyVoice3 2、VoxCPM2 2
- generation: N=4、`seed_base=104`
- listening bundle 受理条件: 各 group に mechanical-pass candidate が3件以上

loader は base manifest raw SHA、Git blob、candidate set、旧 selection、45 target の
identity・prior outcome を再計算する。plan と公開版の非 selected 集合が exact
一致しない場合は生成前に失敗する。selected group の上書き、target の追加・欠落、
非 canonical plan、既定 plan の探索は受理しない。

## 生成と証拠の配置

4 model は model ごとに1つの partial run として、plan にある離散 line だけを N=4
生成する。scenario、reference voice、artifacts の root はすべて絶対 path で
明示し、worktree 相対の代替 path は使わない。

権威 artifacts root:

```text
F:\WorkSpace\GayaDemo\artifacts\issue-174
```

code worktree を削除しても ledger、sidecar、WAV、Opus、QC、listening bundle、
decision、release、publish audit が残ることを要件とする。生成 run の一部に失敗が
あり、mechanical-pass candidate が3件未満になった model は、その run を初回の
権威入力として採用しない。新しい run を明示的に作り直し、run 間の take index
付け替えや暗黙 retry 合成は行わない。

## QC と聴取判断

通常の「絶対採用可否」ではなく `missing-slot-best-of-n-v1` の
`best_available` 判断を行う。各候補について次を全て記録してから、group 内の
相対的な最良候補を1件選ぶ。

- `content_correct`: 台詞の欠落・追加・反復を含む内容一致
- `prompt_leakage`: 感情名、演技指示、話し方、メタ文の音声への漏洩
- `reading_correct`: 語・漢字の読み
- `accent_naturalness`: 日本語の音調・アクセントの自然さ（1..5）
- `role_match`: 役柄・声線の一致（1..5）
- `delivery_match`: 感情・強度・演技の一致（1..5）
- `audio_quality`: 自然さと音質（1..5）
- `adoptable`: 単独で通常採用できる品質か
- `notes`: 欠点の自由記録

理論上の読みが正しくても日本語アクセントが不自然なら
`accent_naturalness` に反映する。完全合格候補がなくても skip はせず、
`content_correct=false` や `adoptable=false` を正直に保持したまま最良候補を
選べる。この選択は「絶対合格」を意味しない。専用ページはこの基準を音声読込前
から常時表示し、全候補の必須評価と全45 group の選択が終わるまで decision を
export しない。

## published-base overlay

旧 aggregate selection は、公開 manifest の automatic-gate 1,122 group と保存済み
Qwen human 160 group から format v2 として再構成し、SHA-256 が `629cc803…` と
exact 一致することを先に確認する。最終 selection は独立した
format v3 / `take-selection-v2` とする。

- 非 target 1,243 group: candidate、decision、authority、gate/rubric を exact 継承
- target 45 group: 新しい全 eligible candidate と `best_available` authority を収録
- 旧 skipped 39 candidate: 最終 manifest から除外し、base provenance だけに残す
- 最終 terminal state: selected 1,288、skipped 0、failure 0

provenance は旧 run がローカルに存在すると偽装せず、
`base.kind=published_manifest` とする。base manifest、plan、supplement run、
candidate set、decision の全 digest と、45件の prior outcome を明示する。

## R2 増分公開

公開済み object は immutable とし、旧 1,243 candidate に PUT しない。旧 object は
R2 S3 endpoint に対する全件 HEAD で key、length、Content-Type、Cache-Control、
保存済み SHA metadata を照合する。新 candidate はローカル bytes の SHA-256 を
検証後、`If-None-Match: *`、`Content-MD5`、`x-amz-meta-sha256` を付けた単一
`PutObject` だけを許可する。`412 Precondition Failed` は既存 object を HEAD して
同一 identity のときだけ冪等成功とし、不一致なら衝突として失敗する。

R2 は object と metadata に強整合性を提供するため、PUT 後は即時 HEAD を行う。
ETag は multipart を含め内容 SHA と同一とは限らず、内容 identity として使わない。
全 upload 後に最終 manifest の全 candidate を再 HEAD し、成功してから
`data/manifest.json` を更新する。

根拠:

- [Cloudflare R2 S3 API compatibility](https://developers.cloudflare.com/r2/api/s3/api/)
- [Cloudflare R2 consistency model](https://developers.cloudflare.com/r2/reference/consistency/)
- [Cloudflare R2 error codes](https://developers.cloudflare.com/r2/api/error-codes/)
- [AWS S3 conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
- [AWS HeadObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html)

## 完了ゲート

1. 4 model の run と QC が terminal
2. 全45 group に mechanical-pass candidate 3件以上
3. owner decision が canonical contract を通過
4. final release が 8 × 161、selected 1,288、skipped/failure 0
5. 旧 selected 1,243 group が exact 継承
6. R2 増分 upload と全候補 HEAD sweep が成功
7. pipeline test、site check/test/build、公開 bundle 境界が成功
8. PR CI、merge、Pages production、全 selected audio GET/decode が成功
9. Issue report、remote sync、worktree と branch の cleanup が完了
