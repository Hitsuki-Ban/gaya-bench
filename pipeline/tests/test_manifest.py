from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from gaya_pipeline.adapters.base import Capabilities, ModelProfile
from gaya_pipeline.manifest import ManifestError, load_manifest, update_manifest


def _profile(version: str) -> ModelProfile:
    return ModelProfile(
        id="model-a",
        name="Model A",
        version=version,
        license_note="MIT",
        capabilities=Capabilities(
            emotion=False,
            voice_prompt=False,
            clone=False,
            nonverbal=False,
            reading=False,
        ),
    )


def _clip(
    line_id: str,
    sha256: str,
    *,
    scenario_id: str = "tavern-night",
) -> dict[str, object]:
    return {
        "model": "model-a",
        "scenario": scenario_id,
        "line": line_id,
        "variant": "dry",
        "path": f"audio/model-a/{scenario_id}/{line_id}-dry.opus",
        "duration_sec": 1.0,
        "sha256": sha256,
        "gen_params": {},
        "rtf": 0.5,
        "loudness": {
            "i_lufs": -18.0,
            "tp_dbtp": -1.0,
            "shortfall": False,
        },
    }


def _failure(
    line_id: str,
    *,
    scenario_id: str = "tavern-night",
) -> dict[str, str]:
    return {
        "model": "model-a",
        "scenario": scenario_id,
        "line": line_id,
        "variant": "dry",
        "reason": "generation_failed",
    }


def _manifest(
    *,
    version: str = "1",
    clips: list[dict[str, object]] | None = None,
    failures: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "format_version": 2,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "models": [_profile(version).as_manifest_entry()],
        "clips": [] if clips is None else clips,
        "failures": [] if failures is None else failures,
    }


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_load_manifest_rejects_v1_without_migration(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    v1 = _manifest()
    v1["format_version"] = 1
    del v1["failures"]
    _write_manifest(manifest_path, v1)

    with pytest.raises(ManifestError, match="format_version"):
        load_manifest(manifest_path)


@pytest.mark.parametrize(
    ("failures", "message"),
    [
        (
            [
                {
                    "model": "model-a",
                    "scenario": "tavern-night",
                    "line": "barmaid-001",
                    "variant": "dry",
                    "reason": "CUDA out of memory",
                },
            ],
            "reason",
        ),
        (
            [_failure("barmaid-001"), _failure("barmaid-001")],
            "failure key",
        ),
        (
            [
                {
                    **_failure("barmaid-001"),
                    "detail": "CUDA out of memory",
                },
            ],
            "項目",
        ),
    ],
)
def test_load_manifest_rejects_invalid_failures(
    tmp_path: Path,
    failures: list[dict[str, str]],
    message: str,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, _manifest(failures=failures))

    with pytest.raises(ManifestError, match=message):
        load_manifest(manifest_path)


@pytest.mark.parametrize(
    "loudness",
    [
        {"i_lufs": -18.0, "tp_dbtp": -1.0},
        {"i_lufs": -18.0, "tp_dbtp": -1.0, "shortfall": "false"},
    ],
)
def test_load_manifest_rejects_invalid_clip_loudness(
    tmp_path: Path,
    loudness: dict[str, object],
) -> None:
    manifest_path = tmp_path / "manifest.json"
    clip = _clip("barmaid-001", "a" * 64)
    clip["loudness"] = loudness
    _write_manifest(manifest_path, _manifest(clips=[clip]))

    with pytest.raises(ManifestError, match="loudness"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_clip_failure_key_conflict(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(
        manifest_path,
        _manifest(
            clips=[_clip("barmaid-001", "a" * 64)],
            failures=[_failure("barmaid-001")],
        ),
    )

    with pytest.raises(ManifestError, match="clips と failures"):
        load_manifest(manifest_path)


@pytest.mark.parametrize(
    ("clips", "failures"),
    [
        ([_clip("barmaid-001", "a" * 64)], []),
        ([], [_failure("barmaid-001")]),
    ],
)
def test_load_manifest_rejects_results_for_unknown_model(
    tmp_path: Path,
    clips: list[dict[str, object]],
    failures: list[dict[str, str]],
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest = _manifest(clips=clips, failures=failures)
    manifest["models"] = []
    _write_manifest(manifest_path, manifest)

    with pytest.raises(ManifestError, match="存在しない model"):
        load_manifest(manifest_path)


def test_failure_demotes_success_and_skipped_success_repairs_failure(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    old_clip = _clip("barmaid-001", "a" * 64)
    manifest = _manifest(clips=[old_clip])

    update_manifest(
        manifest_path,
        manifest,
        _profile("1"),
        [],
        [_failure("barmaid-001")],
        replace_model_results=False,
        replace_scenario_results=None,
    )
    failed = load_manifest(manifest_path)
    assert failed["clips"] == []
    assert failed["failures"] == [_failure("barmaid-001")]

    new_clip = _clip("barmaid-001", "b" * 64)
    update_manifest(
        manifest_path,
        failed,
        _profile("1"),
        [new_clip],
        [],
        replace_model_results=False,
        replace_scenario_results=None,
    )
    recovered = load_manifest(manifest_path)
    assert recovered["clips"] == [new_clip]
    assert recovered["failures"] == []


def test_scope_cleanup_applies_to_clips_and_failures(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    keep_clip = _clip("fruit-vendor-001", "a" * 64, scenario_id="market-day")
    stale_clip = _clip("barmaid-001", "b" * 64)
    stale_failure = _failure("barmaid-002")
    manifest = _manifest(clips=[keep_clip, stale_clip], failures=[stale_failure])

    update_manifest(
        manifest_path,
        manifest,
        _profile("1"),
        [_clip("drunkard-001", "c" * 64)],
        [],
        replace_model_results=False,
        replace_scenario_results="tavern-night",
    )
    output = load_manifest(manifest_path)

    assert output["clips"] == [
        keep_clip,
        _clip("drunkard-001", "c" * 64),
    ]
    assert output["failures"] == []


def test_model_version_change_removes_old_version_results(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    old_clip = _clip("barmaid-001", "a" * 64)
    old_failure = _failure("barmaid-002")
    manifest = _manifest(clips=[old_clip], failures=[old_failure])
    new_failure = _failure("drunkard-001")

    updated = update_manifest(
        manifest_path,
        manifest,
        _profile("2"),
        [],
        [new_failure],
        replace_model_results=False,
        replace_scenario_results=None,
    )
    output = load_manifest(manifest_path)

    assert updated is True
    assert output["models"][0]["version"] == "2"
    assert output["clips"] == []
    assert output["failures"] == [new_failure]


def test_noop_preserves_generated_at_and_mtime(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    clip = _clip("barmaid-001", "a" * 64)
    manifest = _manifest(clips=[clip])
    _write_manifest(manifest_path, manifest)
    before_mtime = manifest_path.stat().st_mtime_ns
    before_bytes = manifest_path.read_bytes()

    updated = update_manifest(
        manifest_path,
        load_manifest(manifest_path),
        _profile("1"),
        [clip],
        [],
        replace_model_results=False,
        replace_scenario_results=None,
    )

    assert updated is False
    assert manifest_path.stat().st_mtime_ns == before_mtime
    assert manifest_path.read_bytes() == before_bytes


def test_atomic_replace_failure_preserves_bytes_and_removes_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest = _manifest(clips=[_clip("barmaid-001", "a" * 64)])
    _write_manifest(manifest_path, manifest)
    before_bytes = manifest_path.read_bytes()

    def fail_replace(
        source: str | os.PathLike[str], target: str | os.PathLike[str]
    ) -> None:
        raise OSError(f"replace failed: {source} -> {target}")

    monkeypatch.setattr("gaya_pipeline.manifest.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        update_manifest(
            manifest_path,
            manifest,
            _profile("1"),
            [],
            [_failure("barmaid-001")],
            replace_model_results=False,
            replace_scenario_results=None,
        )

    assert manifest_path.read_bytes() == before_bytes
    assert list(tmp_path.glob(f".{manifest_path.name}.*.tmp")) == []


def test_atomic_replace_failure_is_not_masked_by_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest = _manifest(clips=[_clip("barmaid-001", "a" * 64)])
    _write_manifest(manifest_path, manifest)

    def fail_replace(
        source: str | os.PathLike[str], target: str | os.PathLike[str]
    ) -> None:
        raise OSError(f"replace failed: {source} -> {target}")

    def fail_cleanup(self: Path, *, missing_ok: bool = False) -> None:
        raise OSError(f"cleanup failed: {self}")

    monkeypatch.setattr("gaya_pipeline.manifest.os.replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)

    with pytest.raises(OSError, match="replace failed") as raised:
        update_manifest(
            manifest_path,
            manifest,
            _profile("1"),
            [],
            [_failure("barmaid-001")],
            replace_model_results=False,
            replace_scenario_results=None,
        )

    assert any("cleanup failed" in note for note in raised.value.__notes__)
