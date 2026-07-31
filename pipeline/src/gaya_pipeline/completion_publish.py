from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

from botocore.exceptions import BotoCoreError, ClientError

from gaya_pipeline.completion_release import (
    CompletionReleaseError,
    validate_completion_release,
)
from gaya_pipeline.publish import CACHE_CONTROL, CONTENT_TYPE, R2_BUCKET


class CompletionPublishError(RuntimeError):
    pass


class S3Client(Protocol):
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CompletionPublishRecord:
    key: str
    source: Literal["inherited", "supplement"]
    status: Literal["verified", "uploaded", "skipped"]
    size_bytes: int


@dataclass(frozen=True)
class CompletionPublishSummary:
    records: tuple[CompletionPublishRecord, ...]

    @property
    def uploaded_count(self) -> int:
        return sum(item.status == "uploaded" for item in self.records)

    @property
    def skipped_count(self) -> int:
        return sum(item.status == "skipped" for item in self.records)

    @property
    def inherited_count(self) -> int:
        return sum(item.source == "inherited" for item in self.records)


@dataclass(frozen=True)
class _Object:
    key: str
    sha256: str
    source: Literal["inherited", "supplement"]
    content: bytes | None
    size_bytes: int | None


def run_completion_publish(
    *,
    release_dir: Path,
    artifacts_dir: Path,
    client: S3Client,
) -> CompletionPublishSummary:
    """Publish supplement bytes while treating the published base as remote-only."""

    try:
        release = validate_completion_release(
            release_dir=release_dir,
            artifacts_dir=artifacts_dir,
        )
    except CompletionReleaseError as error:
        raise CompletionPublishError(f"completion release が不正です: {error}") from error
    objects = _collect_objects(release.manifest, release.provenance, artifacts_dir)

    preflight: dict[str, dict[str, Any] | None] = {}
    conflicts: list[str] = []
    for item in objects:
        remote = _head(client, item.key)
        preflight[item.key] = remote
        if remote is None:
            if item.source == "inherited":
                conflicts.append(f"{item.key} (published base missing)")
            continue
        if not _matches(remote, item, inherited_size=None):
            conflicts.append(item.key)
    if conflicts:
        raise CompletionPublishError(
            "R2 preflight HEAD が release と競合しています: " + ", ".join(conflicts),
        )

    records: list[CompletionPublishRecord] = []
    for item in objects:
        remote = preflight[item.key]
        if item.source == "inherited":
            assert remote is not None
            records.append(
                CompletionPublishRecord(
                    key=item.key,
                    source="inherited",
                    status="verified",
                    size_bytes=_remote_size(remote, item.key),
                ),
            )
            continue
        if remote is not None:
            assert item.size_bytes is not None
            records.append(
                CompletionPublishRecord(
                    key=item.key,
                    source="supplement",
                    status="skipped",
                    size_bytes=item.size_bytes,
                ),
            )
            continue
        records.append(_put_supplement(client, item))

    final_conflicts: list[str] = []
    for item in objects:
        remote = _head(client, item.key)
        inherited_size = (
            _remote_size(preflight[item.key], item.key)
            if item.source == "inherited" and preflight[item.key] is not None
            else None
        )
        if remote is None or not _matches(
            remote,
            item,
            inherited_size=inherited_size,
        ):
            final_conflicts.append(item.key)
    if final_conflicts:
        raise CompletionPublishError(
            "R2 final full HEAD sweep に失敗しました: "
            + ", ".join(final_conflicts),
        )
    return CompletionPublishSummary(records=tuple(records))


def _collect_objects(
    manifest: dict[str, Any],
    provenance: dict[str, Any],
    artifacts_dir: Path,
) -> list[_Object]:
    supplements = {
        item["take_id"]: (run["run_id"], item)
        for run in provenance["supplement_runs"]
        for item in run["candidates"]
    }
    takes_root = artifacts_dir.resolve() / "takes"
    objects: list[_Object] = []
    keys: set[str] = set()
    for candidate in manifest["candidates"]:
        key = candidate["path"]
        if key in keys:
            raise CompletionPublishError(f"manifest candidate key が重複しています: {key}")
        keys.add(key)
        supplement = supplements.get(candidate["take_id"])
        if supplement is None:
            objects.append(
                _Object(
                    key=key,
                    sha256=candidate["sha256"],
                    source="inherited",
                    content=None,
                    size_bytes=None,
                ),
            )
            continue
        run_id, record = supplement
        run_root = _resolve_child_directory(takes_root, run_id, "supplement run")
        path = _resolve_child_file(
            run_root,
            record["run_relative_path"],
            "supplement Opus",
        )
        try:
            content = path.read_bytes()
        except OSError as error:
            raise CompletionPublishError(f"supplement Opus を読めません: {path}") from error
        if (
            len(content) != record["size_bytes"]
            or hashlib.sha256(content).hexdigest() != candidate["sha256"]
        ):
            raise CompletionPublishError(
                f"supplement Opus が release provenance と不一致です: {key}",
            )
        objects.append(
            _Object(
                key=key,
                sha256=candidate["sha256"],
                source="supplement",
                content=content,
                size_bytes=len(content),
            ),
        )
    return sorted(objects, key=lambda item: item.key)


def _head(client: S3Client, key: str) -> dict[str, Any] | None:
    try:
        return client.head_object(Bucket=R2_BUCKET, Key=key)
    except ClientError as error:
        if _is_not_found(error):
            return None
        raise CompletionPublishError(f"R2 HEAD に失敗しました: {key}") from error
    except BotoCoreError as error:
        raise CompletionPublishError(f"R2 HEAD に失敗しました: {key}") from error


def _put_supplement(
    client: S3Client,
    item: _Object,
) -> CompletionPublishRecord:
    if item.content is None or item.size_bytes is None:
        raise CompletionPublishError("inherited candidate は PUT できません。")
    content_md5 = base64.b64encode(hashlib.md5(item.content).digest()).decode("ascii")  # noqa: S324
    try:
        client.put_object(
            Bucket=R2_BUCKET,
            Key=item.key,
            Body=item.content,
            ContentLength=item.size_bytes,
            ContentType=CONTENT_TYPE,
            CacheControl=CACHE_CONTROL,
            ContentMD5=content_md5,
            Metadata={"sha256": item.sha256},
            IfNoneMatch="*",
        )
    except ClientError as error:
        if not _is_precondition_failed(error):
            raise CompletionPublishError(
                f"R2 supplement conditional PUT に失敗しました: {item.key}",
            ) from error
        remote = _head_after_412(client, item.key, error)
        if remote is None or not _matches(remote, item, inherited_size=None):
            raise CompletionPublishError(
                f"R2 supplement immutable object が競合しました: {item.key}",
            ) from error
        return CompletionPublishRecord(
            key=item.key,
            source="supplement",
            status="skipped",
            size_bytes=item.size_bytes,
        )
    except BotoCoreError as error:
        raise CompletionPublishError(
            f"R2 supplement conditional PUT に失敗しました: {item.key}",
        ) from error
    return CompletionPublishRecord(
        key=item.key,
        source="supplement",
        status="uploaded",
        size_bytes=item.size_bytes,
    )


def _head_after_412(
    client: S3Client,
    key: str,
    error: ClientError,
) -> dict[str, Any] | None:
    try:
        return _head(client, key)
    except CompletionPublishError:
        raise CompletionPublishError(
            f"R2 412 後の HEAD で状態を確定できません: {key}",
        ) from error


def _matches(
    remote: dict[str, Any],
    item: _Object,
    *,
    inherited_size: int | None,
) -> bool:
    metadata = remote.get("Metadata")
    cache_control = remote.get("CacheControl")
    content_length = remote.get("ContentLength")
    if (
        not isinstance(metadata, dict)
        or metadata.get("sha256") != item.sha256
        or remote.get("ContentType") != CONTENT_TYPE
        or not isinstance(cache_control, str)
        or _cache_directives(cache_control) != _cache_directives(CACHE_CONTROL)
        or isinstance(content_length, bool)
        or not isinstance(content_length, int)
        or content_length < 1
    ):
        return False
    if item.source == "supplement":
        return content_length == item.size_bytes
    if inherited_size is not None:
        return content_length == inherited_size
    return True


def _remote_size(remote: dict[str, Any], key: str) -> int:
    value = remote.get("ContentLength")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CompletionPublishError(f"R2 ContentLength が不正です: {key}")
    return value


def _is_not_found(error: ClientError) -> bool:
    response = error.response
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = str(response.get("Error", {}).get("Code", ""))
    return status == 404 or code in {"404", "NoSuchKey", "NotFound"}


def _is_precondition_failed(error: ClientError) -> bool:
    response = error.response
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = str(response.get("Error", {}).get("Code", ""))
    return status == 412 or code in {"412", "PreconditionFailed"}


def _cache_directives(value: str) -> tuple[str, ...]:
    return tuple(sorted(item.strip().lower() for item in value.split(",")))


def _resolve_child_directory(root: Path, name: str, label: str) -> Path:
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise CompletionPublishError(f"{label} name が不正です。")
    path = root / name
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise CompletionPublishError(f"{label} が存在しません: {path}") from error
    if not resolved.is_dir() or resolved.parent != root.resolve():
        raise CompletionPublishError(f"{label} はtakes root直下directoryが必要です。")
    return resolved


def _resolve_child_file(root: Path, relative: str, label: str) -> Path:
    pure = PurePosixPath(relative)
    path = root / Path(*pure.parts)
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise CompletionPublishError(f"{label} が存在しません: {path}") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise CompletionPublishError(f"{label} はrun root内通常ファイルが必要です。")
    return resolved
