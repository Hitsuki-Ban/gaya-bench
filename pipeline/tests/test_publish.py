from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from gaya_pipeline import cli
from gaya_pipeline.publish import (
    CACHE_CONTROL,
    CONTENT_TYPE,
    R2_BUCKET,
    PublishError,
    PublishSummary,
    create_r2_client,
    run_publish,
)
from gaya_pipeline.take_identity import canonical_json, make_take_id


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.head_calls: list[str] = []
        self.head_requests: list[dict[str, Any]] = []
        self.put_calls: list[dict[str, Any]] = []
        self.put_failures: dict[str, Exception] = {}
        self.concurrent_objects: dict[str, dict[str, Any]] = {}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        self.head_calls.append(key)
        self.head_requests.append(dict(kwargs))
        if key not in self.objects:
            raise _client_error("404", 404, "HeadObject")
        return dict(self.objects[key])

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        call = dict(kwargs)
        self.put_calls.append(call)
        if key in self.concurrent_objects:
            self.objects[key] = self.concurrent_objects.pop(key)
            raise _client_error("PreconditionFailed", 412, "PutObject")
        failure = self.put_failures.pop(key, None)
        if failure is not None:
            raise failure
        body = kwargs["Body"]
        content = body if isinstance(body, bytes) else body.read()
        self.objects[key] = _remote_object(content)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


def _client_error(code: str, status: int, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "test error"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


def _remote_object(
    content: bytes,
    *,
    sha256: str | None = None,
    content_type: str = CONTENT_TYPE,
    cache_control: str = CACHE_CONTROL,
) -> dict[str, Any]:
    digest = hashlib.sha256(content)
    return {
        "ContentLength": len(content),
        "ContentType": content_type,
        "CacheControl": cache_control,
        "Metadata": {"sha256": sha256 or digest.hexdigest()},
        "ChecksumSHA256": base64.b64encode(digest.digest()).decode("ascii"),
    }


def _candidate(line: str, content: bytes) -> dict[str, Any]:
    sha256 = hashlib.sha256(content).hexdigest()
    input_sha = hashlib.sha256(f"input:{line}".encode()).hexdigest()
    return {
        "model": "model-a",
        "scenario": "scene-a",
        "line": line,
        "variant": "dry",
        "take_index": 1,
        "take_id": make_take_id(
            generation_input_sha256=input_sha,
            final_opus_sha256=sha256,
        ),
        "path": (
            f"audio/takes/model-a/scene-a/{line}/dry/"
            f"take-0001-{sha256}.opus"
        ),
        "duration_sec": 1.0,
        "sha256": sha256,
        "generation_input_sha256": input_sha,
        "gen_params": {
            "seed": 1,
            "recipe_version": "test-v1",
            "sampling": {},
            "requested": {},
            "realized": {},
        },
        "rtf": 0.5,
        "loudness": {
            "source": "encoded_opus",
            "i_lufs": -18.0,
            "tp_dbtp": -1.0,
            "shortfall": False,
        },
        "gate": {
            "mechanical": "pass",
            "content": "review_required",
            "policy_version": "take-gates-v2",
        },
    }


def _manifest(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format_version": 4,
        "generated_at": "2026-07-30T00:00:00Z",
        "candidate_set_sha256": "a" * 64,
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
            },
        ],
        "candidates": candidates,
        "curations": [
            {
                "model": candidate["model"],
                "scenario": candidate["scenario"],
                "line": candidate["line"],
                "variant": candidate["variant"],
                "decision": "selected",
                "take_id": candidate["take_id"],
                "curation_sha256": "b" * 64,
            }
            for candidate in candidates
        ],
        "failures": [],
    }


def _write_canonical(path: Path, document: Any) -> bytes:
    payload = canonical_json(document).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _write_release(
    root: Path,
    *,
    contents: tuple[bytes, ...] = (b"opus-a", b"opus-b"),
) -> tuple[Path, Path, list[dict[str, Any]]]:
    release_dir = root / "release"
    takes_root = root / "takes"
    run_id = "run-model-a"
    candidates = [
        _candidate(f"line-{index}", content)
        for index, content in enumerate(contents, start=1)
    ]
    manifest = _manifest(candidates)
    manifest_raw = _write_canonical(release_dir / "manifest-v4.json", manifest)
    (release_dir / "manifest-v4.sha256").write_text(
        hashlib.sha256(manifest_raw).hexdigest(),
        encoding="ascii",
    )
    source_path = takes_root / run_id / "manifest-v4.json"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    source_raw = source_path.read_bytes()
    provenance = {
        "format_version": 1,
        "plan_sha256": "c" * 64,
        "runs": [
            {
                "model": "model-a",
                "run_id": run_id,
                "ledger_path": "C:/fixed/ledger.json",
                "ledger_sha256": "d" * 64,
                "qc_report_sha256": "e" * 64,
                "manifest_sha256": hashlib.sha256(source_raw).hexdigest(),
                "candidate_set_sha256": manifest["candidate_set_sha256"],
            },
        ],
    }
    provenance_raw = _write_canonical(
        release_dir / "baseline-provenance.json",
        provenance,
    )
    (release_dir / "baseline-provenance.sha256").write_text(
        hashlib.sha256(provenance_raw).hexdigest(),
        encoding="ascii",
    )
    for candidate, content in zip(candidates, contents, strict=True):
        local_path = (
            takes_root
            / run_id
            / "audio"
            / candidate["model"]
            / candidate["scenario"]
            / candidate["line"]
            / candidate["variant"]
            / "take-0001.opus"
        )
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)
    return release_dir, takes_root, candidates


def test_first_publishはconditional_putしsecondは全skip(
    tmp_path: Path,
) -> None:
    release, takes, candidates = _write_release(tmp_path)
    client = FakeS3Client()

    first = run_publish(release_dir=release, takes_root=takes, client=client)
    put_count = len(client.put_calls)
    second = run_publish(release_dir=release, takes_root=takes, client=client)

    assert first.uploaded_count == 2
    assert first.skipped_count == 0
    assert second.uploaded_count == 0
    assert second.skipped_count == 2
    assert len(client.put_calls) == put_count == 2
    assert all(
        request["ChecksumMode"] == "ENABLED"
        for request in client.head_requests
    )
    first_put = client.put_calls[0]
    first_candidate = sorted(candidates, key=lambda item: item["path"])[0]
    content = next(
        value
        for value in (b"opus-a", b"opus-b")
        if hashlib.sha256(value).hexdigest() == first_candidate["sha256"]
    )
    assert first_put == {
        "Bucket": R2_BUCKET,
        "Key": first_candidate["path"],
        "Body": content,
        "ContentLength": len(content),
        "ContentType": CONTENT_TYPE,
        "CacheControl": CACHE_CONTROL,
        "Metadata": {"sha256": first_candidate["sha256"]},
        "IfNoneMatch": "*",
        "ChecksumSHA256": base64.b64encode(
            hashlib.sha256(content).digest(),
        ).decode("ascii"),
    }


def test_remote_conflictは全HEAD後もzero_put(tmp_path: Path) -> None:
    release, takes, candidates = _write_release(tmp_path)
    client = FakeS3Client()
    client.objects[candidates[0]["path"]] = _remote_object(
        b"different",
        sha256="0" * 64,
    )

    with pytest.raises(PublishError, match="immutable R2 object"):
        run_publish(release_dir=release, takes_root=takes, client=client)

    assert sorted(client.head_calls) == sorted(
        candidate["path"] for candidate in candidates
    )
    assert client.put_calls == []


def test_remote_checksum欠落はmetadata一致でもzero_put(
    tmp_path: Path,
) -> None:
    release, takes, candidates = _write_release(tmp_path)
    remote = _remote_object(b"opus-a")
    remote.pop("ChecksumSHA256")
    client = FakeS3Client()
    client.objects[candidates[0]["path"]] = remote

    with pytest.raises(PublishError, match="immutable R2 object"):
        run_publish(release_dir=release, takes_root=takes, client=client)

    assert sorted(client.head_calls) == sorted(
        candidate["path"] for candidate in candidates
    )
    assert client.put_calls == []


@pytest.mark.parametrize(
    "mutation",
    [
        "manifest-marker",
        "manifest-noncanonical",
        "manifest-v3",
        "source-candidate",
        "missing-opus",
        "bad-opus-sha",
    ],
)
def test_local_release不整合はnetwork前に拒否(
    tmp_path: Path,
    mutation: str,
) -> None:
    release, takes, candidates = _write_release(tmp_path)
    manifest_path = release / "manifest-v4.json"
    if mutation == "manifest-marker":
        (release / "manifest-v4.sha256").write_text("0" * 64, encoding="ascii")
    elif mutation == "manifest-noncanonical":
        document = json.loads(manifest_path.read_bytes())
        manifest_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        (release / "manifest-v4.sha256").write_text(
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            encoding="ascii",
        )
    elif mutation == "manifest-v3":
        document = json.loads(manifest_path.read_bytes())
        document["format_version"] = 3
        raw = _write_canonical(manifest_path, document)
        (release / "manifest-v4.sha256").write_text(
            hashlib.sha256(raw).hexdigest(),
            encoding="ascii",
        )
    elif mutation == "source-candidate":
        source_path = takes / "run-model-a" / "manifest-v4.json"
        document = json.loads(source_path.read_bytes())
        document["candidates"] = document["candidates"][1:]
        document["curations"] = document["curations"][1:]
        raw = _write_canonical(source_path, document)
        _rewrite_provenance_manifest_sha(release, raw)
    elif mutation == "missing-opus":
        next(takes.rglob("*.opus")).unlink()
    elif mutation == "bad-opus-sha":
        next(takes.rglob("*.opus")).write_bytes(b"changed")
    client = FakeS3Client()

    with pytest.raises(PublishError):
        run_publish(release_dir=release, takes_root=takes, client=client)

    assert client.head_calls == []
    assert client.put_calls == []


@pytest.mark.parametrize("bad_value", [1, None])
def test_provenance非文字列は変換せずnetwork前に拒否(
    tmp_path: Path,
    bad_value: object,
) -> None:
    release, takes, _candidates = _write_release(tmp_path)
    path = release / "baseline-provenance.json"
    document = json.loads(path.read_bytes())
    document["runs"][0]["run_id"] = bad_value
    raw = _write_canonical(path, document)
    (release / "baseline-provenance.sha256").write_text(
        hashlib.sha256(raw).hexdigest(),
        encoding="ascii",
    )
    client = FakeS3Client()

    with pytest.raises(PublishError, match="全項目は文字列"):
        run_publish(release_dir=release, takes_root=takes, client=client)

    assert client.head_calls == []
    assert client.put_calls == []


def test_provenance_run_idのcontainment違反はnetwork前に拒否(
    tmp_path: Path,
) -> None:
    release, takes, _candidates = _write_release(tmp_path)
    path = release / "baseline-provenance.json"
    document = json.loads(path.read_bytes())
    document["runs"][0]["run_id"] = "../outside"
    raw = _write_canonical(path, document)
    (release / "baseline-provenance.sha256").write_text(
        hashlib.sha256(raw).hexdigest(),
        encoding="ascii",
    )
    client = FakeS3Client()

    with pytest.raises(PublishError, match="安全な path segment"):
        run_publish(release_dir=release, takes_root=takes, client=client)

    assert client.head_calls == []
    assert client.put_calls == []


def _rewrite_provenance_manifest_sha(release: Path, source_raw: bytes) -> None:
    path = release / "baseline-provenance.json"
    document = json.loads(path.read_bytes())
    document["runs"][0]["manifest_sha256"] = hashlib.sha256(source_raw).hexdigest()
    raw = _write_canonical(path, document)
    (release / "baseline-provenance.sha256").write_text(
        hashlib.sha256(raw).hexdigest(),
        encoding="ascii",
    )


def test_partial_upload後の再実行は既存same_shaをskip(
    tmp_path: Path,
) -> None:
    release, takes, candidates = _write_release(tmp_path)
    ordered = sorted(candidates, key=lambda item: item["path"])
    client = FakeS3Client()
    client.put_failures[ordered[1]["path"]] = EndpointConnectionError(
        endpoint_url="https://example.invalid",
    )

    with pytest.raises(PublishError, match="conditional upload"):
        run_publish(release_dir=release, takes_root=takes, client=client)
    assert ordered[0]["path"] in client.objects

    summary = run_publish(release_dir=release, takes_root=takes, client=client)
    assert summary.uploaded_count == 1
    assert summary.skipped_count == 1


def test_412後HEADがsame_shaならconcurrent_successとしてskip(
    tmp_path: Path,
) -> None:
    release, takes, candidates = _write_release(tmp_path, contents=(b"opus-a",))
    candidate = candidates[0]
    client = FakeS3Client()
    client.concurrent_objects[candidate["path"]] = _remote_object(b"opus-a")

    summary = run_publish(release_dir=release, takes_root=takes, client=client)

    assert summary.uploaded_count == 0
    assert summary.skipped_count == 1
    assert client.put_calls[0]["IfNoneMatch"] == "*"


def test_412後HEADが異なるobjectならconflict(
    tmp_path: Path,
) -> None:
    release, takes, candidates = _write_release(tmp_path, contents=(b"opus-a",))
    candidate = candidates[0]
    client = FakeS3Client()
    client.concurrent_objects[candidate["path"]] = _remote_object(
        b"different",
        sha256="0" * 64,
    )

    with pytest.raises(PublishError, match="concurrent upload と競合"):
        run_publish(release_dir=release, takes_root=takes, client=client)


def test_final_HEAD_sweepは不整合後も全objectを確認(
    tmp_path: Path,
) -> None:
    release, takes, candidates = _write_release(tmp_path)
    bad_key = sorted(candidate["path"] for candidate in candidates)[0]

    class FinalSweepClient(FakeS3Client):
        def __init__(self) -> None:
            super().__init__()
            self.counts: dict[str, int] = {}

        def head_object(self, **kwargs: Any) -> dict[str, Any]:
            key = kwargs["Key"]
            self.counts[key] = self.counts.get(key, 0) + 1
            remote = super().head_object(**kwargs)
            if key == bad_key and self.counts[key] == 2:
                return _remote_object(b"conflict", sha256="0" * 64)
            return remote

    client = FinalSweepClient()
    with pytest.raises(PublishError, match="final HEAD sweep"):
        run_publish(release_dir=release, takes_root=takes, client=client)

    assert client.counts == {
        candidate["path"]: 2 for candidate in candidates
    }


def test_env_fileは必須値を同一fileから読む(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "r2.env"
    env_file.write_text(
        "\n".join(
            (
                f"CLOUDFLARE_ACCOUNT_ID={'a' * 32}",
                "R2_ACCESS_KEY_ID=access",
                "R2_SECRET_ACCESS_KEY=secret",
            ),
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}
    fake = FakeS3Client()

    def fake_client(**kwargs: Any) -> FakeS3Client:
        captured.update(kwargs)
        return fake

    monkeypatch.setattr("gaya_pipeline.publish.boto3.client", fake_client)
    assert create_r2_client(env_file) is fake
    assert captured["aws_access_key_id"] == "access"
    assert captured["aws_secret_access_key"] == "secret"
    assert captured["endpoint_url"] == f"https://{'a' * 32}.r2.cloudflarestorage.com"

    with pytest.raises(PublishError, match="env file が存在しません"):
        create_r2_client(tmp_path / "missing.env")


def test_env_fileはambient環境変数を展開しない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "r2.env"
    env_file.write_text(
        "\n".join(
            (
                "CLOUDFLARE_ACCOUNT_ID=${AMBIENT_ACCOUNT_ID}",
                "R2_ACCESS_KEY_ID=${AMBIENT_ACCESS_KEY_ID}",
                "R2_SECRET_ACCESS_KEY=${AMBIENT_SECRET_ACCESS_KEY}",
            ),
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AMBIENT_ACCOUNT_ID", "a" * 32)
    monkeypatch.setenv("AMBIENT_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("AMBIENT_SECRET_ACCESS_KEY", "secret")

    with pytest.raises(PublishError, match="CLOUDFLARE_ACCOUNT_ID の形式"):
        create_r2_client(env_file)


@pytest.mark.parametrize(
    "argv",
    [
        ["publish"],
        ["publish", "--release", "release"],
        ["publish", "--release", "release", "--takes-root", "takes"],
    ],
)
def test_publish_cliは三つのpath引数を必須にする(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(argv)
    assert error.value.code == 2


def test_publish_cliはexplicit_pathだけを渡す(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "release"
    takes = tmp_path / "takes"
    env_file = tmp_path / ".env"
    client = FakeS3Client()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(cli, "create_r2_client", lambda path: client)

    def fake_publish(**kwargs: Any) -> PublishSummary:
        captured.update(kwargs)
        return PublishSummary(records=())

    monkeypatch.setattr(cli, "run_publish", fake_publish)
    result = cli.main(
        [
            "publish",
            "--release",
            str(release),
            "--takes-root",
            str(takes),
            "--env-file",
            str(env_file),
        ],
    )

    assert result == 0
    assert captured == {
        "release_dir": release,
        "takes_root": takes,
        "client": client,
    }


def test_baseline_commandとgen_selectionは存在しない() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["baseline", "plan"])
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "gen",
                "--model",
                "dummy",
                "--selection",
                "plan.json",
                "--takes",
                "1",
                "--seed-base",
                "1",
            ],
        )
