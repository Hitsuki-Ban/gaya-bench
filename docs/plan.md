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

## マイルストーン

### M1: 基盤 (リサーチ・CI・スキーマ)
- モデル最新動向リサーチ (Claude側調査 `docs/research/` + Codex独立検証 → 統合レポート)
- ベンチサイトUX調査 + デザイン参考画像
- CI構築 (schema validate / lint / typecheck / build)
- シナリオスキーマ検証CLI

### M2: 生成パイプライン
- 生成ハーネスコア (アダプタIF・CLI・manifest・ラウドネス正規化・opusエンコード)
- R2ストレージ + publishコマンド
- 参照音声キット (権利確認済み素材)
- モデル別アダプタ (ショートリスト確定後に個別起票)

### M3: コンテンツ (Director担当)
- テストシナリオv1 (8シーン・約80セリフ、[content-plan.md](content-plan.md))
- 全モデル×全シナリオのバッチ生成ラン (開発機)

### M4: ベンチサイト
- サイト骨組み (Vite Plus + React + shadcn/ui + manifest読み込み)
- 比較マトリクスビュー + プレイヤーUX ([ux-spec.md](ux-spec.md))
- シナリオビュー + フィルタ + モデル詳細
- A/Bブラインドモード
- ビジュアルポリッシュ
- Cloudflare Pagesデプロイ (CI)

### M5: 本公開
- クレジット/ライセンスページ (モデル・素材・音源規約の表記)
- QA + 公開チェックリスト → 本公開

### M6: +α (公開後)
- インタラクティブ生成デモ (開発機Worker or クラウドWorker + キュー)
- 走り書き→LLM構造化フロー (人間のト書きをスキーマ準拠YAMLに変換)
- 中距離シミュレーション (sceneバリアント)
- シーン喧騒ミキサー (ガヤ重ね再生)
- RunPodによる大型モデル追加ベンチ

## 進行ルール

- チケットの依存関係はIssue本文の「依存」欄に記載
- Directorのシナリオ・ドキュメント作業も同様にIssue化してトラッキングする (`claude` ラベル)
- 生成モデルのショートリストは `docs/research/` の統合レポートで確定し、[model-criteria.md](model-criteria.md) に反映する
