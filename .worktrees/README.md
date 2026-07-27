# .worktrees/ — 作業用worktree置き場

Codex等の作業エージェントがIssue単位のworktreeをここに作る。中身はgit管理外 (このREADMEのみ追跡)。

## 使い方

```bash
# Issue #12 に着手する例
git worktree add .worktrees/12-matrix-view -b codex/12-matrix-view

# 作業完了 (PRマージ後) の後片付け
git worktree remove .worktrees/12-matrix-view
git branch -d codex/12-matrix-view
```

## 規約

- ディレクトリ名: `<issue番号>-<slug>` (ブランチ名 `codex/<issue番号>-<slug>` と対応)
- 1 Issue = 1 worktree。マージ後は必ず remove して残骸を残さない
- worktree内でも依存インストールは各ディレクトリで独立に行う (`pnpm install` / `uv sync`)
- `.worktrees/` 内を別worktreeのネスト先にしない
