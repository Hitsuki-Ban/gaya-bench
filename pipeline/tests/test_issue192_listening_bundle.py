import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


_TOOL_PATH = Path(__file__).parents[1] / "tools" / "issue192_listening_bundle.py"
_SPEC = importlib.util.spec_from_file_location("issue192_listening_bundle", _TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

BundleError = _MODULE.BundleError
MODEL = _MODULE.MODEL
SOURCE_RUN_ID = _MODULE.SOURCE_RUN_ID
CANDIDATE_RUN_ID = _MODULE.CANDIDATE_RUN_ID
build = _MODULE.build
canonical_bytes = _MODULE.canonical_bytes

_RESULT_TOOL_PATH = Path(__file__).parents[1] / "tools" / "issue192_listening_results.py"
_RESULT_SPEC = importlib.util.spec_from_file_location(
    "issue192_listening_results",
    _RESULT_TOOL_PATH,
)
assert _RESULT_SPEC is not None and _RESULT_SPEC.loader is not None
_RESULT_MODULE = importlib.util.module_from_spec(_RESULT_SPEC)
sys.modules[_RESULT_SPEC.name] = _RESULT_MODULE
_RESULT_SPEC.loader.exec_module(_RESULT_MODULE)
ResultError = _RESULT_MODULE.ResultError
summarize_result = _RESULT_MODULE.summarize


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def _write_marker_bound_json(path: Path, value: object) -> None:
    _write_json(path, value)
    path.with_suffix(".sha256").write_text(_sha(path.read_bytes()), encoding="ascii")


def _attempt(
    run_root: Path,
    *,
    scenario: str,
    line: str,
    take_index: int,
    audio: bytes,
) -> dict[str, object]:
    take_id = _sha(f"{run_root.name}:{scenario}:{line}:{take_index}".encode())
    relative = f"audio/{take_id}.wav"
    path = run_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio)
    return {
        "model": MODEL,
        "scenario": scenario,
        "line": line,
        "variant": "dry",
        "take_index": take_index,
        "take_id": take_id,
        "audio": {"wav_path": relative, "wav_sha256": _sha(audio)},
    }


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    artifacts = tmp_path / "artifacts"
    source_root = artifacts / "takes" / SOURCE_RUN_ID
    candidate_root = artifacts / "takes" / CANDIDATE_RUN_ID
    current_yatai = _attempt(
        source_root,
        scenario="festival-night",
        line="yatai-obasan-003",
        take_index=3,
        audio=b"current-yatai",
    )
    current_fruit = _attempt(
        source_root,
        scenario="market-day",
        line="fruit-vendor-002",
        take_index=3,
        audio=b"current-fruit",
    )
    new_yatai = _attempt(
        candidate_root,
        scenario="festival-night",
        line="yatai-obasan-003",
        take_index=3,
        audio=b"new-yatai",
    )
    new_fruit_1 = _attempt(
        candidate_root,
        scenario="market-day",
        line="fruit-vendor-002",
        take_index=1,
        audio=b"new-fruit-1",
    )
    new_fruit_4 = _attempt(
        candidate_root,
        scenario="market-day",
        line="fruit-vendor-002",
        take_index=4,
        audio=b"new-fruit-4",
    )
    source_attempts = [current_yatai, current_fruit]
    candidate_attempts = [new_yatai, new_fruit_1, new_fruit_4]
    _write_json(
        source_root / "ledger.json",
        {"run_id": SOURCE_RUN_ID, "attempts": source_attempts},
    )
    _write_json(
        candidate_root / "ledger.json",
        {"run_id": CANDIDATE_RUN_ID, "attempts": candidate_attempts},
    )
    for root, run_id, attempts in (
        (source_root, SOURCE_RUN_ID, source_attempts),
        (candidate_root, CANDIDATE_RUN_ID, candidate_attempts),
    ):
        _write_json(
            root / "qc-report.json",
            {
                "run_id": run_id,
                "attempts": [
                    {
                        "model": item["model"],
                        "scenario": item["scenario"],
                        "line": item["line"],
                        "variant": item["variant"],
                        "take_id": item["take_id"],
                        "status": "eligible",
                        "gates": {
                            "mechanical": "pass",
                            "content": "review_required",
                        },
                    }
                    for item in attempts
                ],
            },
        )
        _write_marker_bound_json(root / "candidate-set.json", {"run_id": run_id})

    manifest = {
        "candidates": [
            {
                "model": MODEL,
                "scenario": item["scenario"],
                "line": item["line"],
                "variant": "dry",
                "take_id": item["take_id"],
            }
            for item in source_attempts
        ],
        "curations": [
            {
                "model": MODEL,
                "scenario": item["scenario"],
                "line": item["line"],
                "variant": "dry",
                "decision": "selected",
                "take_id": item["take_id"],
            }
            for item in source_attempts
        ],
    }
    manifest_path = tmp_path / "manifest-v4.json"
    _write_json(manifest_path, manifest)
    return manifest_path, artifacts, {
        "current_yatai": str(current_yatai["take_id"]),
        "current_fruit": str(current_fruit["take_id"]),
        "new_yatai": str(new_yatai["take_id"]),
        "new_fruit_1": str(new_fruit_1["take_id"]),
        "new_fruit_4": str(new_fruit_4["take_id"]),
    }


def test_build_creates_exact_two_group_blind_bundle(tmp_path: Path) -> None:
    manifest, artifacts, _takes = _fixture(tmp_path)
    output = tmp_path / "bundle"

    build(manifest, artifacts, output)

    bundle_path = output / "baseline-quality-ab-bundle-v1.json"
    raw = bundle_path.read_bytes()
    bundle = json.loads(raw)
    assert raw == canonical_bytes(bundle)
    assert (output / "baseline-quality-ab-bundle-v1.sha256").read_text() == _sha(raw)
    assert [
        (group["scenario"], group["line"], len(group["candidates"]))
        for group in bundle["groups"]
    ] == [
        ("festival-night", "yatai-obasan-003", 2),
        ("market-day", "fruit-vendor-002", 3),
    ]
    assert {item["variant"] for item in bundle["groups"][0]["candidates"]} == {
        "current-selected",
        "new-take-0003",
    }
    assert {item["variant"] for item in bundle["groups"][1]["candidates"]} == {
        "current-selected",
        "new-take-0001",
        "new-take-0004",
    }
    audio = sorted((output / "audio").iterdir())
    assert len(audio) == 5
    for group in bundle["groups"]:
        for candidate in group["candidates"]:
            path = output / candidate["audio_path"]
            assert path.is_file()
            assert _sha(path.read_bytes()) == candidate["audio_sha256"]


def test_build_is_deterministic_and_rejects_nonempty_output(tmp_path: Path) -> None:
    manifest, artifacts, _takes = _fixture(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"

    build(manifest, artifacts, first)
    build(manifest, artifacts, second)

    assert (first / "baseline-quality-ab-bundle-v1.json").read_bytes() == (
        second / "baseline-quality-ab-bundle-v1.json"
    ).read_bytes()
    with pytest.raises(BundleError, match="output must be absent or empty"):
        build(manifest, artifacts, first)


def test_build_rejects_tampered_audio_before_copy(tmp_path: Path) -> None:
    manifest, artifacts, takes = _fixture(tmp_path)
    source_root = artifacts / "takes" / SOURCE_RUN_ID
    ledger = json.loads((source_root / "ledger.json").read_text(encoding="utf-8"))
    attempt = next(item for item in ledger["attempts"] if item["take_id"] == takes["current_yatai"])
    (source_root / attempt["audio"]["wav_path"]).write_bytes(b"tampered")

    with pytest.raises(BundleError, match="WAV hash mismatch"):
        build(manifest, artifacts, tmp_path / "bundle")


def test_build_rejects_candidate_set_marker_drift(tmp_path: Path) -> None:
    manifest, artifacts, _takes = _fixture(tmp_path)
    marker = (
        artifacts
        / "takes"
        / CANDIDATE_RUN_ID
        / "candidate-set.sha256"
    )
    marker.write_text("0" * 64, encoding="ascii")

    with pytest.raises(BundleError, match="SHA marker mismatch"):
        build(manifest, artifacts, tmp_path / "bundle")


def test_build_rejects_noncanonical_marker_bound_candidate_set(tmp_path: Path) -> None:
    manifest, artifacts, _takes = _fixture(tmp_path)
    candidate_set = (
        artifacts
        / "takes"
        / CANDIDATE_RUN_ID
        / "candidate-set.json"
    )
    candidate_set.write_text(
        json.dumps({"run_id": CANDIDATE_RUN_ID}, indent=2),
        encoding="utf-8",
    )
    candidate_set.with_suffix(".sha256").write_text(
        _sha(candidate_set.read_bytes()),
        encoding="ascii",
    )

    with pytest.raises(BundleError, match="marker-bound JSON must use canonical bytes"):
        build(manifest, artifacts, tmp_path / "bundle")


def _completed_result(bundle_dir: Path, choices: dict[tuple[str, str], str]) -> Path:
    bundle = json.loads(
        (bundle_dir / "baseline-quality-ab-bundle-v1.json").read_text(encoding="utf-8")
    )
    groups = []
    for group in bundle["groups"]:
        candidates = {item["variant"]: item["id"] for item in group["candidates"]}
        choice = choices[(group["scenario"], group["line"])]
        groups.append(
            {
                "id": group["id"],
                "heard_candidate_ids": [item["id"] for item in group["candidates"]],
                "choice": candidates.get(choice, choice),
                "notes": "",
            }
        )
    result = {
        "format_version": 1,
        "protocol": "baseline-quality-ab-result-v1",
        "study_id": bundle["study_id"],
        "groups": groups,
    }
    path = bundle_dir.parent / "result.json"
    path.write_bytes(canonical_bytes(result))
    return path


def test_result_binds_blind_choices_to_source_take_receipts(tmp_path: Path) -> None:
    manifest, artifacts, takes = _fixture(tmp_path)
    bundle = tmp_path / "bundle"
    build(manifest, artifacts, bundle)
    result = _completed_result(
        bundle,
        {
            ("festival-night", "yatai-obasan-003"): "new-take-0003",
            ("market-day", "fruit-vendor-002"): "current-selected",
        },
    )

    report = summarize_result(
        bundle_dir=bundle,
        result_path=result,
        manifest_path=manifest,
        artifacts=artifacts,
    )

    assert report["protocol"] == "issue-192-targeted-listening-result-v1"
    assert [group["decision"] for group in report["groups"]] == [
        "new-take-0003",
        "current-selected",
    ]
    assert report["groups"][0]["selected"]["take_id"] == takes["new_yatai"]
    assert report["groups"][1]["selected"]["take_id"] == takes["current_fruit"]
    assert len(report["groups"][0]["heard_candidates"]) == 2
    assert len(report["groups"][1]["heard_candidates"]) == 3
    assert report["sources"]["authority"]["candidate_run"]["run_id"] == (
        CANDIDATE_RUN_ID
    )


def test_result_rejects_incomplete_playback(tmp_path: Path) -> None:
    manifest, artifacts, _takes = _fixture(tmp_path)
    bundle = tmp_path / "bundle"
    build(manifest, artifacts, bundle)
    result_path = _completed_result(
        bundle,
        {
            ("festival-night", "yatai-obasan-003"): "new-take-0003",
            ("market-day", "fruit-vendor-002"): "new-take-0004",
        },
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["groups"][0]["heard_candidate_ids"].pop()
    result_path.write_bytes(canonical_bytes(result))

    with pytest.raises(ResultError, match="complete playback"):
        summarize_result(
            bundle_dir=bundle,
            result_path=result_path,
            manifest_path=manifest,
            artifacts=artifacts,
        )
