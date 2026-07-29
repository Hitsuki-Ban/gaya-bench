from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path

import pytest

from gaya_pipeline.take_identity import make_take_id
from gaya_pipeline.take_ledger import TakeLedgerError
from gaya_pipeline.take_manifest_v4 import (
    TakeManifestError,
    candidate_from_attempt,
    validate_manifest_v4,
)


FIXTURE = Path(__file__).parent / "fixtures" / "manifest-v4-valid.json"


def _manifest() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _candidate(manifest: dict[str, object]) -> dict[str, object]:
    return manifest["candidates"][0]  # type: ignore[index]


def test_manifest_v4のexact_fixture() -> None:
    manifest = _manifest()
    assert validate_manifest_v4(manifest) is manifest


@pytest.mark.parametrize("value", [None, "", "A" * 64, "0" * 63])
def test_candidate_set_sha256は完全な小文字SHA256が必須(value: object) -> None:
    manifest = _manifest()
    manifest["candidate_set_sha256"] = value
    with pytest.raises(TakeManifestError, match="candidate_set_sha256"):
        validate_manifest_v4(manifest)


@pytest.mark.parametrize(
    "field",
    ["take_index", "take_id", "generation_input_sha256"],
)
def test_candidateのtake_provenance欠落を拒否(field: str) -> None:
    manifest = _manifest()
    _candidate(manifest).pop(field)
    with pytest.raises(TakeManifestError):
        validate_manifest_v4(manifest)


@pytest.mark.parametrize(
    "field",
    ["seed", "recipe_version", "sampling", "requested", "realized"],
)
def test_candidate_gen_paramsの必須field欠落を拒否(field: str) -> None:
    manifest = _manifest()
    _candidate(manifest)["gen_params"].pop(field)  # type: ignore[union-attr]
    with pytest.raises(TakeManifestError, match="gen_params"):
        validate_manifest_v4(manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seed", True),
        ("recipe_version", ""),
        ("sampling", []),
        ("requested", None),
        ("requested", {"temperature": math.nan}),
        ("realized", "invalid"),
    ],
)
def test_candidate_gen_paramsの基本型境界を検証(
    field: str,
    value: object,
) -> None:
    manifest = _manifest()
    _candidate(manifest)["gen_params"][field] = value  # type: ignore[index]
    with pytest.raises(TakeManifestError, match="gen_params"):
        validate_manifest_v4(manifest)


def test_duplicate_take_idとduplicate_slotを拒否() -> None:
    manifest = _manifest()
    manifest["curations"] = []
    manifest["candidates"].append(deepcopy(_candidate(manifest)))  # type: ignore[union-attr]
    with pytest.raises(TakeManifestError, match="take_index|take_id"):
        validate_manifest_v4(manifest)


def test_orphanまたは別groupのselected_takeを拒否() -> None:
    for mutation in ("orphan", "other-group"):
        manifest = _manifest()
        curation = manifest["curations"][0]  # type: ignore[index]
        if mutation == "orphan":
            curation["take_id"] = "d" * 64
        else:
            curation["line"] = "other-line"
        with pytest.raises(TakeManifestError, match="selected"):
            validate_manifest_v4(manifest)


def test_skippedにtake_idを許可しない() -> None:
    manifest = _manifest()
    manifest["curations"][0]["decision"] = "skipped"  # type: ignore[index]
    with pytest.raises(TakeManifestError):
        validate_manifest_v4(manifest)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda candidate: candidate["gate"].update(mechanical="fail"),
        lambda candidate: candidate["gate"].update(content="reject"),
        lambda candidate: candidate.update(take_index=True),
        lambda candidate: candidate.update(duration_sec=math.nan),
        lambda candidate: candidate.update(path="../outside.opus"),
        lambda candidate: candidate.update(model="unknown"),
        lambda candidate: candidate.update(extra=True),
    ],
)
def test_gate_type_path_model_unknown_keyを拒否(mutation: object) -> None:
    manifest = _manifest()
    mutation(_candidate(manifest))  # type: ignore[operator]
    with pytest.raises(TakeManifestError):
        validate_manifest_v4(manifest)


def test_candidateとlogical_failureのgroup競合を拒否() -> None:
    manifest = _manifest()
    manifest["failures"][0].update(  # type: ignore[index]
        scenario="tavern-night",
        line="barmaid-001",
    )
    with pytest.raises(TakeManifestError, match="競合"):
        validate_manifest_v4(manifest)


def test_take_idはinputとaudio_shaの組に一致する必要がある() -> None:
    manifest = _manifest()
    _candidate(manifest)["sha256"] = "e" * 64
    _candidate(manifest)["path"] = (
        "audio/takes/dummy/tavern-night/barmaid-001/dry/"
        f"take-0001-{'e' * 64}.opus"
    )
    with pytest.raises(TakeManifestError, match="take_id"):
        validate_manifest_v4(manifest)


def _attempt(status: str) -> dict[str, object]:
    input_sha = "a" * 64
    audio_sha = "b" * 64
    return {
        "model": "dummy",
        "scenario": "tavern-night",
        "line": "barmaid-001",
        "variant": "dry",
        "take_index": 1,
        "take_id": make_take_id(
            generation_input_sha256=input_sha,
            final_opus_sha256=audio_sha,
        ),
        "generation_input_sha256": input_sha,
        "generation": {
            "status": "succeeded",
            "seed": None,
            "sampling": {},
            "rtf": 0.5,
        },
        "audio": {
            "wav_path": "audio/dummy/tavern-night/barmaid-001/dry/take-0001.wav",
            "wav_sha256": "f" * 64,
            "opus_path": "audio/dummy/tavern-night/barmaid-001/dry/take-0001.opus",
            "opus_sha256": audio_sha,
            "sidecar_sha256": "0" * 64,
        },
        "gates": {
            "mechanical": "pass",
            "content": "review_required",
        },
        "features": {"status": "unscored"},
        "status": status,
    }


def test_eligibleだけcandidateへ構築できる() -> None:
    candidate = candidate_from_attempt(
        _attempt("eligible"),
        duration_sec=1.25,
        loudness={
            "source": "encoded_opus",
            "i_lufs": -18.0,
            "tp_dbtp": -1.0,
            "shortfall": False,
        },
        gate_policy_version="take-gate-v1",
        recipe_version="fixed-single-v1",
        requested_params={"temperature": 1.0},
        realized_params={"temperature": 1.0},
    )
    assert candidate["take_index"] == 1
    assert candidate["gen_params"]["requested"] == {"temperature": 1.0}
    assert candidate["gen_params"]["realized"] == {"temperature": 1.0}

    for status in ("generated", "blocked", "hard_rejected", "generation_failed"):
        with pytest.raises(TakeLedgerError):
            candidate_from_attempt(
                _attempt(status),
                duration_sec=1.25,
                loudness={
                    "source": "encoded_opus",
                    "i_lufs": -18.0,
                    "tp_dbtp": -1.0,
                    "shortfall": False,
                },
                gate_policy_version="take-gate-v1",
                recipe_version="fixed-single-v1",
                requested_params={"temperature": 1.0},
                realized_params={"temperature": 1.0},
            )


def test_v3はv4入口で拒否する() -> None:
    manifest = _manifest()
    manifest["format_version"] = 3
    with pytest.raises(TakeManifestError, match="format_version"):
        validate_manifest_v4(manifest)
