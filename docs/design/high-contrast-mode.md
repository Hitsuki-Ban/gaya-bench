# ハイコントラストモード設計

Gaya Bench の通常表示は、暗いオーディオワークベンチと琥珀色の再生位置を維持する。
OS が強制色を有効にした場合だけ、著者色ではなく利用者が選んだシステム色を使う。
テーマ切替 UI や第二の固定配色は設けない。

## 通常モードの監査

| 組み合わせ | コントラスト比 |
| --- | ---: |
| foreground / background | 16.73:1 |
| muted foreground / background | 7.28:1 |
| primary / background | 9.45:1 |
| primary foreground / primary | 9.33:1 |
| destructive / background | 6.34:1 |
| control border / card | 3.21:1 |

本文・補助文・accent・error は現在の方向を保つ。カード境界は情報階層を静かに区切る
装飾として維持し、操作対象だけ `--input` を3:1以上の境界へ分離する。

## 強制色の意味

| 用途 | 背景 | 前景 / 境界 |
| --- | --- | --- |
| ページ・カード | `Canvas` | `CanvasText` |
| ボタン | `ButtonFace` | `ButtonText` / `ButtonBorder` |
| 入力欄 | `Field` | `FieldText` / `ButtonBorder` |
| 選択・現在位置 | `Highlight` | `HighlightText` |
| リンク | `Canvas` | `LinkText` |
| 警告・破壊操作 | `Mark` | `MarkText` |
| disabled・未収録 | `Canvas` | `GrayText` |

`GrayText` は通常の補助文に使わない。`Mark` と `MarkText`、`Highlight` と
`HighlightText` のようなシステム定義の組を崩さない。通常はブラウザの色変換に任せる。
ただし Chromium のテキスト背板がシステム色の塗りと文字を分断する要素に限り、同じ規則で
背景と前景の組を明示したうえで `forced-color-adjust: none` を適用する。ページ、カード、
レイアウトコンテナなどのサブツリー全体には適用しない。

## 状態の形

- 選択: `Highlight` の実線と塗り。`aria-pressed` / `aria-selected` / `aria-current` を優先する
- 再生中・現在位置: 2px以上の実線。既存の停止アイコンと状態文も残す
- roving cursor: 要素内側の3px輪郭。実フォーカスは要素外側の3px輪郭
- error / warning: `Mark` の二重線または輪郭と、既存の警告アイコン・文言
- disabled / unavailable: `GrayText`、破線、`opacity: 1`
- capability: 対応は実線と塗り、非対応は破線
- progress: trackを実線で囲み、valueの長さを形として残す
- waveform: 経過部分を塗り、未経過部分を中抜きにする

box-shadow、半透明背景、gradient、著者指定の `accent-color` は強制色で消える前提とする。
単色 Lucide アイコンは `currentColor` を継続して使う。

## 実装契約

補償は `site/src/index.css` の単一 `@media (forced-colors: active)` に集約する。
コンポーネント側は既存 ARIA だけで状態を表せない場合に限り、外観用の `data-*` を公開する。
イベント、状態遷移、DOM順序、音声処理、評価データは変更しない。

新しい共有コンポーネントは、次を満たすこと。

1. focus を box-shadow だけで表さない
2. selected / playing / error / disabled を色だけで表さない
3. transparent border が強制色で突然現れても意味が崩れない
4. SVG の単色アイコンは `currentColor` を使う
5. 追加の強制色ルールは各 TSX に散らさず、共通メディアクエリへ置く

## 検証

- Chromium `forcedColors: "active"` で public / internal の主要画面を確認する
- desktop 1440px と mobile 390px の初期表示、選択、再生、error、disabled、focusを確認する
- 通常モードと強制色モードを別々にスクリーンショット確認する
- Windows 実機では Aquatic / Desert / Dusk / Night sky の各 Contrast Theme を最終確認する

## 参照

- [CSS Color Adjustment Module Level 1](https://www.w3.org/TR/css-color-adjust-1/)
- [CSS Color Level 4: System Colors](https://www.w3.org/TR/css-color-4/#css-system-colors)
- [WAI C40: focus の二色表示](https://www.w3.org/WAI/WCAG22/Techniques/css/C40)
- [Microsoft Edge: forced colors の実装指針](https://blogs.windows.com/msedgedev/2020/09/17/styling-for-windows-high-contrast-with-new-standards-for-forced-colors/)
- [Microsoft: Contrast themes](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/high-contrast-themes)
