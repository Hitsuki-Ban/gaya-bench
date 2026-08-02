# ローカル聴取アプリ設計原則

Issue #174 の人手聴取は、公開サイトとは分離した単一のローカルアプリで実施する。公開画面は日本語の静的サイトを維持し、この画面だけを中国語の作業 UI とする。

## 1. 実行・データ境界

- エージェントが絶対パスで入力 bundle と結果 directory を指定する。ブラウザの directory picker、`localStorage`、download は使わない。
- `vp run listening:start` / `listening:status` / `listening:stop` を唯一の lifecycle とする。
- Windows の `start` は `vp run` の process job から独立した `Win32_Process.Create` で daemon を生成する。Node の `detached` だけに依存せず、`status` / `stop` は session ID と health を照合する。
- daemon は `127.0.0.1` の固定 port にだけ bind し、入力 bundle を read-only、結果 directory を write-only として扱う。
- bundle、結果 directory、authority plan は相互にも `site/`、session directory にも重ならない独立 boundary に限定し、Vite の file allowlist は `site/` だけに固定する。authority plan は既存の通常 file とし、symlink を拒否する。
- API は bootstrap、候補 ID による audio、draft、finalize、health、stop だけを公開する。任意 path の read/write API は作らない。
- draft は候補集 hash と整数 revision に束縛し、変更ごとに直列・atomic 保存する。保存状態を UI に常時表示する。
- autosave は draft に限る。最終 decision は全項目の能動的な確認後に一度だけ書き込み、その後は read-only とする。
- 必須設定、bundle、hash、port、保存のいずれかが不正なら明示的に停止する。旧 picker / storage / protocol への fallback は持たない。

起動契約は次の 3 command に固定する。

```powershell
vp run listening:start --workflow role-quality-review-v1 --bundle <absolute-bundle-directory> --output <absolute-result-directory> --port 4173
vp run listening:status
vp run listening:stop
```

Phase B 597 group の全量 owner 聴取 workflow は Director 契約変更により起動不可とする。
公開後は `role-quality-review-v1` で soft signal 付き 145 slot だけを復核し、外部 authority
plan は受け付けない。bundle 自身が plan / decision / manifest / quality-signals SHA を固定する。

API は同一 origin の `/__gaya-listening` 配下に限定する。

| method | path | 役割 |
| --- | --- | --- |
| `GET` | `/bootstrap` | 検証済み bundle、現在 revision、結果名、mutation token を一度に返す |
| `GET` | `/audio/:candidateId` | 検証済み候補だけを Range 対応で再生する |
| `GET` | `/draft` | 結果 directory の現行 draft を復元する |
| `PUT` | `/draft` | `revision` 付き全量 snapshot を直列・atomic 保存する |
| `POST` | `/finalize` | 全対象の明示判断を write-once の復核結果として保存する |
| `GET` | `/health` | CLI が PID だけでなく実 daemon を確認する |
| `POST` | `/shutdown` | token と Origin を確認して保存 queue の後に停止する |

`draft` と結果は bundle の plan / decision / manifest / quality-signals SHA と各 take ID に完全に束縛する。任意 file path、directory 一覧、汎用 JSON 保存 endpoint は設けない。

## 2. 聴取者に必要な情報

- 聴取者が project の履歴や用語を知っていると仮定しない。各 round の冒頭で「何を選ぶか」と「今回は判断しないこと」を一文ずつ示す。
- 各判断に必要な evidence を同じ作業視野へ置く。役名、性別、年齢、役柄、声質、性格、場面、読み、発話状況、感情、強度、正解台本、候補、heard 状態、選択、問題、確認を一画面に集約する。
- 入力に存在しない前後行の同一性は要求しない。各候補は表示中の一行について、提示された役柄、場面、読み、感情、強度と照合する。
- model、seed、hash、conditioning などの provenance は主判断から外し、一段の disclosure に畳む。

## 3. 情報と操作の配置

- 公開後復核の主タスクは「この1件が指定された性別と全体的な声線に合うか」の一つだけとする。台詞内容、発音、音質はこの round で判断しない。
- 役の evidence → 候補 → 問題 → 確認を上から一方向に並べ、候補間の切替は一動作、選択は一動作、確認は一動作にする。
- 各項目は公開済みの selected 音声1件だけを表示し、最後まで再生した後に `符合角色` / `不符合` を選ぶ。
- 復核結果は公開 selection を書き換えない。問題が確認された slot は新しい Issue / role epoch の後続置換対象にする。
- 確認後は次の未確認 group へ自動で進む。手動 workflow 切替、folder 選択、rubric の pass/fail 切替、export は置かない。

## 4. 文言と interaction

- 作業 UI は中国語で、制御名は実行結果をそのまま書く。例: `确认并进入下一组`、`已保存 13:20:04`。
- 初期 candidate と最終評価は中立にする。bundle の provisional candidate を選択状態へ転用しない。
- 問題入口は常時見える `发现问题` とし、内容、提示語漏洩、読み、pitch、性別、年齢、役柄、自然度・音質の短い tag をその場で展開する。
- 長い説明は書かない。補助説明は `title`、disclosure、短い inline status に畳み、primary surface を押し下げない。
- 保存は `正在保存` / `已保存` / `保存失败` を focus を奪わない status として示す。保存失敗時は finalization を停止する。
- 全対象の判断後に独立した `完成复核` を表示し、使用者の明示操作だけで finalize する。
- keyboard でも全操作を完了できる。候補の直接再生 shortcut は現在表示中の候補数に合わせ、選択・問題・本組確認・最終確定まで自然な focus 順にする。

## 5. 検証基準

- 1600×900 desktop と 390×844 mobile の初期 viewport で、現在の判断内容、役 evidence、正解台本、現在の全候補、heard 数、保存状態が理解できる。
- 現在の全候補を最後まで聴く前、未選択かつ使用不可でもない時、使用不可理由がない時、保存失敗時、revision conflict 時、全 group 確認前、finalized 後に誤った確定ができない。
- refresh / daemon restart 後に同じ group と draft を結果 directory から復元する。
- 複数 tab の stale revision は 409 で拒否し、silent overwrite しない。
- public build に listening page、daemon、local API client が含まれない。

## 根拠

- [ITU-R BS.1534-3](https://www.itu.int/dms_pubrec/itu-r/rec/bs/R-REC-BS.1534-3-201510-I%21%21PDF-E.pdf): 同一 trial の多属性評価を避け、複数 stimulus を同一比較面へ置く。
- [ITU-T P.808](https://www.itu.int/rec/dologin_pub.asp?id=T-REC-P.808-202106-I%21%21PDF-E&lang=e&type=items): 評価前の stimulus 聴取を保証する。
- [W3C COGA: clear instructions](https://www.w3.org/TR/coga-usable/#use-clear-step-by-step-instructions): 活動の直前または隣に短い手順を置く。
- [W3C COGA: avoid data loss](https://www.w3.org/TR/coga-usable/#avoid-data-loss-and-timeouts): 中断可能な autosave と recovery を提供する。
- [WAI-ARIA radio group pattern](https://www.w3.org/WAI/ARIA/apg/patterns/radio/): 候補集合の keyboard navigation と選択 semantics。
- [WCAG 2.2 status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html): focus を移さず保存・失敗状態を通知する。
- [Node.js child process](https://nodejs.org/api/child_process.html#optionsdetached): detached process と stdio / `unref()` の基本条件。
- [Microsoft Win32 Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects): job 配下の child lifecycle と `Win32_Process.Create` の境界。
