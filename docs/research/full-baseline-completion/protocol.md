# 全モデル完全基準線・役柄固定プロトコル

Issue #174 / #177 では、公開版の欠項だけを埋めるのではなく、全モデルで役柄情報が
生成入力まで正しく届くことを固定入力から再検証する。特に参照音声を持たない
Qwen3-TTS / Irodori の役柄は、台詞生成より先に役柄専用の中立 anchor を選定する。
その選定を拘束した状態で Phase B の 363 slot を生成または再利用し、公開版から
継承する 925 slot と合わせて 8 model × 161 line = 1,288 slot を確定する。

## 権威入力

唯一の計画は [`plan.json`](plan.json) の canonical bytes とする。

- protocol: `role-baseline-plan-v1`
- plan SHA-256:
  `f21f7ffa598c38b24f345b8c05f4d18fe3073618deaa742bb55ff30e0a26a0e5`
- base manifest SHA-256:
  `f9dfda542fd1120fe0f74daae3036eab5211d7394d155f7b9953978e59bbe89d`
- base manifest Git blob:
  `44061fafe330a9bebfed7a97a0b69ebe234c8724`
- base candidate set SHA-256:
  `91913e08f97497f1f7604f109a6d0f7308742237277f6bbc5483678ac9858cc2`
- base aggregate selection SHA-256:
  `629cc80346160eb8e687757e6f792ef519da9a4fb74f79bdf97eb4d00f56126e`
- scenario registry: 15 file の path / raw SHA-256 と aggregate SHA-256
- voice metadata: path / raw SHA-256
- model identity: 8 model の固定 profile revision
- role snapshot: 58 role の scenario、character、role 7項目、reference voice、
  scene setting、role identity SHA-256

loader はこれらを実ファイルから再計算する。古い plan protocol、非 canonical JSON、
欠落・余分な role/target、revision 差異、相対 path、既定 plan の探索は生成前に
拒否する。

## Phase A: role anchor

明示的な参照音声を持たない 53 role を、Qwen3-TTS と Irodori で各1回ずつ扱う。
対象は exact 106 group、初回は各 N=4、最低 mechanical-pass 3件である。seed は
`role-anchor-derived-sha256-v1` により plan、model、scenario、character、attempt
から導出し、全 attempt で一意にする。

Phase A の入力は台詞の emotion / intensity を一切含めない。各 role の
`name / kind / gender / age / archetype / voice / personality`、scene setting、
固定の中立 anchor text、model revision、seed を明示する。

- Qwen3-TTS: VoiceDesign で短い中立 anchor WAV を生成する。選定後、固定 anchor
  text と WAV から Base の voice clone prompt を1 roleにつき1つ作る。
- Irodori: 完全な役柄情報を含む中立 caption で anchor WAV を生成する。選定後は
  その WAV を参照音声とし、Phase B の各台詞で完全な役柄・scene・emotion・
  delivery caption を渡す。

run は `artifacts/role-anchors/runs/<run-id>/` が所有し、新規 directory にしか
書かない。candidate sidecar / ledger / WAV は plan、model revision、role identity、
review role epoch、attempt、seed、generation input SHA、WAV SHA に拘束する。
QC は `mechanical=pass|fail`、`content=not_checked|pass|review_required`、
`notes=string[]` の exact contract とし、Phase A producer は content を
`not_checked` のまま記録する。
過去 cache や最新 run を探索・再利用しない。

初回 run の明示 ID だけを merge して `role-anchor-candidate-set-v1` を作る。
各 candidate group は eligible candidate とは別に、その role で実行済みの全 attempt
番号を canonical 昇順で保持する。rejected / failed attempt も履歴から除外せず、
eligible candidate の attempt はこの履歴の部分集合でなければならない。seed は
plan、role、attempt から再計算する。
eligible が3件未満の group は `role-anchor-topup-v1` で source candidate-set SHAを
固定し、全 attempt 履歴の最大値より後を追加する。失敗した topup run も次回 merge
へ明示的に含める。既存 attempt の再試行・置換、take index の付け替え、失敗 run の
省略、暗黙 retry 合成は行わない。

## Phase A listening と decision

全106 groupが最低3件を満たした後だけ `role-review-v1` bundleを作る。bundle直下に
許されるファイルは `role-review-v1.json` と、その JSON が参照する
`audio/<candidate-id>.wav` だけである。decision template その他の未参照ファイルは
同梱しない。

各 group では少なくとも異なる2候補を聴き、選択候補自身を必ず聴く。判断基準は
ページ上で常時表示し、次を全て明示入力する。

- content、prompt leakage、reading、pitch accent
- gender、age、archetype、同一 role の voice identity
- delivery、naturalness / audio quality 1..5、notes

anchor で意味を持たない reading / pitch accent / delivery は
`not_applicable` を明示し、未入力にはしない。絶対合格候補がなくても最良候補を
選べるが、rubric の fail はそのまま証拠として残す。

ページが export する唯一の decision は `role-review-decision-v1` である。root は
phase、plan SHA、candidate-set SHA、groups、role reopen requests を exact に持つ。
各 group は review group の SHA-256、heard candidate IDs、selected candidate ID、
完全 rubric、`confirmed=true` を拘束する。finalize は106 groupとのexact一致、
group SHA、candidate順、最低聴取数、選択候補、role epochに加え、各groupが
mechanical-pass候補3件以上であることを独立に再検証する。role reopen requestが
1件でも残るdecisionは確定しない。

## anchor selection と role epoch

finalize は選択 WAV を content-addressed ID で新規 directory へ copyし、
`role-anchor-selection-v1` と、そのcanonical bytesを拘束する隣接
`role-anchor-selection-v1.sha256` を確定する。選定後の role epoch は review前のepochを
流用せず、次を含む `selected-role-epoch-v1` の canonical SHA-256 とする。

- model / model revision
- scenario / character / role identity SHA-256
- review role epoch
- selected anchor ID / selected WAV SHA-256
- 当該 group decision SHA-256

したがって anchor、WAV、判断、model revision、役柄 snapshot のどれかが変われば
Phase B の role epoch も変わる。

## Phase B: line generation

Phase B は N=4、最低 eligible 3件、`derived-sha256-v1` / seed base 104 とする。
target は次の exact 363 slot である。

- Qwen3-TTS: 161 generate
- Irodori: 161 generate
- Chatterbox: 13 generate
- CosyVoice3: 14 generate
- GPT-SoVITS: 12 generate
- VoxCPM2: 2 explicit reuse

Qwen3-TTS / Irodori の参照音声なし role は、明示した
`role-anchor-selection-v1` から role、revision、selected role epoch、anchor ID、
WAV SHAを解決し、その receipt を generation input / realized input に残す。
明示的な reference voice を持つ role はその固定 WAV を使う。他 model に anchor
selection を渡した場合は拒否する。resolverには現在のfrozen plan SHAを明示し、
selection rootと隣接SHA markerの双方を照合する。selection / marker / planの未指定、
古い role epoch、WAV改ざん、別 revision、別 role への流用、cache/latest/env による
代替は受理しない。

Phase Bのcandidate selection、final release overlay、R2増分公開は別のrelease境界で
行う。925 inherited groupを再生成せず、363 targetとの和集合がexact 1,288 groupに
なることを検証する。

5 modelの生成は各1つの`primary` runを起点とする。ledger / QC sourceはfrozen plan
SHA、run kind、anchor selection SHA（Qwen / Irodoriのみ）、全target groupと各
role epochをexactに持つ。eligibleが3件未満のgroupは、異なるseed baseと
`supersedes_run_id`を持つ明示`topup` runで再生成する。topupはsuperseded runの
exact subsetであり、そのgroupのcandidateを整組取代する。primaryとtopupの候補を
拼接したり、複数runから都合のよいcandidateだけを集めたりしない。最終363 groupは
それぞれ一意の有効source runを持つ。
consumerは各attemptのseedをsource seed base、model、scenario、line、variant、
take indexから`derived-sha256-v1`で再計算する。candidateはledger/QCの同一eligible
slotへexact joinし、generation input、seed、gate、duration、encoded Opus loudness
をQC snapshotから独立に照合する。

Qwen3-TTS upstreamのVoiceDesignは各requestでtokenizeと`model.generate`を実行し、
coreの`DynamicCache`もrequest内で作られる。Irodoriの`context_kv_cache`と
`torch.Generator`もsampling request内に閉じる。このため役ごとにprocessをreload
することは要件にせず、固定role anchor / clone promptと全入力receiptの一致を
境界にする。Qwenの同一role clone prompt再利用はupstreamのVoice Design then Clone
手順に沿う。

- [Qwen3-TTS VoiceDesign wrapper](https://github.com/QwenLM/Qwen3-TTS/blob/6cafe5582caea83df269c36b1ce62d953a9cc66b/qwen_tts/inference/qwen3_tts_model.py#L635-L665)
- [Qwen3-TTS request-local cache](https://github.com/QwenLM/Qwen3-TTS/blob/6cafe5582caea83df269c36b1ce62d953a9cc66b/qwen_tts/core/models/modeling_qwen3_tts.py#L1857-L2103)
- [Irodori inference runtime](https://github.com/Aratako/Irodori-TTS/blob/eaf74d6a19138f743acb5b71a445fd25a57db987/irodori_tts/inference_runtime.py#L746-L1072)
- [Irodori request-local RNG / KV](https://github.com/Aratako/Irodori-TTS/blob/eaf74d6a19138f743acb5b71a445fd25a57db987/irodori_tts/rf.py#L111-L417)

## Phase B listening と line decision

listening bundleは5 primary、明示topup、次の固定Vox exceptionだけから作る。

- run ID: `20260730T204323380360Z-voxcpm2-n4`
- ledger SHA-256:
  `589da2bf299cba5d25a07e6af17726795936cd53d33ff71820eaadbc321e24f7`
- QC report SHA-256:
  `5843a783fcbdad585cec0c641f52950a6fb2046d8198e168ea47d42ffc2af0f9`
- manifest SHA-256:
  `c096cd388229f0ac60fae42e82a8d3d8423c5e22644983c3da570b5e3bd41563`
- candidate-set SHA-256:
  `7be722c866a4f1df013821fa178fe00a755ad75b92fe642f342cddada5ce3954`
- exact group:
  `voxcpm2/goblin-camp/goblin-cook-001/dry`、
  `voxcpm2/spirit-forest/pixie-003/dry`

このrunだけはPhase B provenance導入前のため固定摘要exceptionとする。別run、
別group、4摘要の1 byteでも異なるもの、または他modelのlegacy runへ一般化しない。

line decisionは`role-baseline-decision-v1`で、frozen plan SHA、anchor selection SHA、
candidate-set SHAに加え、全363 groupのrole epochとcanonical `group_sha256`を持つ。
group hashはscenario title、本文、delivery、role epoch、source run ID、候補の
take ID / path / audio SHA / gateをexactに拘束する。candidate、source、selection、
role epochのいずれかが変わった旧decisionやgroup hash欠落は再生できない。各groupは
mechanical-pass candidate 3件以上の全候補を評価し、必ず1件を選ぶ。

## final release と publish

source auditの公開比較はexact partitionを構成する。

- replacement: mismatch 357 + failure 6 = 363
- inherited: match 780 + Vox identity unverifiable 145 = 925
- final: 925 + 363 = selected 1,288、failure 0

inheritedは「公開candidate全体」ではなく、legacy selected groupからreplacement exact
集合を引いたものとする。したがってreplacementには旧selected 318件も含まれ、新しい
selectionで置き換える。release provenanceは780件をmatched countとして記録し、
145件は`model / scenario / line / variant / take_id / reason`を逐条
`inherited_identity_unverifiable`として列挙する。検証不能なものをmatchへ昇格しない。
release検証とpublishは固定SHAのsource auditを明示入力し、この145件をauditから
再構成して完全一致を要求する。release自身のcountや列挙だけを権威として扱わない。

R2 publishは全objectをpreflight HEADし、新しいimmutable音声だけを
`If-None-Match: *`付きでpre-uploadする。upload完了後にfinal manifest全candidateを
もう一度HEADし、key、length、Content-Type、Cache-Control、SHA metadataを照合する。
この最終全量HEADが成功する前はmanifest activationもpublish receiptも書かない。
preflightでcanonical manifest bytesを一度だけ固定し、その同じbytesとdigestを
final HEAD後のactivationに使用する。成功後に初めてmanifestをactivation pathへ
atomic置換し、その後にcanonical persistent receiptを書く。activation後のreceipt
書込み失敗ではactivationを巻き戻さず、receipt未作成として失敗を報告する。
R2は複数object transactionを提供しないため、途中失敗で未参照immutable objectが
残る可能性はあるが、receiptを先に書いてactivation済みとは表現しない。

## 完了ゲート

1. plan、scenario、voice、model revision、58 role snapshotが再計算で一致する
2. Phase A 106 groupが各3 eligible以上、owner decisionがexact contractを通る
3. selection 106 groupのselected role epochと全WAV SHAが再検証できる
4. Phase B 363 targetがterminalで、Qwen / Irodori receiptが選定anchorを拘束する
5. final releaseが inherited 925 + Phase B 363 = selected 1,288、failure 0となる
6. pipeline / site のschema、test、lint、typecheck、buildが成功する
7. R2 immutable upload、全object HEAD、manifest / Pages公開後のGET/decodeが成功する
8. Issue report、merge、remote sync、worktree / branch cleanupが完了する
