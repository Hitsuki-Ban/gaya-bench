from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gaya_pipeline.adapters.base import ModelProfile


class ManifestError(RuntimeError):
    pass


TOP_LEVEL_KEYS = {"format_version", "generated_at", "models", "clips"}
MODEL_KEYS = {"id", "name", "version", "license_note", "capabilities"}
CAPABILITY_KEYS = {
    "emotion",
    "voice_prompt",
    "clone",
    "nonverbal",
    "reading",
}
CLIP_KEYS = {
    "model",
    "scenario",
    "line",
    "variant",
    "path",
    "duration_sec",
    "sha256",
    "gen_params",
    "rtf",
}


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"format_version": 1, "models": [], "clips": []}
    if not path.is_file():
        raise ManifestError(f"manifest がファイルではありません: {path}")

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"manifest を読み込めません: {path}") from error

    if not isinstance(manifest, dict):
        raise ManifestError("manifest のトップレベルは object が必要です。")
    if set(manifest) != TOP_LEVEL_KEYS:
        raise ManifestError("manifest のトップレベル項目が v1 と一致しません。")
    if manifest["format_version"] != 1:
        raise ManifestError("未対応の manifest format_version です。")
    if not isinstance(manifest["generated_at"], str):
        raise ManifestError("manifest generated_at は文字列が必要です。")
    if not isinstance(manifest["models"], list):
        raise ManifestError("manifest models は配列が必要です。")
    if not isinstance(manifest["clips"], list):
        raise ManifestError("manifest clips は配列が必要です。")

    for model in manifest["models"]:
        _validate_model(model)
    for clip in manifest["clips"]:
        _validate_clip(clip)
    model_ids = [model["id"] for model in manifest["models"]]
    if len(model_ids) != len(set(model_ids)):
        raise ManifestError("manifest model id が重複しています。")
    clip_keys = [_clip_key(clip) for clip in manifest["clips"]]
    if len(clip_keys) != len(set(clip_keys)):
        raise ManifestError("manifest clip key が重複しています。")
    return manifest


def update_manifest(
    path: Path,
    manifest: dict[str, Any],
    profile: ModelProfile,
    clips: list[dict[str, Any]],
    *,
    replace_model_clips: bool,
    replace_scenario_clips: str | None,
) -> bool:
    _validate_model(profile.as_manifest_entry())
    models_by_id = {str(model["id"]): model for model in manifest["models"]}
    previous_profile = models_by_id.get(profile.id)
    version_changed = (
        previous_profile is not None and previous_profile["version"] != profile.version
    )
    models_by_id[profile.id] = profile.as_manifest_entry()

    clips_by_key = {
        _clip_key(clip): clip
        for clip in manifest["clips"]
        if not (
            clip["model"] == profile.id
            and (
                version_changed
                or replace_model_clips
                or clip["scenario"] == replace_scenario_clips
            )
        )
    }
    for clip in clips:
        _validate_clip(clip)
        clips_by_key[_clip_key(clip)] = clip

    content = {
        "format_version": 1,
        "models": sorted(
            models_by_id.values(),
            key=lambda model: str(model["id"]),
        ),
        "clips": sorted(clips_by_key.values(), key=_clip_key),
    }
    current_content = {
        "format_version": manifest["format_version"],
        "models": manifest["models"],
        "clips": manifest["clips"],
    }
    if _canonical_json(content) == _canonical_json(current_content):
        return False

    output = {
        "format_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models": content["models"],
        "clips": content["clips"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def _validate_model(model: Any) -> None:
    if not isinstance(model, dict) or set(model) != MODEL_KEYS:
        raise ManifestError("manifest model の項目が v1 と一致しません。")
    if not isinstance(model["capabilities"], dict):
        raise ManifestError("model capabilities は object が必要です。")
    if set(model["capabilities"]) != CAPABILITY_KEYS:
        raise ManifestError("model capabilities の項目が v1 と一致しません。")
    if not all(isinstance(model["capabilities"][key], bool) for key in CAPABILITY_KEYS):
        raise ManifestError("model capabilities は bool が必要です。")
    for key in ("id", "name", "version", "license_note"):
        if not isinstance(model[key], str):
            raise ManifestError(f"model {key} は文字列が必要です。")


def _validate_clip(clip: Any) -> None:
    if not isinstance(clip, dict) or set(clip) != CLIP_KEYS:
        raise ManifestError("manifest clip の項目が v1 と一致しません。")
    for key in ("model", "scenario", "line", "variant", "path", "sha256"):
        if not isinstance(clip[key], str):
            raise ManifestError(f"clip {key} は文字列が必要です。")
    if not isinstance(clip["gen_params"], dict):
        raise ManifestError("clip gen_params は object が必要です。")
    for key in ("duration_sec", "rtf"):
        value = clip[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ManifestError(f"clip {key} は数値が必要です。")


def _clip_key(clip: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(clip["model"]),
        str(clip["scenario"]),
        str(clip["line"]),
        str(clip["variant"]),
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
