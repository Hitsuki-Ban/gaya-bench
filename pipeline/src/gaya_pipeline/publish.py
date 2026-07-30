from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import dotenv_values

from gaya_pipeline.release import (
    ReleaseError,
    validate_finalized_release,
)

R2_BUCKET = "gaya-bench-audio"
CONTENT_TYPE = "audio/ogg"
CACHE_CONTROL = "public, max-age=31536000, immutable"
_ACCOUNT_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_REQUIRED_ENVIRONMENT = (
    "CLOUDFLARE_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)
class PublishError(RuntimeError):
    pass


class S3Client(Protocol):
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PublishRecord:
    key: str
    status: Literal["uploaded", "skipped"]
    size_bytes: int


@dataclass(frozen=True)
class PublishSummary:
    records: tuple[PublishRecord, ...]

    @property
    def uploaded_count(self) -> int:
        return sum(record.status == "uploaded" for record in self.records)

    @property
    def skipped_count(self) -> int:
        return sum(record.status == "skipped" for record in self.records)


@dataclass(frozen=True)
class _PublishObject:
    key: str
    content: bytes
    sha256: str
    checksum_sha256: str
    size_bytes: int


def create_r2_client(env_file: Path) -> S3Client:
    if not env_file.is_file():
        raise PublishError(f"env file が存在しません: {env_file}")
    try:
        environment = dotenv_values(env_file, interpolate=False)
    except OSError as error:
        raise PublishError(f"env file を読み込めません: {env_file}") from error
    missing = [name for name in _REQUIRED_ENVIRONMENT if not environment.get(name)]
    if missing:
        raise PublishError(
            "R2 認証設定が不足しています: " + ", ".join(missing),
        )

    account_id = environment["CLOUDFLARE_ACCOUNT_ID"]
    if not isinstance(account_id, str) or _ACCOUNT_ID_PATTERN.fullmatch(account_id) is None:
        raise PublishError("CLOUDFLARE_ACCOUNT_ID の形式が不正です。")
    try:
        return boto3.client(
            service_name="s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=environment["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=environment["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )
    except BotoCoreError as error:
        raise PublishError("R2 client を初期化できません。") from error


def run_publish(
    *,
    release_dir: Path,
    takes_root: Path,
    client: S3Client,
) -> PublishSummary:
    objects = _collect_publish_objects(release_dir, takes_root)

    preflight: dict[str, Literal["pending", "skipped"]] = {}
    conflicts: list[str] = []
    for item in objects:
        remote = _head_object(client, item.key)
        if remote is None:
            preflight[item.key] = "pending"
        elif _matches_remote(remote, item):
            preflight[item.key] = "skipped"
        else:
            conflicts.append(item.key)
    if conflicts:
        raise PublishError(
            "immutable R2 object が release candidate と競合しています: "
            + ", ".join(conflicts),
        )

    records: list[PublishRecord] = []
    for item in objects:
        if preflight[item.key] == "skipped":
            records.append(
                PublishRecord(
                    key=item.key,
                    status="skipped",
                    size_bytes=item.size_bytes,
                ),
            )
            continue
        records.append(_put_immutable(client, item))

    final_conflicts: list[str] = []
    for item in objects:
        remote = _head_object(client, item.key)
        if remote is None or not _matches_remote(remote, item):
            final_conflicts.append(item.key)
    if final_conflicts:
        raise PublishError(
            "R2 final HEAD sweep に失敗しました: " + ", ".join(final_conflicts),
        )
    return PublishSummary(records=tuple(records))


def _collect_publish_objects(
    release_dir: Path,
    takes_root: Path,
) -> list[_PublishObject]:
    try:
        release = validate_finalized_release(
            release_dir=release_dir,
            takes_root=takes_root,
        )
    except ReleaseError as error:
        raise PublishError(f"release bundle が不正です: {error}") from error

    objects: list[_PublishObject] = []
    keys: set[str] = set()
    for candidate in release.manifest["candidates"]:
        model = candidate["model"]
        key = candidate["path"]
        if key in keys:
            raise PublishError(f"release candidate key が重複しています: {key}")
        keys.add(key)
        local_relative = Path(
            "audio",
            candidate["model"],
            candidate["scenario"],
            candidate["line"],
            candidate["variant"],
            f"take-{candidate['take_index']:04d}.opus",
        )
        file_path = _resolve_child(
            release.run_roots[model],
            local_relative,
            f"release candidate {key}",
        )
        if not file_path.is_file():
            raise PublishError(f"source Opus が通常ファイルではありません: {file_path}")
        try:
            content = file_path.read_bytes()
        except OSError as error:
            raise PublishError(f"source Opus を読み込めません: {file_path}") from error
        sha256 = hashlib.sha256(content).hexdigest()
        if sha256 != candidate["sha256"]:
            raise PublishError(f"source Opus SHA が manifest と一致しません: {key}")
        objects.append(
            _PublishObject(
                key=key,
                content=content,
                sha256=sha256,
                checksum_sha256=base64.b64encode(
                    hashlib.sha256(content).digest(),
                ).decode("ascii"),
                size_bytes=len(content),
            ),
        )
    return sorted(objects, key=lambda item: item.key)


def _resolve_child(root: Path, relative: str | Path, label: str) -> Path:
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise PublishError(f"{label} が存在しません: {candidate}") from error
    if not resolved.is_relative_to(root):
        raise PublishError(f"{label} が root 外を参照しています: {candidate}")
    return resolved


def _head_object(client: S3Client, key: str) -> dict[str, Any] | None:
    try:
        return client.head_object(
            Bucket=R2_BUCKET,
            Key=key,
            ChecksumMode="ENABLED",
        )
    except ClientError as error:
        if _is_not_found(error):
            return None
        raise PublishError(f"R2 HEAD に失敗しました: {key}") from error
    except BotoCoreError as error:
        raise PublishError(f"R2 HEAD に失敗しました: {key}") from error


def _put_immutable(client: S3Client, item: _PublishObject) -> PublishRecord:
    try:
        client.put_object(
            Bucket=R2_BUCKET,
            Key=item.key,
            Body=item.content,
            ContentLength=item.size_bytes,
            ContentType=CONTENT_TYPE,
            CacheControl=CACHE_CONTROL,
            Metadata={"sha256": item.sha256},
            IfNoneMatch="*",
            ChecksumSHA256=item.checksum_sha256,
        )
    except (BotoCoreError, ClientError) as error:
        remote = _head_after_put_failure(client, item, error)
        if remote is None:
            raise PublishError(f"R2 conditional upload に失敗しました: {item.key}") from error
        if not _matches_remote(remote, item):
            raise PublishError(
                f"immutable R2 object が concurrent upload と競合しました: {item.key}",
            ) from error
        return PublishRecord(
            key=item.key,
            status="skipped",
            size_bytes=item.size_bytes,
        )
    return PublishRecord(
        key=item.key,
        status="uploaded",
        size_bytes=item.size_bytes,
    )


def _head_after_put_failure(
    client: S3Client,
    item: _PublishObject,
    error: BotoCoreError | ClientError,
) -> dict[str, Any] | None:
    try:
        return _head_object(client, item.key)
    except PublishError:
        raise PublishError(
            f"R2 upload の成否を HEAD で確定できません: {item.key}",
        ) from error


def _matches_remote(remote: dict[str, Any], item: _PublishObject) -> bool:
    metadata = remote.get("Metadata")
    remote_cache_control = remote.get("CacheControl")
    remote_checksum = remote.get("ChecksumSHA256")
    return (
        isinstance(metadata, dict)
        and metadata.get("sha256") == item.sha256
        and remote.get("ContentLength") == item.size_bytes
        and remote.get("ContentType") == CONTENT_TYPE
        and isinstance(remote_cache_control, str)
        and _cache_control_directives(remote_cache_control)
        == _cache_control_directives(CACHE_CONTROL)
        and remote_checksum == item.checksum_sha256
    )


def _is_not_found(error: ClientError) -> bool:
    response = error.response
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = str(response.get("Error", {}).get("Code", ""))
    return status == 404 or code in {"404", "NoSuchKey", "NotFound"}


def _cache_control_directives(value: str) -> tuple[str, ...]:
    return tuple(sorted(directive.strip().lower() for directive in value.split(",")))
