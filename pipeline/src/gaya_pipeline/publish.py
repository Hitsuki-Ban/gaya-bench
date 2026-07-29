from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import dotenv_values

from gaya_pipeline.take_identity import TakeIdentityError, canonical_json
from gaya_pipeline.take_manifest_v4 import (
    TakeManifestError,
    validate_manifest_v4,
)

R2_BUCKET = "gaya-bench-audio"
CONTENT_TYPE = "audio/ogg"
CACHE_CONTROL = "public, max-age=31536000, immutable"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_ACCOUNT_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_REQUIRED_ENVIRONMENT = (
    "CLOUDFLARE_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)
_PROVENANCE_KEYS = {"format_version", "plan_sha256", "runs"}
_PROVENANCE_RUN_KEYS = {
    "model",
    "run_id",
    "ledger_path",
    "ledger_sha256",
    "qc_report_sha256",
    "manifest_sha256",
    "candidate_set_sha256",
}


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
    release_root = _require_directory(release_dir, "release directory")
    takes_root_resolved = _require_directory(takes_root, "takes root")
    manifest = _load_marked_manifest(
        release_root / "manifest-v4.json",
        release_root / "manifest-v4.sha256",
        "release manifest v4",
    )
    provenance = _load_provenance(release_root)
    run_by_model = {run["model"]: run for run in provenance["runs"]}
    release_models = {model["id"] for model in manifest["models"]}
    if set(run_by_model) != release_models:
        raise PublishError(
            "release provenance model coverage が release manifest と一致しません。",
        )

    source_candidates: dict[str, set[str]] = {}
    source_roots: dict[str, Path] = {}
    for model, record in run_by_model.items():
        run_root = _resolve_child(
            takes_root_resolved,
            record["run_id"],
            f"source run {record['run_id']}",
        )
        source_manifest_path = run_root / "manifest-v4.json"
        source_manifest, raw = _load_source_manifest(
            source_manifest_path,
            f"source manifest {record['run_id']}",
        )
        if hashlib.sha256(raw).hexdigest() != record["manifest_sha256"]:
            raise PublishError(
                f"source manifest SHA が release provenance と一致しません: "
                f"{record['run_id']}",
            )
        if source_manifest["candidate_set_sha256"] != record["candidate_set_sha256"]:
            raise PublishError(
                f"source candidate set SHA が release provenance と一致しません: "
                f"{record['run_id']}",
            )
        if [entry["id"] for entry in source_manifest["models"]] != [model]:
            raise PublishError(
                f"source manifest model が release provenance と一致しません: "
                f"{record['run_id']}",
            )
        source_candidates[model] = {
            canonical_json(candidate) for candidate in source_manifest["candidates"]
        }
        source_roots[model] = run_root

    objects: list[_PublishObject] = []
    keys: set[str] = set()
    for candidate in manifest["candidates"]:
        model = candidate["model"]
        if model not in source_candidates:
            raise PublishError(f"release candidate の source run がありません: {model}")
        if canonical_json(candidate) not in source_candidates[model]:
            raise PublishError(
                "release candidate が provenance source manifest と exact に"
                f"一致しません: {candidate['path']}",
            )
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
            source_roots[model],
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


def _load_marked_manifest(
    path: Path,
    marker_path: Path,
    label: str,
) -> dict[str, Any]:
    manifest, raw = _load_canonical_manifest(path, label)
    expected_sha = _load_sha_marker(marker_path, label)
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise PublishError(f"{label} の raw SHA marker が一致しません。")
    return manifest


def _load_canonical_manifest(
    path: Path,
    label: str,
) -> tuple[dict[str, Any], bytes]:
    raw, document = _load_canonical_json(path, label)
    try:
        manifest = validate_manifest_v4(document)
    except (TakeManifestError, ValueError) as error:
        raise PublishError(f"{label} が v4 schema を満たしません: {error}") from error
    try:
        canonical = canonical_json(manifest).encode("utf-8")
    except TakeIdentityError as error:
        raise PublishError(f"{label} を canonicalize できません。") from error
    if raw != canonical:
        raise PublishError(f"{label} は canonical bytes が必要です。")
    return manifest, raw


def _load_source_manifest(
    path: Path,
    label: str,
) -> tuple[dict[str, Any], bytes]:
    raw, document = _load_canonical_json(path, label)
    try:
        manifest = validate_manifest_v4(document)
    except (TakeManifestError, ValueError) as error:
        raise PublishError(f"{label} が v4 schema を満たしません: {error}") from error
    return manifest, raw


def _load_provenance(release_root: Path) -> dict[str, Any]:
    path = release_root / "baseline-provenance.json"
    raw, document = _load_canonical_json(path, "release provenance")
    _load_and_verify_marker(
        release_root / "baseline-provenance.sha256",
        raw,
        "release provenance",
    )
    if not isinstance(document, dict) or set(document) != _PROVENANCE_KEYS:
        raise PublishError("release provenance の root 項目が不正です。")
    if document["format_version"] != 1:
        raise PublishError("release provenance format_version は 1 が必要です。")
    _require_sha(document["plan_sha256"], "release provenance plan_sha256")
    if not isinstance(document["runs"], list) or not document["runs"]:
        raise PublishError("release provenance runs は非空の配列が必要です。")

    runs: list[dict[str, str]] = []
    for index, value in enumerate(document["runs"]):
        field = f"release provenance runs[{index}]"
        if not isinstance(value, dict) or set(value) != _PROVENANCE_RUN_KEYS:
            raise PublishError(f"{field} の項目が不正です。")
        if not all(
            isinstance(value[key], str)
            for key in _PROVENANCE_RUN_KEYS
        ):
            raise PublishError(f"{field} の全項目は文字列が必要です。")
        run = {key: value[key] for key in _PROVENANCE_RUN_KEYS}
        for key in ("model", "run_id"):
            _require_path_segment(run[key], f"{field}.{key}")
        for key in (
            "ledger_sha256",
            "qc_report_sha256",
            "manifest_sha256",
            "candidate_set_sha256",
        ):
            _require_sha(run[key], f"{field}.{key}")
        if not run["ledger_path"]:
            raise PublishError(f"{field}.ledger_path は非空文字列が必要です。")
        runs.append(run)
    if runs != sorted(runs, key=lambda run: run["model"]):
        raise PublishError("release provenance runs は model 順が必要です。")
    if len({run["model"] for run in runs}) != len(runs):
        raise PublishError("release provenance model が重複しています。")
    if len({run["run_id"] for run in runs}) != len(runs):
        raise PublishError("release provenance run_id が重複しています。")
    normalized = {
        "format_version": 1,
        "plan_sha256": document["plan_sha256"],
        "runs": runs,
    }
    if raw != canonical_json(normalized).encode("utf-8"):
        raise PublishError("release provenance は canonical bytes が必要です。")
    return normalized


def _load_canonical_json(path: Path, label: str) -> tuple[bytes, Any]:
    if not path.is_file():
        raise PublishError(f"{label} が存在しません: {path}")
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublishError(f"{label} を読み込めません: {path}") from error
    return raw, document


def _load_and_verify_marker(marker_path: Path, raw: bytes, label: str) -> None:
    marker = _load_sha_marker(marker_path, label)
    if hashlib.sha256(raw).hexdigest() != marker:
        raise PublishError(f"{label} の raw SHA marker が一致しません。")


def _load_sha_marker(path: Path, label: str) -> str:
    if not path.is_file():
        raise PublishError(f"{label} SHA marker が存在しません: {path}")
    try:
        marker = path.read_bytes()
    except OSError as error:
        raise PublishError(f"{label} SHA marker を読み込めません: {path}") from error
    try:
        text = marker.decode("ascii")
    except UnicodeDecodeError as error:
        raise PublishError(f"{label} SHA marker が ASCII ではありません。") from error
    _require_sha(text, f"{label} SHA marker")
    return text


def _require_directory(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise PublishError(f"{label} が存在しません: {path}") from error
    if not resolved.is_dir():
        raise PublishError(f"{label} が directory ではありません: {path}")
    return resolved


def _resolve_child(root: Path, relative: str | Path, label: str) -> Path:
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise PublishError(f"{label} が存在しません: {candidate}") from error
    if not resolved.is_relative_to(root):
        raise PublishError(f"{label} が root 外を参照しています: {candidate}")
    return resolved


def _require_path_segment(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise PublishError(f"{field} は安全な path segment が必要です。")
    return value


def _require_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise PublishError(f"{field} は完全な小文字 SHA-256 が必要です。")
    return value


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
