# 本公開 QA チェックリスト

Issue [#18](https://github.com/Hitsuki-Ban/gaya-bench/issues/18) の
Owner コメントで確定した軽量公開ゲートを記録する。
Lighthouse と A11y の網羅確認は公開後改善へ回し、このゲートには含めない。

## 対象

- 公開サイト: <https://gaya-bench.pages.dev/>
- 公開音声: <https://audio.gaya-bench.hitsuki.space/>
- QA 対象のサイト revision:
  `c1a8f4488d5113d663c849abec69cd387cee3f63`
- 実施日: 2026-07-31 (JST)

## 公開ゲート

- [x] selected 全 1,243 clip が HTTP 200 で取得できる
- [x] selected 全 clip の Content-Type と SHA-256 が manifest と一致する
- [x] selected 全 clip を FFmpeg で最後まで decode できる
- [x] Chrome デスクトップで主要導線と音声再生が動作する
- [x] Firefox デスクトップで主要導線と音声再生が動作する
- [x] モバイル実機 1 台で下記の判断基準を確認する
- [x] サイト内導線、外部リンク、存在しない URL の表示を確認する
- [x] OGP メタデータと OGP 画像を確認する
- [x] README に公開 URL と本チェックリストへの導線がある
- [x] GitHub repository の Website に公開 URL を設定する
- [x] Owner が Issue #18 に公開承認をコメントする

## 1. 公開音声の機械チェック

次のコマンドは manifest v4 の `decision: selected` だけを対象にする。
各 URL の response body をすべて取得し、HTTP status、`audio/ogg`、
SHA-256 を照合したうえで、FFmpeg の error を fatal として audio stream を
null output へ完全 decode する。retry や別 URL への fallback は行わない。

```powershell
uv run --project pipeline --locked gaya launch verify-audio `
  --manifest data/manifest.json `
  --base-url https://audio.gaya-bench.hitsuki.space/ `
  --workers 8 `
  --timeout-seconds 30
```

2026-07-31 の結果:

```text
検証成功: 1243 clips / 32138839 bytes / 38.627s
```

実行環境は FFmpeg 8.1.1。pipeline test は 576 件すべて成功し、
15 scenario の schema validation も成功した。

## 2. デスクトップブラウザ

Playwright 1.57.0 から、インストール済み Chrome と Playwright 管理の
Firefox を headless で起動した。

| 項目 | Chrome 150.0.7871.187 | Firefox 144.0.2 |
| --- | ---: | ---: |
| サイト内 route | 26 / 26 成功 | 26 / 26 成功 |
| 初期 audio request | 0 | 0 |
| 最初の再生操作後の audio request | 1 | 1 |
| 再生中の button state | `再生` → `停止` | `再生` → `停止` |
| console error | 0 | 0 |
| uncaught page error | 0 | 0 |
| failed request | 0 | 0 |
| 横 overflow | 0 route | 0 route |

26 route は `/`、`/ab`、`/credits`、8 model page、15 scenario page。
各 route で HTTP 200、可視 `h1`、横 overflow がないことを確認した。
最初の audio は両ブラウザで再生完了後に `停止` から `再生` へ戻った。

## 3. モバイル実機

デスクトップの mobile emulation は実機確認の代わりにしない。
実機 1 台で次を確認する。

1. <https://gaya-bench.pages.dev/> を開き、横スクロールや重なりがない
2. 最初の AivisSpeech コハクの再生ボタンを押す
3. 実際に音が聞こえ、ボタン表示が `再生` から `停止` へ変わる
4. 再生後に画面下部の transport が表示され、操作できる
5. `A/B`、scenario、model、クレジットへ移動できる
6. <https://gaya-bench.pages.dev/__launch-qa-not-found__> で
   「ページが見つかりません」とホームへ戻るリンクが表示される

2026-07-31 (JST) に Owner がスマートフォンと PC の双方で上記の公開サイトを
確認し、問題なく公開可能と判断した。

補助確認として iPhone 13 emulation では、`/`、`/ab`、scenario、
model、`/credits`、存在しない URL の 6 route がすべて正常に表示され、
横 overflow、console error、page error はなかった。初期 audio request は
0、再生操作後は 1 だった。

## 4. リンクと Not Found

- 26 のサイト内 route を実ブラウザエンジンで開き、すべて正常表示
- 全 route から収集した外部 HTTP(S) link 29 件を GET し、すべて成功
- 存在しない URL はアプリの custom Not Found page を表示
- Not Found page の見出しは「ページが見つかりません」で、
  ホームへ戻るリンクが存在

Cloudflare Pages の SPA routing を使用しているため、存在しない URL の
HTTP response は app shell の 200 となり、React Router が custom Not Found
page を描画する。これは `404.html` を置かない現行 architecture の意図した動作。

## 5. OGP

トップページで次を確認した。

- `og:title`: `Gaya Bench — 日本語 TTS ボイス比較`
- `og:description`: RPG モブ NPC 向け日本語 TTS の比較内容を記載
- `og:type`: `website`
- `og:url`: `https://gaya-bench.pages.dev/`
- `og:image`: `https://gaya-bench.pages.dev/og-image.png`
- `twitter:card`: `summary_large_image`
- OGP image: HTTP 200、PNG、1,200 × 630

## 6. 公開承認

Owner は 2026-07-31 (JST) にスマートフォン / PC の確認完了と公開承認を
[Issue #18 のコメント](https://github.com/Hitsuki-Ban/gaya-bench/issues/18#issuecomment-5134714894)
として記録した。GitHub repository の Website には
<https://gaya-bench.pages.dev/> を設定済み。
