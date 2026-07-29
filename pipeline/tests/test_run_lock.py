from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import BinaryIO

import pytest

from gaya_pipeline import run_lock
from gaya_pipeline.run_lock import RunLockError, exclusive_run_lock


def test_lock_conflictは回数制限なしで取得まで待機する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "lock"
    lock_path.write_bytes(b"\0")
    attempts = 0
    sleeps: list[float] = []

    def conflict_then_acquire(_handle: BinaryIO, _lock_kind: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts <= 12:
            raise OSError(errno.EACCES, "lock conflict")

    monkeypatch.setattr(run_lock, "_lock_once", conflict_then_acquire)
    monkeypatch.setattr(run_lock.time, "sleep", sleeps.append)

    with lock_path.open("r+b") as handle:
        run_lock._lock_file(handle, "windows")

    assert attempts == 13
    assert sleeps == [run_lock._LOCK_RETRY_INTERVAL_SEC] * 12


def test_lock取得時の非競合errnoは即時失敗する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "lock"
    lock_path.write_bytes(b"\0")
    sleeps: list[float] = []

    def fail(_handle: BinaryIO, _lock_kind: str) -> None:
        raise OSError(errno.EIO, "io failure")

    monkeypatch.setattr(run_lock, "_lock_once", fail)
    monkeypatch.setattr(run_lock.time, "sleep", sleeps.append)

    with lock_path.open("r+b") as handle:
        with pytest.raises(RunLockError, match="io failure"):
            run_lock._lock_file(handle, "posix")

    assert sleeps == []


def test_run_lockはsymlink先を変更しない(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    external = tmp_path / "external-lock"
    external.write_bytes(b"")
    lock_path = run_root / ".run.lock"
    try:
        lock_path.symlink_to(external)
    except OSError as error:
        pytest.skip(f"symlink を作成できない環境です: {error}")

    with pytest.raises(RunLockError, match="symlink|reparse"):
        with exclusive_run_lock(run_root):
            raise AssertionError("symlink lock を取得してはいけません。")

    assert external.read_bytes() == b""


def test_run_lockはhardlink先を変更しない(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    external = tmp_path / "external-lock"
    external.write_bytes(b"")
    lock_path = run_root / ".run.lock"
    try:
        os.link(external, lock_path)
    except OSError as error:
        pytest.skip(f"hardlink を作成できない環境です: {error}")

    with pytest.raises(RunLockError, match="hardlink|link count"):
        with exclusive_run_lock(run_root):
            raise AssertionError("hardlink lock を取得してはいけません。")

    assert external.read_bytes() == b""


def test_run_lockはopen後identity確認前にlock_byteを書かない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    lock_path = run_root / ".run.lock"
    lock_path.write_bytes(b"")
    monkeypatch.setattr(run_lock.os.path, "samestat", lambda _left, _right: False)

    with pytest.raises(RunLockError, match="置換"):
        with exclusive_run_lock(run_root):
            raise AssertionError("identity 不一致の lock を取得してはいけません。")

    assert lock_path.read_bytes() == b""
