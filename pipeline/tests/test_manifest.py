from __future__ import annotations

import json
from pathlib import Path

from gaya_pipeline.adapters.base import Capabilities, ModelProfile
from gaya_pipeline.manifest import update_manifest


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


def _clip(line_id: str, sha256: str) -> dict[str, object]:
    return {
        "model": "model-a",
        "scenario": "tavern-night",
        "line": line_id,
        "variant": "dry",
        "path": f"audio/model-a/tavern-night/{line_id}-dry.opus",
        "duration_sec": 1.0,
        "sha256": sha256,
        "gen_params": {},
        "rtf": 0.5,
    }


def test_model_version_change_removes_old_version_clips(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    old_first = _clip("barmaid-001", "a" * 64)
    old_second = _clip("barmaid-002", "b" * 64)
    manifest = {
        "format_version": 1,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "models": [_profile("1").as_manifest_entry()],
        "clips": [old_first, old_second],
    }
    new_first = _clip("barmaid-001", "c" * 64)

    updated = update_manifest(
        manifest_path,
        manifest,
        _profile("2"),
        [new_first],
        replace_model_clips=False,
        replace_scenario_clips=None,
    )
    output = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert updated is True
    assert output["models"][0]["version"] == "2"
    assert output["clips"] == [new_first]
