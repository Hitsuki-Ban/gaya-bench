from __future__ import annotations

import errno
import os
import stat
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


class RunLockError(RuntimeError):
    pass


_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_LOCK_RETRY_INTERVAL_SEC = 0.05


def _thread_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve()))
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


@contextmanager
def exclusive_run_lock(run_root: Path) -> Iterator[None]:
    lock_path = run_root / ".run.lock"
    if sys.platform == "win32":
        lock_kind = "windows"
    elif os.name == "posix":
        lock_kind = "posix"
    else:
        raise RunLockError(
            f"run lock を実装していない OS です: {sys.platform}/{os.name}",
        )
    if not run_root.is_dir():
        raise RunLockError(f"run root が directory ではありません: {run_root}")

    _assert_not_symlink_or_reparse(lock_path)
    with _thread_lock(lock_path):
        _assert_not_symlink_or_reparse(lock_path)
        try:
            handle = lock_path.open("a+b")
        except OSError as error:
            raise RunLockError(
                f"run lock file を開けません: {lock_path}: {error}",
            ) from error
        try:
            _verify_open_file_identity(lock_path, handle)
            _ensure_lock_byte(handle)
            _lock_file(handle, lock_kind)
            try:
                yield
            finally:
                _unlock_file(handle, lock_kind)
        finally:
            try:
                handle.close()
            except OSError as error:
                raise RunLockError(
                    f"run lock file を閉じられません: {lock_path}: {error}",
                ) from error


def _assert_not_symlink_or_reparse(path: Path) -> None:
    if path.is_symlink():
        raise RunLockError(f"run lock file に symlink は使用できません: {path}")
    try:
        entry = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise RunLockError(
            f"run lock file を lstat できません: {path}: {error}",
        ) from error
    if stat.S_ISLNK(entry.st_mode) or (
        getattr(entry, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise RunLockError(
            f"run lock file に symlink/reparse point は使用できません: {path}",
        )


def _verify_open_file_identity(path: Path, handle: BinaryIO) -> None:
    try:
        entry = os.lstat(path)
        opened = os.fstat(handle.fileno())
    except OSError as error:
        raise RunLockError(
            f"run lock file identity を確認できません: {path}: {error}",
        ) from error
    if stat.S_ISLNK(entry.st_mode) or (
        getattr(entry, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise RunLockError(
            f"run lock file に symlink/reparse point は使用できません: {path}",
        )
    if not os.path.samestat(entry, opened):
        raise RunLockError(f"run lock file が open 中に置換されました: {path}")
    if entry.st_nlink != 1 or opened.st_nlink != 1:
        raise RunLockError(
            f"run lock file の hardlink/link count が不正です: {path}",
        )


def _ensure_lock_byte(handle: BinaryIO) -> None:
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
    except OSError as error:
        raise RunLockError(f"run lock byte を準備できません: {error}") from error


def _lock_file(handle: BinaryIO, lock_kind: str) -> None:
    conflict_errnos = {errno.EACCES, errno.EAGAIN}
    if lock_kind == "windows":
        conflict_errnos.add(errno.EDEADLOCK)
    while True:
        try:
            _lock_once(handle, lock_kind)
            return
        except ImportError as error:
            raise RunLockError(f"run lock を取得できません: {error}") from error
        except OSError as error:
            if error.errno not in conflict_errnos:
                raise RunLockError(
                    f"run lock を取得できません: {error}",
                ) from error
            time.sleep(_LOCK_RETRY_INTERVAL_SEC)


def _lock_once(handle: BinaryIO, lock_kind: str) -> None:
    handle.seek(0)
    if lock_kind == "windows":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: BinaryIO, lock_kind: str) -> None:
    try:
        handle.seek(0)
        if lock_kind == "windows":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError) as error:
        raise RunLockError(f"run lock を解放できません: {error}") from error
