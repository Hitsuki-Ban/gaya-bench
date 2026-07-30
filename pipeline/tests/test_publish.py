from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from gaya_pipeline import cli
from gaya_pipeline.curation import (
    build_candidate_set,
    canonical_candidate_set_bytes,
    canonical_curation_bytes,
)
from gaya_pipeline.publish import (
    CACHE_CONTROL,
    CONTENT_TYPE,
    R2_BUCKET,
    PublishError,
    PublishSummary,
    create_r2_client,
    run_publish,
)
from gaya_pipeline.selection import human_selection_group
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


def _candidate(
    line: str,
    content: bytes,
    *,
    take_index: int = 1,
) -> dict[str, Any]:
    sha256 = hashlib.sha256(content).hexdigest()
    input_sha = hashlib.sha256(f"input:{line}".encode()).hexdigest()
    return {
        "model": "model-a",
        "scenario": "scene-a",
        "line": line,
        "variant": "dry",
        "take_index": take_index,
        "take_id": make_take_id(
            generation_input_sha256=input_sha,
            final_opus_sha256=sha256,
        ),
        "path": (
            f"audio/takes/model-a/scene-a/{line}/dry/"
            f"take-{take_index:04d}-{sha256}.opus"
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


def _manifest(
    candidates: list[dict[str, Any]],
    *,
    candidate_set_sha256: str,
    curation_sha256: str,
) -> dict[str, Any]:
    return {
        "format_version": 4,
        "generated_at": "2026-07-30T00:00:00Z",
        "candidate_set_sha256": candidate_set_sha256,
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
                "curation_sha256": curation_sha256,
            }
            for candidate in _first_candidates_by_group(candidates)
        ],
        "failures": [],
    }


def _write_canonical(path: Path, document: Any) -> bytes:
    payload = canonical_json(document).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _first_candidates_by_group(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    first: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for candidate in candidates:
        identity = tuple(
            candidate[key] for key in ("model", "scenario", "line", "variant")
        )
        first.setdefault(identity, candidate)
    return [first[identity] for identity in sorted(first)]


def _write_release(
    root: Path,
    *,
    contents: tuple[bytes, ...] = (b"opus-a", b"opus-b"),
    same_group: bool = False,
) -> tuple[Path, Path, list[dict[str, Any]]]:
    release_dir = root / "release"
    takes_root = root / "takes"
    run_id = "run-model-a"
    candidates = (
        [
            _candidate("line-1", content, take_index=index)
            for index, content in enumerate(contents, start=1)
        ]
        if same_group
        else [
            _candidate(f"line-{index}", content)
            for index, content in enumerate(contents, start=1)
        ]
    )
    models = [
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
    ]
    candidate_set = build_candidate_set(
        scenario_sha256="f" * 64,
        lines=[
            {
                "scenario": candidate["scenario"],
                "line": candidate["line"],
                "scenario_title": "Scene A",
                "text": candidate["line"],
                "delivery": "test",
            }
            for candidate in _first_candidates_by_group(candidates)
        ],
        models=models,
        candidates=candidates,
        failures=[],
    )
    candidate_set_raw = canonical_candidate_set_bytes(candidate_set)
    candidate_set_sha256 = hashlib.sha256(candidate_set_raw).hexdigest()
    curation = {
        "format_version": 1,
        "rubric_version": "take-curation-v1",
        "candidate_set_sha256": candidate_set_sha256,
        "groups": [
            {
                "model": candidate["model"],
                "scenario": candidate["scenario"],
                "line": candidate["line"],
                "variant": candidate["variant"],
                "candidates": [
                    {
                        "take_id": member["take_id"],
                        "path": member["path"],
                        "audio_sha256": member["sha256"],
                        "rubric": {
                            "content_correct": True,
                            "intent_match": 5,
                            "character_naturalness": 5,
                            "adoptable": True,
                        },
                    }
                    for member in candidates
                    if all(
                        member[key] == candidate[key]
                        for key in ("model", "scenario", "line", "variant")
                    )
                ],
                "decision": {
                    "type": "selected",
                    "take_id": candidate["take_id"],
                },
            }
            for candidate in _first_candidates_by_group(candidates)
        ],
    }
    curation_raw = canonical_curation_bytes(curation)
    curation_sha256 = hashlib.sha256(curation_raw).hexdigest()
    manifest = _manifest(
        candidates,
        candidate_set_sha256=candidate_set_sha256,
        curation_sha256=curation_sha256,
    )
    _write_canonical(release_dir / "candidate-set.json", candidate_set)
    (release_dir / "candidate-set.sha256").write_text(
        candidate_set_sha256,
        encoding="ascii",
    )
    (release_dir / "curation").mkdir(parents=True, exist_ok=True)
    (release_dir / "curation" / f"{curation_sha256}.json").write_bytes(
        curation_raw,
    )
    manifest_raw = _write_canonical(release_dir / "manifest-v4.json", manifest)
    (release_dir / "manifest-v4.sha256").write_text(
        hashlib.sha256(manifest_raw).hexdigest(),
        encoding="ascii",
    )
    run_root = takes_root / run_id
    source_path = run_root / "manifest-v4.json"
    source_raw = _write_canonical(source_path, manifest)
    (run_root / "candidate-set.json").write_bytes(candidate_set_raw)
    (run_root / "candidate-set.sha256").write_text(
        candidate_set_sha256,
        encoding="ascii",
    )
    ledger_raw = _write_canonical(run_root / "ledger.json", {"fixture": "ledger"})
    qc_report_raw = _write_canonical(
        run_root / "qc-report.json",
        {"fixture": "qc-report"},
    )
    provenance = {
        "format_version": 1,
        "candidate_set_sha256": candidate_set_sha256,
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "runs": [
            {
                "model": "model-a",
                "run_id": run_id,
                "ledger_sha256": hashlib.sha256(ledger_raw).hexdigest(),
                "qc_report_sha256": hashlib.sha256(qc_report_raw).hexdigest(),
                "manifest_sha256": hashlib.sha256(source_raw).hexdigest(),
                "candidate_set_sha256": candidate_set_sha256,
            },
        ],
    }
    provenance_raw = _write_canonical(
        release_dir / "release-provenance.json",
        provenance,
    )
    (release_dir / "release-provenance.sha256").write_text(
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
            / f"take-{candidate['take_index']:04d}.opus"
        )
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)
    return release_dir, takes_root, candidates


def _drop_second_release_candidate(release_dir: Path) -> None:
    candidate_set_path = release_dir / "candidate-set.json"
    candidate_set = json.loads(candidate_set_path.read_bytes())
    candidate_set["candidates"] = candidate_set["candidates"][:1]
    kept_take_id = candidate_set["candidates"][0]["take_id"]
    candidate_set_raw = canonical_candidate_set_bytes(candidate_set)
    candidate_set_sha256 = hashlib.sha256(candidate_set_raw).hexdigest()
    candidate_set_path.write_bytes(candidate_set_raw)
    (release_dir / "candidate-set.sha256").write_text(
        candidate_set_sha256,
        encoding="ascii",
    )

    old_curation_path = next((release_dir / "curation").glob("*.json"))
    curation = json.loads(old_curation_path.read_bytes())
    curation["candidate_set_sha256"] = candidate_set_sha256
    curation["groups"][0]["candidates"] = [
        candidate
        for candidate in curation["groups"][0]["candidates"]
        if candidate["take_id"] == kept_take_id
    ]
    curation_raw = canonical_curation_bytes(curation)
    curation_sha256 = hashlib.sha256(curation_raw).hexdigest()
    old_curation_path.unlink()
    (release_dir / "curation" / f"{curation_sha256}.json").write_bytes(
        curation_raw,
    )

    manifest_path = release_dir / "manifest-v4.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["candidate_set_sha256"] = candidate_set_sha256
    manifest["candidates"] = manifest["candidates"][:1]
    manifest["curations"][0]["curation_sha256"] = curation_sha256
    manifest_raw = _write_canonical(manifest_path, manifest)
    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    (release_dir / "manifest-v4.sha256").write_text(
        manifest_sha256,
        encoding="ascii",
    )

    provenance_path = release_dir / "release-provenance.json"
    provenance = json.loads(provenance_path.read_bytes())
    provenance["candidate_set_sha256"] = candidate_set_sha256
    provenance["manifest_sha256"] = manifest_sha256
    provenance_raw = _write_canonical(provenance_path, provenance)
    (release_dir / "release-provenance.sha256").write_text(
        hashlib.sha256(provenance_raw).hexdigest(),
        encoding="ascii",
    )


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


def test_releaseはsource_group内の全candidateをexact投影する(
    tmp_path: Path,
) -> None:
    release, takes, _candidates = _write_release(tmp_path, same_group=True)
    _drop_second_release_candidate(release)
    client = FakeS3Client()

    with pytest.raises(PublishError, match="release 投影が exact"):
        run_publish(release_dir=release, takes_root=takes, client=client)

    assert client.head_calls == []
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


def test_selection_v2改変はnetwork前に拒否(tmp_path: Path) -> None:
    release, takes, _candidates = _write_release(tmp_path)
    old_curation_path = next((release / "curation").glob("*.json"))
    old_curation = json.loads(old_curation_path.read_bytes())
    selection = {
        "format_version": 2,
        "protocol": "take-selection-v1",
        "candidate_set_sha256": old_curation["candidate_set_sha256"],
        "groups": [
            human_selection_group(group) for group in old_curation["groups"]
        ],
    }
    selection["groups"][0]["authority"]["rubric_version"] = "tampered-rubric"
    selection_raw = canonical_json(selection).encode("utf-8")
    selection_sha256 = hashlib.sha256(selection_raw).hexdigest()
    old_curation_path.unlink()
    (release / "curation" / f"{selection_sha256}.json").write_bytes(
        selection_raw,
    )

    manifest_path = release / "manifest-v4.json"
    manifest = json.loads(manifest_path.read_bytes())
    for curation in manifest["curations"]:
        curation["curation_sha256"] = selection_sha256
    manifest_raw = _write_canonical(manifest_path, manifest)
    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    (release / "manifest-v4.sha256").write_text(
        manifest_sha256,
        encoding="ascii",
    )

    provenance_path = release / "release-provenance.json"
    provenance = json.loads(provenance_path.read_bytes())
    provenance["manifest_sha256"] = manifest_sha256
    provenance_raw = _write_canonical(provenance_path, provenance)
    (release / "release-provenance.sha256").write_text(
        hashlib.sha256(provenance_raw).hexdigest(),
        encoding="ascii",
    )
    client = FakeS3Client()

    with pytest.raises(PublishError, match="rubric_version"):
        run_publish(release_dir=release, takes_root=takes, client=client)

    assert client.head_calls == []
    assert client.put_calls == []


@pytest.mark.parametrize("bad_value", [1, None])
def test_provenance非文字列は変換せずnetwork前に拒否(
    tmp_path: Path,
    bad_value: object,
) -> None:
    release, takes, _candidates = _write_release(tmp_path)
    path = release / "release-provenance.json"
    document = json.loads(path.read_bytes())
    document["runs"][0]["run_id"] = bad_value
    raw = _write_canonical(path, document)
    (release / "release-provenance.sha256").write_text(
        hashlib.sha256(raw).hexdigest(),
        encoding="ascii",
    )
    client = FakeS3Client()

    with pytest.raises(PublishError, match="path segment"):
        run_publish(release_dir=release, takes_root=takes, client=client)

    assert client.head_calls == []
    assert client.put_calls == []


def test_provenance_run_idのcontainment違反はnetwork前に拒否(
    tmp_path: Path,
) -> None:
    release, takes, _candidates = _write_release(tmp_path)
    path = release / "release-provenance.json"
    document = json.loads(path.read_bytes())
    document["runs"][0]["run_id"] = "../outside"
    raw = _write_canonical(path, document)
    (release / "release-provenance.sha256").write_text(
        hashlib.sha256(raw).hexdigest(),
        encoding="ascii",
    )
    client = FakeS3Client()

    with pytest.raises(PublishError, match="path segment"):
        run_publish(release_dir=release, takes_root=takes, client=client)

    assert client.head_calls == []
    assert client.put_calls == []


def _rewrite_provenance_manifest_sha(release: Path, source_raw: bytes) -> None:
    path = release / "release-provenance.json"
    document = json.loads(path.read_bytes())
    document["runs"][0]["manifest_sha256"] = hashlib.sha256(source_raw).hexdigest()
    raw = _write_canonical(path, document)
    (release / "release-provenance.sha256").write_text(
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
