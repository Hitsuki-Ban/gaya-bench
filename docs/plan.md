# マスタープラン

最終更新: 2026-07-27 (Director: Claude)

## ゴール

1. **本公開 (v1)**: シーン×キャラ×セリフ×モデルのガヤボイス生成結果を、快適なUXで聴き比べできるベンチサイトを Cloudflare Pages で一般公開する
2. **+α (v2)**: セリフ・キャラ設定を入力してその場で生成できるインタラクティブデモ

## 体制

| 役割 | 担当 | 責務 |
| --- | --- | --- |
| Owner | @Hitsuki-Ban | 最終判断、開発機・Cloudflare・GitHubの権限元 |
| Director | Claude (Fable 5) | 設計、UX判断、シナリオ執筆、リサーチ発注、マージ履歴レビュー、チケット管理 |
| Worker | Codex | コード実装全般、独立調査、デザイン参考画像・素材画像の生成 |

作業フローは [AGENTS.md](../AGENTS.md) を参照。**Issueがすべての作業の起点**。

## マイルストーン / チケットマップ

### M1: 基盤 (リサーチ・CI・スキーマ)
- [#1](https://github.com/Hitsuki-Ban/gaya-bench/issues/1) モデル最新動向リサーチ (Claude調査 + Codex独立検証 → 統合レポート) `P1`
- [#2](https://github.com/Hitsuki-Ban/gaya-bench/issues/2) ベンチ/試聴系サイトUX調査 + デザイン参考画像
- [#3](https://github.com/Hitsuki-Ban/gaya-bench/issues/3) CI構築 `P1`
- [#4](https://github.com/Hitsuki-Ban/gaya-bench/issues/4) pipeline雛形 + `gaya validate` `P1`

### M2: 生成パイプライン
- [#5](https://github.com/Hitsuki-Ban/gaya-bench/issues/5) 生成ハーネスコア `P1` (依存: #4)
- [#6](https://github.com/Hitsuki-Ban/gaya-bench/issues/6) R2 + `gaya publish` (依存: #5)
- [#7](https://github.com/Hitsuki-Ban/gaya-bench/issues/7) 参照音声キット
- [#8](https://github.com/Hitsuki-Ban/gaya-bench/issues/8) モデル別アダプタ実装エピック `P1` (依存: #1, #5)
  - 波1: [#23](https://github.com/Hitsuki-Ban/gaya-bench/issues/23) Qwen3-TTS / [#24](https://github.com/Hitsuki-Ban/gaya-bench/issues/24) Irodori-TTS — 波2: [#26](https://github.com/Hitsuki-Ban/gaya-bench/issues/26) AivisSpeech / [#28](https://github.com/Hitsuki-Ban/gaya-bench/issues/28) GPT-SoVITS — 波3: [#27](https://github.com/Hitsuki-Ban/gaya-bench/issues/27) VoxCPM2 / [#30](https://github.com/Hitsuki-Ban/gaya-bench/issues/30) Chatterbox v3 — 波4: [#35](https://github.com/Hitsuki-Ban/gaya-bench/issues/35) CosyVoice 3 / [#31](https://github.com/Hitsuki-Ban/gaya-bench/issues/31) Supertonic 3 (凍結ベースライン)
  - [#25](https://github.com/Hitsuki-Ban/gaya-bench/issues/25) Step-Audio-EditX はブロック中 (重みライセンス一次情報待ち)、#29 MOSS-TTS はクローズ (12GB超)
  - 確定リスト: `docs/research/models-final.md` (#1完了、#32でDirector承認)

### M3: コンテンツ (Director担当)
- [#9](https://github.com/Hitsuki-Ban/gaya-bench/issues/9) テストシナリオv1 (8シーン・約80行)
- [#10](https://github.com/Hitsuki-Ban/gaya-bench/issues/10) バッチ生成ラン (依存: #8全部, #9)

### M4: ベンチサイト
- [#11](https://github.com/Hitsuki-Ban/gaya-bench/issues/11) サイト骨組み `P1` (依存: #3)
- [#12](https://github.com/Hitsuki-Ban/gaya-bench/issues/12) 比較マトリクス + プレイヤーUX `P1` (依存: #11)
- [#13](https://github.com/Hitsuki-Ban/gaya-bench/issues/13) シナリオビュー + フィルタ + モデル詳細 (依存: #11)
- [#14](https://github.com/Hitsuki-Ban/gaya-bench/issues/14) A/Bブラインドモード (依存: #12)
- [#15](https://github.com/Hitsuki-Ban/gaya-bench/issues/15) Cloudflare Pagesデプロイ (依存: #11, #6)
- [#16](https://github.com/Hitsuki-Ban/gaya-bench/issues/16) ビジュアルポリッシュ (依存: #2, #12, #13)

### M5: 本公開
- [#17](https://github.com/Hitsuki-Ban/gaya-bench/issues/17) クレジット/ライセンスページ `P1` (依存: #7, #8, #11) — 完了
- [#18](https://github.com/Hitsuki-Ban/gaya-bench/issues/18) QA・公開チェックリスト・本公開 `P1` (依存: #10軽量版, #154 P0)

#### 軽量公開計画 (Owner決定 2026-07-30)

人手聴取を公開ゲートから除外し、公開を高速化する。詳細は各Issueのコメント参照。

- **公開ゲート (must)**: ①全モデル×161行の N=1 生成+自動ゲートのみ (#10縮小版。人手策展なしでリリース確定、soft signalは破棄せずUIバッジ表示) ②#154のP0 (内部用語排除・空セル静音化・日本語ラベル・ヒーロー1行化・音声あり既定表示・QC注記バッジ) ③#18最小QA (再生機械チェック・主要ブラウザ+モバイル1台・リンク切れ・OGP)
- **公開後トラック (v1.1+)**: 人手聴取は週次1モデル等の持続ペース / A/Bブラインドで訪問者評価を収集 / Nテイク増量+人手選抜の「ベスト版」レーン / モデル別診断 (#158の5参照診断含む) / #154 P1フルリデザイン
- 原則: モデル単位の公開除外はしない。品質問題は注記して見せる (ベンチの成果物として)。サイトに「品質注記は自動判定・人手検証は順次」の免責を明記

### M6: +α (公開後)
- [#19](https://github.com/Hitsuki-Ban/gaya-bench/issues/19) インタラクティブ生成デモ設計
- [#20](https://github.com/Hitsuki-Ban/gaya-bench/issues/20) 走り書き→LLM構造化フロー (`gaya draft`)
- [#21](https://github.com/Hitsuki-Ban/gaya-bench/issues/21) sceneバリアント (中距離シミュレーション)
- [#22](https://github.com/Hitsuki-Ban/gaya-bench/issues/22) シーン喧騒ミキサー
- RunPodによる大型モデル追加ベンチ (必要になり次第起票)

### M7: 品質強化 (2026-07-28 Owner方針)

一発ポン出しでの実用化は困難という初期ラン所見を受けた品質フェーズ。
運用方向: **複数モデル × 複数テイクの数打ちガチャ + 自動品質ゲートで足切り → 人間は通過テイクだけ選抜**。

- [#74](https://github.com/Hitsuki-Ban/gaya-bench/issues/74) 演技力・表現力の再現手法 独立調査 `P1` (Claude 3方向調査 `docs/research/expressiveness/` との対照統合)
- [#75](https://github.com/Hitsuki-Ban/gaya-bench/issues/75) 読み・リズム・トーンの自動検証と自動調整 `P1` (実例: 辛い=からい/つらいの誤読。ASRラウンドトリップ + 曖昧読み警告)
- [#76](https://github.com/Hitsuki-Ban/gaya-bench/issues/76) 数打ちガチャ運用: 品質ゲート付きテイク選抜ハーネス `P1` `epic` (設計先行、依存: #74 #75)

### クリティカルパス

`#1 → #8(アダプタ群)` と `#3 → #4 → #5` が合流して `#10(生成ラン)`、
サイト側は `#11 → #12` 。最後に `#17 → #18(本公開)`。

## 進行ルール

- **mainへの直pushは原則禁止 (Directorのコンテンツ更新も含む)**。scenarios/ や data/manifest.json の更新もPRを作りCIグリーンを確認してからマージする (2026-07-28、#49の教訓)
- チケットの依存関係はIssue本文の「依存」欄に記載
- Directorのシナリオ・ドキュメント作業も同様にIssue化してトラッキングする (`claude` ラベル)
- 生成モデルのショートリストは `docs/research/` の統合レポートで確定し、[model-criteria.md](model-criteria.md) に反映する
