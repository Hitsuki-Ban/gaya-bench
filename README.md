# Gaya Bench — RPGモブNPCガヤボイス TTS聴き比べベンチ

[![CI](https://github.com/Hitsuki-Ban/gaya-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/Hitsuki-Ban/gaya-bench/actions/workflows/ci.yml)

RPGのモブNPCが発する「ガヤボイス」(中距離で聞こえる一言セリフ・環境喧騒の素材) を、
最新のTTSモデル群で**同一の構造化シナリオ**から生成し、Web上で聴き比べできるようにするリサーチプロジェクト。

- 対象: 日本語優先 / 生成物が商用利用可能なモデル / ローカル実行可能 (RTX 4070 Ti 12GB) を軸に選定
- 成果物: シーン×キャラ×セリフ×モデルの生成結果マトリクスを、快適なUXで聴き比べできる静的サイト (Cloudflare Pages)
- +α: セリフとキャラ設定を書いてその場で生成するインタラクティブデモ

## リポジトリ構成

```
docs/               設計ドキュメント・リサーチ成果物
scenarios/          テストシナリオ (YAML, 真実の源) + JSONスキーマ
pipeline/           生成パイプライン (Python / uv)
site/               ベンチサイト (Vite Plus + React + shadcn/ui)
assets/voices/      クローン用参照音声キット (権利確認済みのみ)
artifacts/          生成物の一次出力 (git管理外)
```

## 主要ドキュメント

| ドキュメント | 内容 |
| --- | --- |
| [docs/plan.md](docs/plan.md) | マスタープラン・マイルストーン・チケットマップ |
| [docs/architecture.md](docs/architecture.md) | システム構成 (パイプライン・ストレージ・CI/CD) |
| [docs/scenario-format.md](docs/scenario-format.md) | シナリオ構造化フォーマット仕様 |
| [docs/content-plan.md](docs/content-plan.md) | テストシナリオのカバレッジ計画・執筆ガイド |
| [docs/ux-spec.md](docs/ux-spec.md) | ベンチサイトUX仕様 |
| [docs/model-criteria.md](docs/model-criteria.md) | ベンチ対象モデルの選定基準 |

## 役割分担

- **Owner**: [@Hitsuki-Ban](https://github.com/Hitsuki-Ban)
- **Director** (設計・シナリオ・レビュー・発注管理): Claude (Fable 5)
- **Worker** (実装・調査・素材生成): Codex — Issue駆動で作業、PRを自主マージ。規約は [AGENTS.md](AGENTS.md)

## ライセンス

- コード: [MIT](LICENSE)
- シナリオテキスト (`scenarios/`): CC BY 4.0
- 生成音声: 各TTSモデルの利用規約に従う (サイト内クレジットページに明記)
