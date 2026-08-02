from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import ClientError

from gaya_pipeline import completion_publish
from gaya_pipeline.completion_publish import (
    CompletionPublishError,
    run_completion_publish,
)
from gaya_pipeline.publish import CACHE_CONTROL, CONTENT_TYPE, R2_BUCKET
from gaya_pipeline.take_identity import canonical_json


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.head_calls: list[dict[str, Any]] = []
        self.put_calls: list[dict[str, Any]] = []
        self.concurrent_412: set[str] = set()

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.head_calls.append(dict(kwargs))
        key = kwargs["Key"]
        if key not in self.objects:
            raise _error("NotFound", 404, "HeadObject")
        return dict(self.objects[key])

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls.append(dict(kwargs))
        key = kwargs["Key"]
        content = kwargs["Body"]
        if key in self.concurrent_412:
            self.concurrent_412.remove(key)
            self.objects[key] = _remote(content)
            raise _error("PreconditionFailed", 412, "PutObject")
        self.objects[key] = _remote(content)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


def _error(code: str, status: int, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "test"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


def _remote(
    content: bytes,
    *,
    sha256: str | None = None,
    content_length: int | None = None,
) -> dict[str, Any]:
    return {
        "Metadata": {"sha256": sha256 or hashlib.sha256(content).hexdigest()},
        "ContentLength": len(content) if content_length is None else content_length,
        "ContentType": CONTENT_TYPE,
        "CacheControl": CACHE_CONTROL,
    }


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FakeS3, dict[str, Any], dict[str, Any], bytes]:
    inherited_content = b"published-remote-only"
    supplement_content = b"new-local-opus"
    inherited = {
        "model": "model",
        "scenario": "scene",
        "line": "old",
        "variant": "dry",
        "take_index": 1,
        "take_id": "1" * 64,
        "path": "audio/takes/model/scene/old/dry/take-0001-old.opus",
        "sha256": hashlib.sha256(inherited_content).hexdigest(),
    }
    supplement = {
        "model": "model",
        "scenario": "scene",
        "line": "new",
        "variant": "dry",
        "take_index": 1,
        "take_id": "2" * 64,
        "path": "audio/takes/model/scene/new/dry/take-0001-new.opus",
        "sha256": hashlib.sha256(supplement_content).hexdigest(),
    }
    relative = "audio/model/scene/new/dry/take-0001.opus"
    local = tmp_path / "artifacts" / "takes" / "run-new" / Path(*relative.split("/"))
    local.parent.mkdir(parents=True)
    local.write_bytes(supplement_content)
    release_root = tmp_path / "release"
    release_root.mkdir()
    manifest = {"candidates": [inherited, supplement]}
    manifest_bytes = canonical_json(manifest).encode("utf-8")
    quality_signals = {"groups": [{"model": "model"}]}
    quality_signals_bytes = canonical_json(quality_signals).encode("utf-8")
    provenance = {
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "quality_signals_sha256": hashlib.sha256(quality_signals_bytes).hexdigest(),
        "source_runs": [
            {
                "run_id": "run-new",
                "candidates": [
                    {
                        "take_id": supplement["take_id"],
                        "path": supplement["path"],
                        "audio_sha256": supplement["sha256"],
                        "run_relative_path": relative,
                        "size_bytes": len(supplement_content),
                    },
                ],
            },
        ],
    }
    (release_root / "manifest-v4.json").write_bytes(manifest_bytes)
    (release_root / "quality-signals.json").write_bytes(quality_signals_bytes)
    (release_root / "release-provenance.json").write_bytes(
        canonical_json(provenance).encode("utf-8"),
    )
    release = SimpleNamespace(
        root=release_root,
        manifest=manifest,
        quality_signals=quality_signals,
        provenance=provenance,
    )
    monkeypatch.setattr(
        completion_publish,
        "validate_completion_release",
        lambda **_kwargs: release,
    )
    client = FakeS3()
    client.objects[inherited["path"]] = _remote(inherited_content)
    return client, inherited, supplement, supplement_content


def _publish(tmp_path: Path, client: FakeS3) -> Any:
    return run_completion_publish(
        release_dir=(tmp_path / "release").resolve(),
        artifacts_dir=(tmp_path / "artifacts").resolve(),
        source_audit_path=(tmp_path / "source-audit.json").resolve(),
        client=client,
        manifest_activation_path=(tmp_path / "active-manifest.json").resolve(),
        quality_signals_activation_path=(
            tmp_path / "active-quality-signals.json"
        ).resolve(),
        publish_receipt_path=(tmp_path / "publish-receipt.json").resolve(),
    )


def test_inheritedはremote_onlyで一度もPUTしない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, inherited, supplement, content = _fixture(tmp_path, monkeypatch)

    summary = _publish(tmp_path, client)

    assert summary.inherited_count == 1
    assert summary.uploaded_count == 1
    assert (tmp_path / "active-manifest.json").read_bytes() == (
        tmp_path / "release" / "manifest-v4.json"
    ).read_bytes()
    assert (tmp_path / "active-quality-signals.json").read_bytes() == (
        tmp_path / "release" / "quality-signals.json"
    ).read_bytes()
    assert (tmp_path / "publish-receipt.json").is_file()
    assert [call["Key"] for call in client.put_calls] == [supplement["path"]]
    assert all(call["Key"] != inherited["path"] for call in client.put_calls)
    put = client.put_calls[0]
    assert put == {
        "Bucket": R2_BUCKET,
        "Key": supplement["path"],
        "Body": content,
        "ContentLength": len(content),
        "ContentType": CONTENT_TYPE,
        "CacheControl": CACHE_CONTROL,
        "ContentMD5": base64.b64encode(hashlib.md5(content).digest()).decode("ascii"),
        "Metadata": {"sha256": supplement["sha256"]},
        "IfNoneMatch": "*",
    }
    assert all(set(call) == {"Bucket", "Key"} for call in client.head_calls)


def test_inherited競合は全preflight後zero_put(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, inherited, supplement, _content = _fixture(tmp_path, monkeypatch)
    client.objects[inherited["path"]]["Metadata"]["sha256"] = "0" * 64

    with pytest.raises(CompletionPublishError, match="preflight"):
        _publish(tmp_path, client)

    assert {call["Key"] for call in client.head_calls} == {
        inherited["path"],
        supplement["path"],
    }
    assert client.put_calls == []


def test_inherited欠落を補完PUTせず硬失敗する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, inherited, _supplement, _content = _fixture(tmp_path, monkeypatch)
    del client.objects[inherited["path"]]

    with pytest.raises(CompletionPublishError, match="published base missing"):
        _publish(tmp_path, client)

    assert client.put_calls == []


def test_412はHEAD同一確認時だけidempotent成功する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _inherited, supplement, _content = _fixture(tmp_path, monkeypatch)
    client.concurrent_412.add(supplement["path"])

    summary = _publish(tmp_path, client)

    assert summary.uploaded_count == 0
    assert summary.skipped_count == 1
    assert len(client.put_calls) == 1


def test_final_HEADのContentLength漂移を拒否する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, inherited, _supplement, _content = _fixture(tmp_path, monkeypatch)
    original = client.head_object
    counts: dict[str, int] = {}

    def drifting_head(**kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        counts[key] = counts.get(key, 0) + 1
        result = original(**kwargs)
        if key == inherited["path"] and counts[key] == 2:
            result["ContentLength"] += 1
        return result

    client.head_object = drifting_head  # type: ignore[method-assign]

    with pytest.raises(CompletionPublishError, match="final full HEAD"):
        _publish(tmp_path, client)

    assert not (tmp_path / "active-manifest.json").exists()
    assert not (tmp_path / "publish-receipt.json").exists()


def test_final_HEAD中のdisk_manifest漂移はpreflight固定bytesをactivateする(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _inherited, supplement, _content = _fixture(tmp_path, monkeypatch)
    original_bytes = (tmp_path / "release" / "manifest-v4.json").read_bytes()
    original_head = client.head_object
    counts: dict[str, int] = {}

    def replacing_head(**kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        counts[key] = counts.get(key, 0) + 1
        result = original_head(**kwargs)
        if key == supplement["path"] and counts[key] == 2:
            (tmp_path / "release" / "manifest-v4.json").write_bytes(
                b'{"replaced":"during-final-head"}',
            )
        return result

    client.head_object = replacing_head  # type: ignore[method-assign]

    summary = _publish(tmp_path, client)

    assert (tmp_path / "active-manifest.json").read_bytes() == original_bytes
    assert summary.manifest_sha256 == hashlib.sha256(original_bytes).hexdigest()


def test_activation失敗時はreceiptを残さない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _inherited, _supplement, _content = _fixture(tmp_path, monkeypatch)

    def fail_activation(_source: Path, _destination: Path) -> None:
        raise OSError("activation failed")

    monkeypatch.setattr(completion_publish, "_install_file", fail_activation)

    with pytest.raises(CompletionPublishError, match="activation/publish receipt"):
        _publish(tmp_path, client)

    assert not (tmp_path / "active-manifest.json").exists()
    assert not (tmp_path / "publish-receipt.json").exists()


def test_receipt失敗時は先に完了したactivationを保持する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _inherited, _supplement, _content = _fixture(tmp_path, monkeypatch)
    original_install = completion_publish._install_file

    def fail_receipt(source: Path, destination: Path) -> None:
        if destination.name == "publish-receipt.json":
            raise OSError("receipt failed")
        original_install(source, destination)

    monkeypatch.setattr(completion_publish, "_install_file", fail_receipt)

    with pytest.raises(CompletionPublishError, match="activation/publish receipt"):
        _publish(tmp_path, client)

    assert (tmp_path / "active-manifest.json").is_file()
    assert not (tmp_path / "publish-receipt.json").exists()
