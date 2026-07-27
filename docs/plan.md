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
  - Tier1: [#23](https://github.com/Hitsuki-Ban/gaya-bench/issues/23) Qwen3-TTS / [#24](https://github.com/Hitsuki-Ban/gaya-bench/issues/24) Irodori-TTS / [#25](https://github.com/Hitsuki-Ban/gaya-bench/issues/25) Step-Audio-EditX / [#26](https://github.com/Hitsuki-Ban/gaya-bench/issues/26) AivisSpeech
  - Tier2: [#27](https://github.com/Hitsuki-Ban/gaya-bench/issues/27) VoxCPM2 / [#28](https://github.com/Hitsuki-Ban/gaya-bench/issues/28) GPT-SoVITS / [#29](https://github.com/Hitsuki-Ban/gaya-bench/issues/29) MOSS-TTS / [#30](https://github.com/Hitsuki-Ban/gaya-bench/issues/30) Chatterbox v3 / [#31](https://github.com/Hitsuki-Ban/gaya-bench/issues/31) Supertonic 3
  - 着手ゲート: #1 の `models-final.md` 検証通過後

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
- [#17](https://github.com/Hitsuki-Ban/gaya-bench/issues/17) クレジット/ライセンスページ `P1` (依存: #7, #8, #11)
- [#18](https://github.com/Hitsuki-Ban/gaya-bench/issues/18) QA・公開チェックリスト・本公開 `P1` (依存: M4全部, #10, #17)

### M6: +α (公開後)
- [#19](https://github.com/Hitsuki-Ban/gaya-bench/issues/19) インタラクティブ生成デモ設計
- [#20](https://github.com/Hitsuki-Ban/gaya-bench/issues/20) 走り書き→LLM構造化フロー (`gaya draft`)
- [#21](https://github.com/Hitsuki-Ban/gaya-bench/issues/21) sceneバリアント (中距離シミュレーション)
- [#22](https://github.com/Hitsuki-Ban/gaya-bench/issues/22) シーン喧騒ミキサー
- RunPodによる大型モデル追加ベンチ (必要になり次第起票)

### クリティカルパス

`#1 → #8(アダプタ群)` と `#3 → #4 → #5` が合流して `#10(生成ラン)`、
サイト側は `#11 → #12` 。最後に `#17 → #18(本公開)`。

## 進行ルール

- チケットの依存関係はIssue本文の「依存」欄に記載
- Directorのシナリオ・ドキュメント作業も同様にIssue化してトラッキングする (`claude` ラベル)
- 生成モデルのショートリストは `docs/research/` の統合レポートで確定し、[model-criteria.md](model-criteria.md) に反映する
