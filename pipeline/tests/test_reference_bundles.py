from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from gaya_pipeline.cli import main
from gaya_pipeline.reference_bundles import (
    ReferenceBundleCatalogError,
    validate_reference_bundle_catalog,
)
from jsonschema import Draft202012Validator, FormatChecker

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPOSITORY_ROOT / "assets" / "reference-bundles" / "schema"
FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "reference-bundles"
    / "valid-catalog"
)
INSTANCES_DIR = (
    Path(__file__).parent / "fixtures" / "reference-bundles" / "instances"
)
VALIDATION_DATE = date(2026, 7, 31)


def _copy_catalog(tmp_path: Path) -> Path:
    catalog_dir = tmp_path / "reference-bundles"
    shutil.copytree(FIXTURE_DIR, catalog_dir)
    shutil.copytree(SCHEMA_DIR, catalog_dir / "schema")
    return catalog_dir


def _read_yaml(path: Path) -> dict[str, object]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _write_yaml(path: Path, document: dict[str, object]) -> None:
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _bundle_path(catalog_dir: Path) -> Path:
    return catalog_dir / "bundles" / "commissioned-elder.yaml"


def _validate_instance(
    schema_filename: str,
    fixture_filename: str,
) -> dict[str, object]:
    schema = json.loads(
        (SCHEMA_DIR / schema_filename).read_text(encoding="utf-8"),
    )
    Draft202012Validator.check_schema(schema)
    document = yaml.safe_load(
        (INSTANCES_DIR / fixture_filename).read_text(encoding="utf-8"),
    )
    errors = list(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(document),
    )
    assert errors == []
    assert isinstance(document, dict)
    return document


def _instance_schema_errors(
    schema_filename: str,
    document: dict[str, object],
) -> list[object]:
    schema = json.loads(
        (SCHEMA_DIR / schema_filename).read_text(encoding="utf-8"),
    )
    return list(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(document),
    )


def _set_synthetic_origin(document: dict[str, object]) -> None:
    document["origin"] = {
        "type": "synthetic",
        "synthetic_policy_id": "official-qwen-voice-design-to-base-v1",
        "generated_on": "2026-07-29",
        "generation_input_sha256": "d" * 64,
        "generation_receipt_sha256": "e" * 64,
        "selection_receipt_sha256": "f" * 64,
    }
    document["rights"]["evidence"] = {
        "type": "model_terms",
        "terms_url": "https://example.invalid/model-terms",
        "reviewed_on": "2026-07-29",
    }


def test_all_five_schemas_are_valid_draft_2020_12() -> None:
    schema_paths = sorted(SCHEMA_DIR.glob("*.schema.json"))

    assert len(schema_paths) == 5
    for schema_path in schema_paths:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["$schema"] == (
            "https://json-schema.org/draft/2020-12/schema"
        )
        Draft202012Validator.check_schema(schema)


def test_recording_request_instance_is_valid() -> None:
    document = _validate_instance(
        "recording-request-v1.schema.json",
        "recording-request-valid.yaml",
    )

    assert document["persona"]["kind"] == "human"
    assert document["delivery_pcm"] == {
        "encoding": "pcm_s16le",
        "sample_rate_hz": 48000,
        "channels": 1,
    }
    assert document["scripts"]["general"]["duration_ms"] == {
        "minimum": 10200,
        "maximum": 14000,
    }
    assert document["scripts"]["short_clone"]["duration_ms"] == {
        "minimum": 5000,
        "maximum": 5000,
    }


def test_derivative_receipt_instance_and_transcript_hashes_are_valid() -> None:
    document = _validate_instance(
        "derivative-receipt-v1.schema.json",
        "derivative-receipt-valid.yaml",
    )

    assert document["operations"] == []
    assert document["inference_reference_sha256"] == (
        document["output_asset"]["asset_sha256"]
    )
    for transcript in (
        document["source_transcript"],
        document["output_asset"]["transcript"],
    ):
        actual = hashlib.sha256(
            transcript["text"].encode("utf-8"),
        ).hexdigest()
        assert transcript["utf8_sha256"] == actual


def test_valid_catalog_metadata_is_accepted_without_private_audio(
    tmp_path: Path,
) -> None:
    catalog_dir = _copy_catalog(tmp_path)

    summary = validate_reference_bundle_catalog(
        catalog_dir,
        as_of=VALIDATION_DATE,
    )

    assert summary.bundle_count == 1
    assert summary.assignment_count == 1
    assert summary.synthetic_policy_count == 1


def test_catalog_validate_cli_accepts_explicit_absolute_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    catalog_dir = _copy_catalog(tmp_path)

    exit_code = main(
        [
            "reference-bundles",
            "catalog-validate",
            "--catalog",
            str(catalog_dir),
            "--as-of",
            VALIDATION_DATE.isoformat(),
        ],
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "検証成功" in captured.out
    assert captured.err == ""


def test_catalog_validate_cli_rejects_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    monkeypatch.chdir(catalog_dir.parent)

    exit_code = main(
        [
            "reference-bundles",
            "catalog-validate",
            "--catalog",
            catalog_dir.name,
            "--as-of",
            VALIDATION_DATE.isoformat(),
        ],
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "絶対パス" in captured.err


def test_bundle_extra_field_is_rejected(tmp_path: Path) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    path = _bundle_path(catalog_dir)
    document = _read_yaml(path)
    document["unexpected"] = True
    _write_yaml(path, document)

    with pytest.raises(ReferenceBundleCatalogError, match="schema 違反"):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


def test_bundle_filename_must_match_bundle_id(tmp_path: Path) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    original_path = _bundle_path(catalog_dir)
    duplicate = _read_yaml(original_path)
    _write_yaml(catalog_dir / "bundles" / "second.yaml", duplicate)
    original_path.unlink()

    with pytest.raises(ReferenceBundleCatalogError, match="ファイル名 stem"):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


def test_unknown_assignment_bundle_is_rejected(tmp_path: Path) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    path = catalog_dir / "assignments.yaml"
    document = _read_yaml(path)
    document["assignments"][0]["bundle_id"] = "missing-bundle"
    _write_yaml(path, document)

    with pytest.raises(
        ReferenceBundleCatalogError,
        match="未知の bundle 'missing-bundle'",
    ):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


@pytest.mark.parametrize(
    "permission",
    ["tts_reference_inference", "commercial_generated_output"],
    ids=["tts-reference", "commercial-output"],
)
def test_production_permission_prohibited_is_rejected(
    tmp_path: Path,
    permission: str,
) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    path = _bundle_path(catalog_dir)
    document = _read_yaml(path)
    document["rights"]["permissions"][permission] = "prohibited"
    _write_yaml(path, document)

    with pytest.raises(
        ReferenceBundleCatalogError,
        match=f"{permission} が permitted",
    ):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


def test_expired_production_term_is_rejected(tmp_path: Path) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    path = _bundle_path(catalog_dir)
    document = _read_yaml(path)
    document["rights"]["term"] = {
        "type": "fixed",
        "starts_on": "2025-01-01",
        "expires_on": "2025-12-31",
        "renewal_review_on": "2025-12-01",
    }
    _write_yaml(path, document)

    with pytest.raises(ReferenceBundleCatalogError, match="失効"):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


def test_unknown_synthetic_policy_is_rejected(tmp_path: Path) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    path = _bundle_path(catalog_dir)
    document = _read_yaml(path)
    document["origin"] = {
        "type": "synthetic",
        "synthetic_policy_id": "unknown-policy",
        "generated_on": "2026-07-29",
        "generation_input_sha256": "d" * 64,
        "generation_receipt_sha256": "e" * 64,
        "selection_receipt_sha256": "f" * 64,
    }
    document["rights"]["evidence"] = {
        "type": "model_terms",
        "terms_url": "https://example.invalid/model-terms",
        "reviewed_on": "2026-07-29",
    }
    _write_yaml(path, document)

    with pytest.raises(
        ReferenceBundleCatalogError,
        match="未知の synthetic policy 'unknown-policy'",
    ):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


def test_transcript_utf8_sha_mismatch_is_rejected(tmp_path: Path) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    path = _bundle_path(catalog_dir)
    document = _read_yaml(path)
    document["clips"]["general"]["transcript"]["text"] = "差し替えた台本"
    _write_yaml(path, document)

    with pytest.raises(
        ReferenceBundleCatalogError,
        match="UTF-8 SHA-256 と一致しません",
    ):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


def test_short_clone_requires_exact_five_seconds_at_48khz(
    tmp_path: Path,
) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    path = _bundle_path(catalog_dir)
    document = _read_yaml(path)
    document["clips"]["short_clone"]["pcm"]["frame_count"] = 239999
    _write_yaml(path, document)

    with pytest.raises(ReferenceBundleCatalogError, match="schema 違反"):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


@pytest.mark.parametrize("target", ["general", "short_clone"])
def test_bundle_requires_general_and_short_clone(
    tmp_path: Path,
    target: str,
) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    path = _bundle_path(catalog_dir)
    document = _read_yaml(path)
    del document["clips"][target]
    _write_yaml(path, document)

    with pytest.raises(ReferenceBundleCatalogError, match="schema 違反"):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


def test_general_clip_rejects_less_than_ten_seconds(
    tmp_path: Path,
) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    path = _bundle_path(catalog_dir)
    document = _read_yaml(path)
    document["clips"]["general"]["pcm"]["frame_count"] = 479999
    _write_yaml(path, document)

    with pytest.raises(ReferenceBundleCatalogError, match="schema 違反"):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


def test_general_clip_rejects_more_than_twenty_seconds(
    tmp_path: Path,
) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    path = _bundle_path(catalog_dir)
    document = _read_yaml(path)
    document["clips"]["general"]["pcm"]["frame_count"] = 960001
    _write_yaml(path, document)

    with pytest.raises(ReferenceBundleCatalogError, match="schema 違反"):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("encoding", "pcm_s24le"),
        ("sample_rate_hz", 44100),
        ("channels", 2),
    ],
)
def test_bundle_clip_rejects_noncanonical_pcm(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    path = _bundle_path(catalog_dir)
    document = _read_yaml(path)
    document["clips"]["general"]["pcm"][field] = value
    _write_yaml(path, document)

    with pytest.raises(ReferenceBundleCatalogError, match="schema 違反"):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("delivery_pcm", "encoding", "pcm_s24le"),
        ("general", "minimum", 9999),
        ("short_clone", "maximum", 5001),
    ],
)
def test_recording_request_rejects_noncanonical_pcm_or_duration(
    target: str,
    field: str,
    value: object,
) -> None:
    document = yaml.safe_load(
        (
            INSTANCES_DIR / "recording-request-valid.yaml"
        ).read_text(encoding="utf-8"),
    )
    if target == "delivery_pcm":
        document["delivery_pcm"][field] = value
    else:
        document["scripts"][target]["duration_ms"][field] = value

    assert _instance_schema_errors(
        "recording-request-v1.schema.json",
        document,
    )


def test_derivative_output_rejects_non_mono_pcm() -> None:
    document = yaml.safe_load(
        (
            INSTANCES_DIR / "derivative-receipt-valid.yaml"
        ).read_text(encoding="utf-8"),
    )
    document["output_asset"]["pcm"]["channels"] = 2

    assert _instance_schema_errors(
        "derivative-receipt-v1.schema.json",
        document,
    )


def test_public_audio_requires_redistribution_permission(
    tmp_path: Path,
) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    path = _bundle_path(catalog_dir)
    document = _read_yaml(path)
    document["publication"]["audio_access"] = "public"
    clips = document["clips"]
    clips["general"]["storage"]["type"] = "public_object"
    clips["short_clone"]["storage"]["type"] = "public_object"
    clips["emotions"][0]["storage"]["type"] = "public_object"
    _write_yaml(path, document)

    with pytest.raises(
        ReferenceBundleCatalogError,
        match="public audio には permitted",
    ):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


def test_catalog_transcript_requires_redistribution_permission(
    tmp_path: Path,
) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    path = _bundle_path(catalog_dir)
    document = _read_yaml(path)
    document["clips"]["general"]["transcript"]["rights"][
        "redistribution"
    ] = "prohibited"
    _write_yaml(path, document)

    with pytest.raises(
        ReferenceBundleCatalogError,
        match="transcript は permitted",
    ):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )


def test_revoked_synthetic_policy_is_allowed_for_evaluation_history(
    tmp_path: Path,
) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    bundle_path = _bundle_path(catalog_dir)
    bundle = _read_yaml(bundle_path)
    _set_synthetic_origin(bundle)
    _write_yaml(bundle_path, bundle)
    assignments_path = catalog_dir / "assignments.yaml"
    assignments = _read_yaml(assignments_path)
    assignments["assignments"][0]["usage"] = "evaluation"
    _write_yaml(assignments_path, assignments)
    policies_path = catalog_dir / "synthetic-sources.yaml"
    policies = _read_yaml(policies_path)
    policies["policies"][0]["status"] = "revoked"
    _write_yaml(policies_path, policies)

    summary = validate_reference_bundle_catalog(
        catalog_dir,
        as_of=VALIDATION_DATE,
    )

    assert summary.bundle_count == 1


def test_revoked_synthetic_policy_is_rejected_for_production(
    tmp_path: Path,
) -> None:
    catalog_dir = _copy_catalog(tmp_path)
    bundle_path = _bundle_path(catalog_dir)
    bundle = _read_yaml(bundle_path)
    _set_synthetic_origin(bundle)
    _write_yaml(bundle_path, bundle)
    policies_path = catalog_dir / "synthetic-sources.yaml"
    policies = _read_yaml(policies_path)
    policies["policies"][0]["status"] = "revoked"
    _write_yaml(policies_path, policies)

    with pytest.raises(
        ReferenceBundleCatalogError,
        match="approved である必要",
    ):
        validate_reference_bundle_catalog(
            catalog_dir,
            as_of=VALIDATION_DATE,
        )
