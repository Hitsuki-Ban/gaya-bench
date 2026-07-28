from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

from gaya_pipeline.manifest import ManifestError, load_manifest

R2_BUCKET = "gaya-bench-audio"
CONTENT_TYPE = "audio/ogg"
CACHE_CONTROL = "public, max-age=0, must-revalidate"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
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
    size_bytes: int


def create_r2_client(repository_root: Path) -> S3Client:
    load_dotenv(repository_root / ".env", override=False)
    missing = [name for name in _REQUIRED_ENVIRONMENT if not os.environ.get(name)]
    if missing:
        raise PublishError(
            "R2 認証設定が不足しています: " + ", ".join(missing),
        )

    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    if _ACCOUNT_ID_PATTERN.fullmatch(account_id) is None:
        raise PublishError("CLOUDFLARE_ACCOUNT_ID の形式が不正です。")
    try:
        return boto3.client(
            service_name="s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )
    except BotoCoreError as error:
        raise PublishError("R2 client を初期化できません。") from error


def run_publish(
    *,
    manifest_path: Path,
    artifacts_dir: Path,
    client: S3Client,
) -> PublishSummary:
    if not manifest_path.is_file():
        raise PublishError(f"manifest が存在しません: {manifest_path}")

    try:
        manifest = load_manifest(manifest_path)
        objects = _collect_publish_objects(manifest, artifacts_dir)
    except (ManifestError, OSError, ValueError) as error:
        raise PublishError(str(error)) from error

    records: list[PublishRecord] = []
    for item in objects:
        remote = _head_object(client, item.key)
        if _matches_remote(remote, item):
            records.append(
                PublishRecord(
                    key=item.key,
                    status="skipped",
                    size_bytes=item.size_bytes,
                )
            )
            continue

        try:
            client.put_object(
                Bucket=R2_BUCKET,
                Key=item.key,
                Body=item.content,
                ContentLength=item.size_bytes,
                ContentType=CONTENT_TYPE,
                CacheControl=CACHE_CONTROL,
                Metadata={"sha256": item.sha256},
            )
        except (BotoCoreError, ClientError) as error:
            raise PublishError(f"R2 upload に失敗しました: {item.key}") from error
        records.append(
            PublishRecord(
                key=item.key,
                status="uploaded",
                size_bytes=item.size_bytes,
            )
        )

    return PublishSummary(records=tuple(records))


def _collect_publish_objects(
    manifest: dict[str, Any],
    artifacts_dir: Path,
) -> list[_PublishObject]:
    artifacts_root = artifacts_dir.resolve()
    objects: list[_PublishObject] = []
    keys: set[str] = set()

    for clip in manifest["clips"]:
        key = str(clip["path"])
        raw_segments = key.split("/")
        if (
            key.startswith("/")
            or "\\" in key
            or any(delimiter in key for delimiter in "%#?")
            or any(segment in {"", ".", ".."} for segment in raw_segments)
        ):
            raise ValueError(f"manifest clip path が不正です: {key}")

        expected_key = (
            f"audio/{clip['model']}/{clip['scenario']}/"
            f"{clip['line']}-{clip['variant']}.opus"
        )
        if key != expected_key:
            raise ValueError(
                f"manifest clip path が規定形式ではありません: {key}",
            )
        if key in keys:
            raise ValueError(f"manifest clip path が重複しています: {key}")
        keys.add(key)

        posix_path = PurePosixPath(*raw_segments)

        sha256 = str(clip["sha256"])
        if _SHA256_PATTERN.fullmatch(sha256) is None:
            raise ValueError(f"manifest clip sha256 が不正です: {key}")

        unresolved_path = artifacts_dir.joinpath(*posix_path.parts)
        try:
            file_path = unresolved_path.resolve(strict=True)
        except (FileNotFoundError, OSError) as error:
            raise ValueError(f"Opus が存在しません: {unresolved_path}") from error
        if not file_path.is_relative_to(artifacts_root) or not file_path.is_file():
            raise ValueError(f"Opus が通常ファイルではありません: {unresolved_path}")

        content = file_path.read_bytes()
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != sha256:
            raise ValueError(f"Opus sha256 が manifest と一致しません: {key}")
        objects.append(
            _PublishObject(
                key=key,
                content=content,
                sha256=sha256,
                size_bytes=len(content),
            )
        )

    return sorted(objects, key=lambda item: item.key)


def _head_object(client: S3Client, key: str) -> dict[str, Any] | None:
    try:
        return client.head_object(Bucket=R2_BUCKET, Key=key)
    except ClientError as error:
        response = error.response
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = str(response.get("Error", {}).get("Code", ""))
        if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise PublishError(f"R2 HEAD に失敗しました: {key}") from error
    except BotoCoreError as error:
        raise PublishError(f"R2 HEAD に失敗しました: {key}") from error


def _matches_remote(remote: dict[str, Any] | None, item: _PublishObject) -> bool:
    if remote is None:
        return False
    metadata = remote.get("Metadata")
    remote_cache_control = remote.get("CacheControl")
    return (
        isinstance(metadata, dict)
        and metadata.get("sha256") == item.sha256
        and remote.get("ContentLength") == item.size_bytes
        and remote.get("ContentType") == CONTENT_TYPE
        and isinstance(remote_cache_control, str)
        and _cache_control_directives(remote_cache_control)
        == _cache_control_directives(CACHE_CONTROL)
    )


def _cache_control_directives(value: str) -> tuple[str, ...]:
    return tuple(sorted(directive.strip().lower() for directive in value.split(",")))
