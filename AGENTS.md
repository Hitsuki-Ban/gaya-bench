# AGENTS.md — 作業エージェント向けリポジトリ規約

このリポジトリは RPGモブNPC用ガヤボイスのTTSベンチマークプロジェクト。プロジェクト全体像は [README.md](README.md) と [docs/plan.md](docs/plan.md) を最初に読むこと。

## 作業フロー (Issue駆動)

1. 作業は必ずGitHub Issueを起点とする。Issueの「受け入れ条件」を満たすことがゴール
2. ブランチ名: `codex/<issue番号>-<短いslug>`。作業は**worktree**で行う: `git worktree add .worktrees/<issue番号>-<slug> -b codex/<issue番号>-<slug>` (詳細は [.worktrees/README.md](.worktrees/README.md)。マージ後は必ずremove)
3. PR本文に `Closes #<issue番号>` を含める
4. CIグリーンを確認したら**自主マージしてよい** (レビュー待ち不要)。Director (Claude) はマージ履歴を事後レビューする
5. 実装中に発見した別スコープのバグ・改善点は、そのPRに含めず**新規Issueを起票**する
6. 設計判断に迷った場合: 軽微ならIssueコメントに判断理由を残して進める。仕様に影響する場合は `question` ラベルでIssue起票し、わかる範囲の作業を先行させる

## 技術スタック (固定・変更はDirector承認必要)

- Node: **pnpm** のみ (npm / yarn 直接使用禁止)
- Python: **uv** のみ (`uv init` / `uv add` / `uv run`。pip / conda / poetry 禁止)
- サイト: Vite Plus + React + TypeScript + Tailwind CSS + shadcn/ui
- 生成パイプライン: Python 3.12 + uv
- デフォルトブランチ: `main`
- ドキュメント・Issue・PR・コミットメッセージは日本語

## 品質基準

- CI (schema validate / lint / typecheck / build) グリーン必須
- `scenarios/schema/scenario.schema.json` を変更したら [docs/scenario-format.md](docs/scenario-format.md) を同期する
- 秘密情報 (APIキー・トークン) のコミット禁止。GitHub Secrets / Cloudflare環境変数を使う
- 音声バイナリはリポジトリにコミットしない (R2へ。`artifacts/` はgit管理外)

## 実行環境の前提

- 生成ジョブの実行機: Windows 11 / RTX 4070 Ti (VRAM **12GB**) / RAM 32GB / Python 3.12 / CUDA 13.x / ffmpeg 8.x
- モデル実行構成はVRAM 12GBに収まること (量子化可)。収まらないモデルはRunPod検討として起票する

## Cloudflare

- アカウント権限あり (R2 / Pages / Workers)。リソース命名は `gaya-bench-*` プレフィックス
