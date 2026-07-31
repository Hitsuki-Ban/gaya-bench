# インタラクティブ生成デモ設計

- 対象 Issue: [#19](https://github.com/Hitsuki-Ban/gaya-bench/issues/19)
- 設計日: 2026-07-31
- 状態: Director APPROVE（2026-07-31）

## 1. 決定案

初版は、既存の Cloudflare Pages にフォームを追加し、Cloudflare Worker を入口、
RunPod Serverless の queue-based endpoint を唯一の GPU 実行先とする。

- GPU worker は **Qwen3-TTS 1.7B の1モデルだけ**を固定 revision と container digest で
  起動する。モデル選択、複数モデルへの同時投入、自動 fallback は設けない。
- キャラクターは #174 / #177 で人間確認済みとなった role anchor の一部を
  preset として公開する。自由な話者記述、声優名入力、参照音声 upload は扱わない。
- RunPod の組み込み queue を唯一の job queue とする。Cloudflare Queues を前段に重ねない。
- `active workers = 0`、`max workers = 1` の flex worker から始める。
- 生成結果はブラウザへ一時返却し、公開 R2、baseline、manifest、curation へ保存しない。
- 開発機 + Cloudflare Tunnel は Access で保護した実装検証専用とし、公開リクエストの
  代替経路にはしない。

この案は「低頻度の公開デモ」を対象にする。常時即応が必要になった場合は、実測後に
`active workers = 1` の費用を別 Issue で判断する。実行時に自動で切り替えない。

## 2. 初版の範囲

### フォーム

| 項目 | 初版 |
| --- | --- |
| キャラクター | 承認済み role anchor から最大6 preset |
| セリフ | 日本語の自由入力、1〜60文字、1行 |
| 感情 | 現行 scenario schema の emotion enum から選択 |
| 強度 | 現行 scenario schema の1〜3 |
| モデル | Qwen3-TTS 1.7B 固定。UIで選択させない |

生成中は `受付済み / 待機中 / 生成中 / 完了 / 失敗` だけを表示する。RunPod が
権威ある数値を返さない限り、架空の待ち人数や残り秒数を表示しない。完了音声には
「未策展のデモ生成であり、ベンチ比較結果ではない」と明記する。

### 対象外

- 8モデル比較、Nテイク選抜、seed・sampling の利用者指定
- 自由なキャラクター設定、実在人物の模倣、参照音声 upload
- SSML、reading override、非言語タグ、複数セリフ、会話生成
- 生成音声の公開 URL、共有 gallery、履歴、アカウント、課金
- baseline / production manifest / R2 candidate への昇格

## 3. 構成

```text
Cloudflare Pages /demo
        |
        | Turnstile token + form
        v
Cloudflare Worker: gaya-bench-demo-api
        |-- exact input validation
        |-- Azure AI Content Safety moderation
        |-- SQLite Durable Object quota reservation
        |-- signed job token
        |
        | RunPod API key は Worker secret のみ
        v
RunPod Serverless queue endpoint
        |
        | pinned Linux container + pinned Qwen/model assets
        | one request -> one take -> postprocess/QC
        v
一時 job result (Opus + public metadata)
        |
        v
Cloudflare Worker -> browser Blob -> 再生
```

### Cloudflare Worker

Worker は次だけを担う。

1. Turnstile を検証し、署名付き短期 session cookie を発行する。
2. exact JSON shape、文字数、enum、preset id を検証する。
3. moderation と quota が通った1件だけを RunPod `/run` へ送る。
4. `job_id + session_id + expiry` を署名した job token を返す。
5. job token を検証して RunPod status/result を proxy する。

RunPod API key、Azure AI Content Safety の endpoint / key、署名鍵のいずれかが欠けた場合は
起動または request を明示的に失敗させる。クライアントへ credential や生の内部エラーを
返さない。

### RunPod worker

- Linux container、adapter commit、model revision、role anchor SHA、postprocess profile を
  image/config で固定する。
- request ごとに Cloudflare Worker 側で seed を1個確定し、RunPod の再実行でも同じ
  request id / seed / input hash を使用する。
- 既存 adapter と audio algorithm を再利用するが、release ledger は書かない。
- final Opus が既存 mechanical gate を通った場合だけ音声を返す。
- adapter が検出した OOM、生成、後処理、QC の失敗は terminal error payload として返し、
  platform に再投入させない。worker crash などで RunPod 自身が同じ job を再実行する場合も、
  retry count と同じ request identity を監査できることを launch gate とする。別 seed、
  別 anchor、別 model、開発機への fallback は行わない。
- job result の保存期間は provider の短期 retention に任せ、プロジェクト側では
  user text と audio を永続化しない。

## 4. 入力と悪用の境界

初版は機能を狭くすることで、自由入力と声の模倣のリスクを抑える。

1. Unicode を NFC に正規化してから1〜60文字・改行なしを検証する。
2. 日本語文字、数字、空白、一般的な日本語句読点だけを許す。URL、email、電話番号、
   HTML/SSML、制御文字、不可視文字を拒否する。
3. 日本語を明示的に訓練・試験対象とする Azure AI Content Safety の Analyze Text API で
   Hate / Sexual / SelfHarm / Violence を検査する。いずれかが severity 4 以上、または
   判定不能なら生成しない。日本語の allow/deny fixture を実装 Issue の launch gate にする。
4. preset は架空 NPC だけとし、自由な話者 prompt と reference upload を置かない。
5. raw text と audio は永続 log に残さない。request digest、preset、判定 category、
   duration、cost seconds、成功/失敗 code だけを運用記録にする。

moderation 障害時に未検査のまま生成する経路は設けない。誤検知時は明示的に拒否し、
規則変更は fixture と一緒に別 revision として行う。

## 5. Queue、上限、失敗状態

### 初期上限

| 制約 | 値 |
| --- | ---: |
| 1 session の同時 job | 1 |
| 1 session の1日生成数 | 2 |
| 全体の1日受付数 | 20 |
| RunPod max workers | 1 |
| RunPod idle timeout | 5秒 |
| job execution timeout | 90秒 |
| job TTL | 10分 |
| UI polling | 2秒以上 |

SQLite-backed Durable Object の単一 counter が、UTC日付ごとの全体数と session 数を
原子的に予約する。Workers Rate Limiting や IP 単独の近似 counter を費用上限の真値に
しない。上限値は環境変数の default で補わず、deploy config に必須値として置く。

`execution timeout` は worker が job を取得した後の実行時間、`job TTL` は queue、
初期化、実行を含む全寿命として別々に固定する。実 endpoint で provider の計測境界も
確認し、90秒 / 10分のどちらかに収まらない場合は値を黙って延長せず launch gate へ戻す。

RunPod の queue-based endpoint が待機と実行順の真値である。前段 queue、独自 scheduler、
複数 GPU への振り分けは作らない。

### 公開状態

| HTTP / job 状態 | UI | 再試行 |
| --- | --- | --- |
| 400 / 422 | 入力を修正 | 利用者が修正後に新規送信 |
| 403 | moderation で受付不可 | 自動再試行なし |
| 429 | session / daily 上限 | リセット時刻を表示 |
| queued / initializing | 待機中 | polling のみ |
| running | 生成中 | polling のみ |
| failed / timeout | 生成失敗 | 同一 job の自動再実行なし |
| upstream unavailable | 現在利用不可 | 503。別 backend へ送らない |
| completed | 再生 | provider retention 後は再取得不可 |

## 6. 実行先の比較

| 案 | 利点 | 主な欠点 | 判断 |
| --- | --- | --- | --- |
| 開発機 + named Cloudflare Tunnel | 既存 RTX 4070 Ti と runtime をそのまま検証できる。Tunnel は outbound-only で inbound port 不要 | 開発作業と競合し、停電・再起動・回線・Windows session に依存。公開可用性と費用上限を保証しにくい | Access 保護の staging のみ |
| RunPod Pod | custom Docker と任意 CUDA runtime。時間単価は安い | 起動停止と queue を自前運用。停止忘れが固定費になる | worker 開発・負荷試験用。公開本番には不採用 |
| RunPod Serverless | custom Docker、組み込み queue、flex の scale-to-zero、max worker で同時実行を制限 | cold start、Linux port、初期化時間も課金。infrastructure failure は同一jobを自動再実行しうる。provider job retention は短い | **初版の採用案** |
| Cloudflare Workers AI | serverless GPU と edge API を一体化できる | project の8 TTS model は catalog に無く、private custom model は self-service の固定経路ではない | TTS 実行先には不採用 |
| Cloudflare Containers | Worker と container を一体運用できる | 公開 instance type は最大12 GiB RAM / 4 vCPUで GPU が提示されていない | TTS 実行先には不採用 |

Cloudflare Tunnel を「RunPod が失敗した時の fallback」にしない。両環境の出力 identity、
可用性、費用責任が曖昧になるためである。

## 7. コスト見積

価格は2026-07-31時点の公式表示で、税・為替・container registry は含めない。

### GPU

RunPod の24 GB general tier（L4 / A5000 / 3090）は、Serverless flex が
`$0.00019/s`、active が `$0.00013/s`。4090 PRO は flex `$0.00031/s`、active
`$0.00021/s` である。

現行 `data/manifest.json` の selected 1,243 clip から計算した RTX 4070 Ti 上の
`duration_sec * rtf` は、モデル別 p95 で約1.2〜19.7秒、Qwen は19.7秒だった。
これは model load と postprocess を含まない下限であり、RunPod の料金見積は次の式で
実測値へ更新する。

```text
月額GPU = flex単価 × (初期化秒 + 実行秒 + idle秒) × 月間worker起動数
         + container disk
```

RunPod は worker の起動から完全停止までを課金し、完了後の idle timeout も含む。初版は
公式 default と同じ5秒を固定する。container disk は5分単位で約 `$0.10/GB/月` のため、
構成する容量を `D GB` とした上限は `D × $0.10/月` と別掲する。次は各 job が独立した
worker 起動になる保守的な月600 job の例であり、同じ worker が連続 job を処理する場合は
idle 秒と初期化秒の重複が減る。

24 GB general tier の月額例:

| 仮定 | 1 job | 月額例 |
| --- | ---: | ---: |
| warm 20秒 + idle 5秒、月600 job | $0.00475 | $2.85 |
| 初期化60秒 + 実行20秒 + idle 5秒、月600 job | $0.01615 | $9.69 |
| active worker 730時間常駐 | — | 約$341.64 |
| container disk `D GB` を1か月保持 | — | 約 `D × $0.10` |

初版は flex と20 job/日の hard cap を採用する。cold start の p50 / p95 と実際の
billable seconds を canary で測り、1件当たりの想定が `$0.05` を超える場合は公開せず
Director へ戻す。

RunPod Pod は公式表示で A5000 `$0.27/h`、RTX 4090 `$0.69/h`。24時間常駐なら
概算で月 `$197.10` / `$503.70` となるため、低頻度デモの常駐先にはしない。

### Edge

- Workers Paid は最低 `$5/月`。初版規模の request / CPU は包含枠内を想定する。
- Turnstile は Free plan で challenge 数が無制限。
- SQLite Durable Object は Free / Paid の包含枠内で、20 job/日では追加費用を
  想定しない。
- Azure AI Content Safety F0 は5,000 transaction/月、5 RPS。初版上限の600件/月は
  無料枠内だが、resource 作成可否、region、最新価格を実装開始時に再確認する。
- 音声を R2 に永続化しないため、user-generated audio の保管費は発生しない。

## 8. リスクと launch gate

| リスク | launch gate |
| --- | --- |
| Linux container で adapter/runtime が再現しない | pinned image を24 GB GPUで起動し、同じ fixture を3回生成して provenance と mechanical gate を確認 |
| cold start が体験または費用を支配する | flex の init / queue / execution p50・p95、billable seconds を各20回測る |
| role identity が baseline とずれる | #174 / #177 の anchor decision と hash が確定するまで実装公開しない |
| 日本語 moderation の誤検知・見逃し | allow 20件 / deny 20件以上の固定 fixture を作り、判定不能をfail-closedで確認 |
| 悪用で費用が膨らむ | Turnstile、session 2件/日、global 20件/日、max worker 1 を実 endpoint で確認 |
| demo出力がbenchmark結果と誤認される | UI、API metadata、download名に「未策展・比較対象外」を表示 |
| model / reference の利用条件が変わる | deploy前に pinned revision と anchor の権利 metadata を再検証 |

## 9. 実装開始条件

1. Director が本書の実行先、Qwen固定、上限、非永続方針を承認する。
2. #174 / #177 が完了し、公開する role anchor id と SHA が固定される。
3. Qwen の Linux container canary が24 GB GPUで pass する。
4. Cloudflare と RunPod の価格を実装 Issue 着手日に再確認する。

承認後は、RunPod worker、Cloudflare API、site UI の3 Issue に分ける。最初の縦 slice は
1 preset / 1 emotion / 1 request とし、動作確認前に汎用 provider interface、複数model
scheduler、account system を作らない。

## 10. Director 判断

Director review は **APPROVE**、blocking finding は0件だった。その後の独立レビューで
日本語非対応の moderation 選定、強度範囲、RunPod 課金式を訂正した。いずれも承認済みの
実行先、公開範囲、非永続方針を変更せず、訂正後の独立差分レビューも APPROVE だった。
次の5点は Director review で一括承認され、Section 8 の launch gate と実装開始時の
価格再確認を必須条件とした。

- 公開本番を RunPod Serverless flex の単一路線とすること
- 初版を Qwen3-TTS 1.7B、最大6 preset、20 job/日に限定すること
- 音声を永続化せず、baseline / manifest と完全に分離すること
- 開発機 Tunnel は Access 保護の staging に限定すること
- #174 / #177 完了後に実装 Issue を起票すること

## 11. 一次資料

- [Cloudflare Tunnel overview](https://developers.cloudflare.com/tunnel/)
- [Cloudflare Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)
- [Cloudflare Workers Rate Limiting](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/)
- [Cloudflare Turnstile plans](https://developers.cloudflare.com/turnstile/plans/)
- [Cloudflare Durable Objects pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/)
- [Cloudflare Workers AI overview](https://developers.cloudflare.com/workers-ai/)
- [Cloudflare Workers AI model catalog](https://developers.cloudflare.com/workers-ai/models/)
- [Cloudflare Containers limits](https://developers.cloudflare.com/containers/platform-details/limits/)
- [Azure AI Content Safety overview](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/overview)
- [Azure AI Content Safety language support](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/language-support)
- [Azure AI Content Safety text quickstart](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/quickstart-text)
- [Azure AI Content Safety migration and pricing summary](https://learn.microsoft.com/en-us/azure/ai-services/content-moderator/content-moderator)
- [RunPod pricing](https://www.runpod.io/pricing)
- [RunPod Serverless pricing](https://docs.runpod.io/serverless/pricing)
- [RunPod Serverless endpoint overview](https://docs.runpod.io/serverless/endpoints/overview)
- [RunPod Serverless endpoint settings](https://docs.runpod.io/serverless/endpoints/endpoint-configurations)
- [RunPod job states and metrics](https://docs.runpod.io/serverless/endpoints/job-states)
- [RunPod Serverless workers](https://docs.runpod.io/serverless/workers/overview)
- [RunPod cached models](https://docs.runpod.io/serverless/endpoints/model-caching)
