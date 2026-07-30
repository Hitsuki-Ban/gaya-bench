from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GROUP_FIELDS = ("model", "scenario", "line", "variant")


class PublicAudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublicAudioSummary:
    verified_count: int
    total_bytes: int
    elapsed_seconds: float


@dataclass(frozen=True)
class _PublicAudio:
    take_id: str
    path: str
    sha256: str


def verify_public_audio(
    *,
    manifest_path: Path,
    base_url: str,
    workers: int,
    timeout_seconds: float,
) -> PublicAudioSummary:
    if workers < 1:
        raise PublicAudioError("workers は 1 以上で指定してください。")
    if timeout_seconds <= 0:
        raise PublicAudioError("timeout-seconds は正数で指定してください。")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise PublicAudioError("ffmpeg が見つかりません。")

    normalized_base_url = _validate_base_url(base_url)
    selected_audio = _load_selected_audio(manifest_path)
    started_at = time.perf_counter()
    total_bytes = 0
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _verify_one,
                audio=audio,
                base_url=normalized_base_url,
                ffmpeg=ffmpeg,
                timeout_seconds=timeout_seconds,
            ): audio
            for audio in selected_audio
        }
        for future in as_completed(futures):
            audio = futures[future]
            try:
                total_bytes += future.result()
            except PublicAudioError as error:
                failures.append(f"{audio.take_id} ({audio.path}): {error}")

    if failures:
        failures.sort()
        visible = failures[:20]
        suffix = (
            f"\n... 残り {len(failures) - len(visible)} 件"
            if len(failures) > len(visible)
            else ""
        )
        raise PublicAudioError(
            f"公開音声の検証に {len(failures)} 件失敗しました:\n"
            + "\n".join(f"- {failure}" for failure in visible)
            + suffix,
        )

    return PublicAudioSummary(
        verified_count=len(selected_audio),
        total_bytes=total_bytes,
        elapsed_seconds=time.perf_counter() - started_at,
    )


def _validate_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/")
    ):
        raise PublicAudioError(
            "base-url は末尾が / の HTTPS URL を指定してください。",
        )
    return base_url


def _load_selected_audio(manifest_path: Path) -> tuple[_PublicAudio, ...]:
    try:
        document = json.loads(manifest_path.read_bytes())
    except OSError as error:
        raise PublicAudioError(
            f"manifest を読み込めません: {manifest_path}: {error}",
        ) from error
    except json.JSONDecodeError as error:
        raise PublicAudioError(
            f"manifest が JSON ではありません: {manifest_path}: {error}",
        ) from error

    if not isinstance(document, dict) or document.get("format_version") != 4:
        raise PublicAudioError("manifest は format_version 4 が必要です。")
    candidates = document.get("candidates")
    curations = document.get("curations")
    if not isinstance(candidates, list) or not isinstance(curations, list):
        raise PublicAudioError(
            "manifest の candidates / curations は配列が必要です。",
        )

    candidates_by_take_id: dict[str, dict[str, Any]] = {}
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise PublicAudioError(f"candidates[{index}] は object が必要です。")
        take_id = candidate.get("take_id")
        if not isinstance(take_id, str) or not _SHA256_PATTERN.fullmatch(take_id):
            raise PublicAudioError(
                f"candidates[{index}].take_id は SHA-256 が必要です。",
            )
        if take_id in candidates_by_take_id:
            raise PublicAudioError(f"candidate take_id が重複しています: {take_id}")
        candidates_by_take_id[take_id] = candidate

    selected: list[_PublicAudio] = []
    selected_take_ids: set[str] = set()
    for index, curation in enumerate(curations):
        if not isinstance(curation, dict):
            raise PublicAudioError(f"curations[{index}] は object が必要です。")
        if curation.get("decision") != "selected":
            continue
        take_id = curation.get("take_id")
        if not isinstance(take_id, str) or take_id not in candidates_by_take_id:
            raise PublicAudioError(
                f"curations[{index}] の selected take_id に candidate がありません。",
            )
        if take_id in selected_take_ids:
            raise PublicAudioError(
                f"selected take_id が重複しています: {take_id}",
            )
        candidate = candidates_by_take_id[take_id]
        for field in _GROUP_FIELDS:
            if curation.get(field) != candidate.get(field):
                raise PublicAudioError(
                    f"curations[{index}].{field} が candidate と一致しません。",
                )
        path = candidate.get("path")
        sha256 = candidate.get("sha256")
        if not isinstance(path, str) or not _is_safe_audio_path(path):
            raise PublicAudioError(
                f"candidate {take_id} の path が不正です: {path!r}",
            )
        if not isinstance(sha256, str) or not _SHA256_PATTERN.fullmatch(sha256):
            raise PublicAudioError(
                f"candidate {take_id} の sha256 が不正です。",
            )
        selected.append(_PublicAudio(take_id, path, sha256))
        selected_take_ids.add(take_id)

    if not selected:
        raise PublicAudioError("selected curation がありません。")
    return tuple(selected)


def _is_safe_audio_path(value: str) -> bool:
    parsed = urlsplit(value)
    path = PurePosixPath(value)
    return (
        parsed.scheme == ""
        and parsed.netloc == ""
        and parsed.query == ""
        and parsed.fragment == ""
        and "\\" not in value
        and not path.is_absolute()
        and len(path.parts) >= 2
        and path.parts[0] == "audio"
        and all(part not in ("", ".", "..") for part in path.parts)
        and path.suffix == ".opus"
    )


def _verify_one(
    *,
    audio: _PublicAudio,
    base_url: str,
    ffmpeg: str,
    timeout_seconds: float,
) -> int:
    url = urljoin(base_url, audio.path)
    request = Request(
        url,
        headers={
            "Accept": "audio/ogg",
            "User-Agent": "gaya-public-audio-verifier/1",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = response.status
            content_type = response.headers.get_content_type()
            body = response.read()
    except (OSError, URLError) as error:
        raise PublicAudioError(f"GET に失敗しました: {url}: {error}") from error

    if status != 200:
        raise PublicAudioError(f"HTTP status が 200 ではありません: {status}")
    if content_type != "audio/ogg":
        raise PublicAudioError(
            f"Content-Type が audio/ogg ではありません: {content_type}",
        )
    if not body:
        raise PublicAudioError("response body が空です。")
    actual_sha256 = hashlib.sha256(body).hexdigest()
    if actual_sha256 != audio.sha256:
        raise PublicAudioError(
            f"SHA-256 が一致しません: expected={audio.sha256} "
            f"actual={actual_sha256}",
        )

    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-xerror",
                "-abort_on",
                "empty_output",
                "-i",
                "pipe:0",
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ],
            input=body,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublicAudioError(f"FFmpeg の実行に失敗しました: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise PublicAudioError(
            f"FFmpeg decode に失敗しました (exit={result.returncode}): {detail}",
        )
    return len(body)
