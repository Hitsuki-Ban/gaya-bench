from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError
from gaya_pipeline import cli
from gaya_pipeline.publish import (
    CACHE_CONTROL,
    CONTENT_TYPE,
    R2_BUCKET,
    PublishError,
    PublishRecord,
    PublishSummary,
    create_r2_client,
    run_publish,
)


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.head_calls: list[str] = []
        self.put_calls: list[dict[str, Any]] = []
        self.head_error: ClientError | None = None
        self.put_error: ClientError | None = None

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        self.head_calls.append(key)
        if self.head_error is not None:
            raise self.head_error
        if key not in self.objects:
            raise _client_error("404", 404, "HeadObject")
        return dict(self.objects[key])

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        if self.put_error is not None:
            raise self.put_error
        body = kwargs.pop("Body")
        content = body if isinstance(body, bytes) else body.read()
        call = {**kwargs, "Body": content}
        self.put_calls.append(call)
        self.objects[kwargs["Key"]] = {
            "ContentLength": len(content),
            "ContentType": kwargs["ContentType"],
            "CacheControl": kwargs["CacheControl"],
            "Metadata": dict(kwargs["Metadata"]),
        }
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


def _client_error(code: str, status: int, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "test error"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


def _write_fixture(
    root: Path,
    *,
    content: bytes = b"opus-data",
    path: str = "audio/model-a/scene-a/line-a-dry.opus",
    sha256: str | None = None,
) -> tuple[Path, Path]:
    artifacts_dir = root / "artifacts"
    opus_path = artifacts_dir.joinpath(*path.split("/"))
    opus_path.parent.mkdir(parents=True, exist_ok=True)
    opus_path.write_bytes(content)
    manifest_path = root / "data" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest = {
        "format_version": 2,
        "generated_at": "2026-07-28T00:00:00+00:00",
        "models": [
            {
                "id": "model-a",
                "name": "Model A",
                "version": "1",
                "license_note": "test",
                "capabilities": {
                    "emotion": False,
                    "voice_prompt": False,
                    "clone": False,
                    "nonverbal": False,
                    "reading": False,
                },
            }
        ],
        "clips": [
            {
                "model": "model-a",
                "scenario": "scene-a",
                "line": "line-a",
                "variant": "dry",
                "path": path,
                "duration_sec": 1.0,
                "sha256": sha256 or hashlib.sha256(content).hexdigest(),
                "gen_params": {},
                "rtf": 0.5,
            }
        ],
        "failures": [],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path, artifacts_dir


def test_first_publish_uploads_and_second_publish_skips(tmp_path: Path) -> None:
    manifest_path, artifacts_dir = _write_fixture(tmp_path)
    client = FakeS3Client()

    first = run_publish(
        manifest_path=manifest_path,
        artifacts_dir=artifacts_dir,
        client=client,
    )
    client.objects["audio/model-a/scene-a/line-a-dry.opus"]["CacheControl"] = (
        "public, must-revalidate, max-age=0"
    )
    second = run_publish(
        manifest_path=manifest_path,
        artifacts_dir=artifacts_dir,
        client=client,
    )

    assert first.uploaded_count == 1
    assert first.skipped_count == 0
    assert second.uploaded_count == 0
    assert second.skipped_count == 1
    assert len(client.put_calls) == 1
    upload = client.put_calls[0]
    assert upload == {
        "Bucket": R2_BUCKET,
        "Key": "audio/model-a/scene-a/line-a-dry.opus",
        "Body": b"opus-data",
        "ContentLength": len(b"opus-data"),
        "ContentType": CONTENT_TYPE,
        "CacheControl": CACHE_CONTROL,
        "Metadata": {"sha256": hashlib.sha256(b"opus-data").hexdigest()},
    }


@pytest.mark.parametrize(
    ("path", "sha256", "message"),
    [
        ("audio/model-a/scene-a/wrong-dry.opus", None, "規定形式"),
        (
            "audio/model-a/scene-a/line-a-dry.opus",
            "invalid",
            "sha256 が不正",
        ),
        (
            "audio/model-a/scene-a/line-a-dry.opus",
            "0" * 64,
            "manifest と一致",
        ),
    ],
)
def test_invalid_local_artifact_fails_before_r2(
    tmp_path: Path,
    path: str,
    sha256: str | None,
    message: str,
) -> None:
    manifest_path, artifacts_dir = _write_fixture(
        tmp_path,
        path=path,
        sha256=sha256,
    )
    client = FakeS3Client()

    with pytest.raises(PublishError, match=message):
        run_publish(
            manifest_path=manifest_path,
            artifacts_dir=artifacts_dir,
            client=client,
        )

    assert client.head_calls == []
    assert client.put_calls == []


def test_missing_manifest_and_missing_opus_fail_before_r2(tmp_path: Path) -> None:
    client = FakeS3Client()
    with pytest.raises(PublishError, match="manifest が存在しません"):
        run_publish(
            manifest_path=tmp_path / "missing.json",
            artifacts_dir=tmp_path / "artifacts",
            client=client,
        )

    manifest_path, artifacts_dir = _write_fixture(tmp_path)
    next(artifacts_dir.rglob("*.opus")).unlink()
    with pytest.raises(PublishError, match="Opus が存在しません"):
        run_publish(
            manifest_path=manifest_path,
            artifacts_dir=artifacts_dir,
            client=client,
        )
    assert client.head_calls == []


def test_remote_metadata_mismatch_reuploads(tmp_path: Path) -> None:
    manifest_path, artifacts_dir = _write_fixture(tmp_path)
    client = FakeS3Client()
    key = "audio/model-a/scene-a/line-a-dry.opus"
    client.objects[key] = {
        "ContentLength": len(b"opus-data"),
        "ContentType": CONTENT_TYPE,
        "CacheControl": "public, max-age=31536000, immutable",
        "Metadata": {"sha256": hashlib.sha256(b"opus-data").hexdigest()},
    }

    summary = run_publish(
        manifest_path=manifest_path,
        artifacts_dir=artifacts_dir,
        client=client,
    )

    assert summary.uploaded_count == 1
    assert client.put_calls[0]["CacheControl"] == CACHE_CONTROL


def test_validated_content_is_frozen_before_remote_calls(tmp_path: Path) -> None:
    manifest_path, artifacts_dir = _write_fixture(tmp_path)
    opus_path = next(artifacts_dir.rglob("*.opus"))

    class MutatingClient(FakeS3Client):
        def head_object(self, **kwargs: Any) -> dict[str, Any]:
            opus_path.write_bytes(b"other-data")
            return super().head_object(**kwargs)

    client = MutatingClient()
    summary = run_publish(
        manifest_path=manifest_path,
        artifacts_dir=artifacts_dir,
        client=client,
    )

    assert summary.uploaded_count == 1
    assert client.put_calls[0]["Body"] == b"opus-data"
    assert client.put_calls[0]["Metadata"] == {
        "sha256": hashlib.sha256(b"opus-data").hexdigest()
    }


@pytest.mark.parametrize(
    ("model_id", "path"),
    [
        ("model%2fa", "audio/model%2fa/scene-a/line-a-dry.opus"),
        ("", "audio//scene-a/line-a-dry.opus"),
        (".", "audio/./scene-a/line-a-dry.opus"),
        ("model#a", "audio/model#a/scene-a/line-a-dry.opus"),
        ("model?a", "audio/model?a/scene-a/line-a-dry.opus"),
    ],
)
def test_unsafe_path_is_rejected_before_r2(
    tmp_path: Path,
    model_id: str,
    path: str,
) -> None:
    manifest_path, artifacts_dir = _write_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["models"][0]["id"] = model_id
    manifest["clips"][0]["model"] = model_id
    manifest["clips"][0]["path"] = path
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    client = FakeS3Client()

    with pytest.raises(PublishError, match="path が不正"):
        run_publish(
            manifest_path=manifest_path,
            artifacts_dir=artifacts_dir,
            client=client,
        )
    assert client.head_calls == []


def test_r2_errors_are_reported_without_response_details(tmp_path: Path) -> None:
    manifest_path, artifacts_dir = _write_fixture(tmp_path)
    client = FakeS3Client()
    client.head_error = _client_error("AccessDenied", 403, "HeadObject")

    with pytest.raises(PublishError, match="R2 HEAD に失敗") as caught:
        run_publish(
            manifest_path=manifest_path,
            artifacts_dir=artifacts_dir,
            client=client,
        )

    assert "test error" not in str(caught.value)


def test_invalid_account_id_is_rejected_before_client_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "attacker.example/path")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    created = False

    def fail_client(**_: Any) -> None:
        nonlocal created
        created = True

    monkeypatch.setattr("gaya_pipeline.publish.boto3.client", fail_client)

    with pytest.raises(PublishError, match="ACCOUNT_ID の形式が不正"):
        create_r2_client(tmp_path)
    assert created is False


def test_cli_prints_publish_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "create_r2_client", lambda _: FakeS3Client())
    monkeypatch.setattr(
        cli,
        "run_publish",
        lambda **_: PublishSummary(
            records=(
                PublishRecord(
                    key="audio/model/scene/line-dry.opus",
                    status="uploaded",
                    size_bytes=42,
                ),
            )
        ),
    )

    exit_code = cli.main(["publish"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "アップロード: audio/model/scene/line-dry.opus (42 bytes)" in output
    assert "完了: アップロード 1 / スキップ 0" in output
