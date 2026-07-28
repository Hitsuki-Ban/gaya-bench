from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gaya_pipeline.adapters.base import ModelProfile


class ManifestError(RuntimeError):
    pass


FORMAT_VERSION = 2
TOP_LEVEL_KEYS = {
    "format_version",
    "generated_at",
    "models",
    "clips",
    "failures",
}
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
    "loudness",
}
FAILURE_KEYS = {"model", "scenario", "line", "variant", "reason"}
FAILURE_REASONS = {"generation_failed"}


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "format_version": FORMAT_VERSION,
            "generated_at": "",
            "models": [],
            "clips": [],
            "failures": [],
        }
    if not path.is_file():
        raise ManifestError(f"manifest がファイルではありません: {path}")

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"manifest を読み込めません: {path}") from error

    _validate_manifest(manifest)
    return manifest


def update_manifest(
    path: Path,
    manifest: dict[str, Any],
    profile: ModelProfile,
    clips: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    *,
    replace_model_results: bool,
    replace_scenario_results: str | None,
) -> bool:
    _validate_manifest(manifest)
    profile_entry = profile.as_manifest_entry()
    _validate_model(profile_entry)
    for clip in clips:
        _validate_clip(clip)
    for failure in failures:
        _validate_failure(failure)

    incoming_clip_keys = [_result_key(clip) for clip in clips]
    incoming_failure_keys = [_result_key(failure) for failure in failures]
    if len(incoming_clip_keys) != len(set(incoming_clip_keys)):
        raise ManifestError("incoming clip key が重複しています。")
    if len(incoming_failure_keys) != len(set(incoming_failure_keys)):
        raise ManifestError("incoming failure key が重複しています。")
    if set(incoming_clip_keys) & set(incoming_failure_keys):
        raise ManifestError("incoming clips と failures の key が競合しています。")

    models_by_id = {str(model["id"]): model for model in manifest["models"]}
    previous_profile = models_by_id.get(profile.id)
    version_changed = (
        previous_profile is not None and previous_profile["version"] != profile.version
    )
    models_by_id[profile.id] = profile_entry

    def is_replaced(result: dict[str, Any]) -> bool:
        return result["model"] == profile.id and (
            version_changed
            or replace_model_results
            or result["scenario"] == replace_scenario_results
        )

    clips_by_key = {
        _result_key(clip): clip for clip in manifest["clips"] if not is_replaced(clip)
    }
    failures_by_key = {
        _result_key(failure): failure
        for failure in manifest["failures"]
        if not is_replaced(failure)
    }

    for clip in clips:
        key = _result_key(clip)
        failures_by_key.pop(key, None)
        clips_by_key[key] = clip
    for failure in failures:
        key = _result_key(failure)
        clips_by_key.pop(key, None)
        failures_by_key[key] = failure

    content = {
        "format_version": FORMAT_VERSION,
        "models": sorted(
            models_by_id.values(),
            key=lambda model: str(model["id"]),
        ),
        "clips": sorted(clips_by_key.values(), key=_result_key),
        "failures": sorted(failures_by_key.values(), key=_result_key),
    }
    current_content = {
        "format_version": manifest["format_version"],
        "models": sorted(
            manifest["models"],
            key=lambda model: str(model["id"]),
        ),
        "clips": sorted(manifest["clips"], key=_result_key),
        "failures": sorted(manifest["failures"], key=_result_key),
    }
    if _canonical_json(content) == _canonical_json(current_content):
        return False

    output = {
        "format_version": FORMAT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models": content["models"],
        "clips": content["clips"],
        "failures": content["failures"],
    }
    _atomic_write_json(path, output)
    return True


def _validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        raise ManifestError("manifest のトップレベルは object が必要です。")
    if "format_version" not in manifest or manifest["format_version"] != FORMAT_VERSION:
        raise ManifestError("未対応の manifest format_version です。")
    if set(manifest) != TOP_LEVEL_KEYS:
        raise ManifestError("manifest のトップレベル項目が v2 と一致しません。")
    if not isinstance(manifest["generated_at"], str):
        raise ManifestError("manifest generated_at は文字列が必要です。")
    if not isinstance(manifest["models"], list):
        raise ManifestError("manifest models は配列が必要です。")
    if not isinstance(manifest["clips"], list):
        raise ManifestError("manifest clips は配列が必要です。")
    if not isinstance(manifest["failures"], list):
        raise ManifestError("manifest failures は配列が必要です。")

    for model in manifest["models"]:
        _validate_model(model)
    for clip in manifest["clips"]:
        _validate_clip(clip)
    for failure in manifest["failures"]:
        _validate_failure(failure)

    model_ids = [model["id"] for model in manifest["models"]]
    if len(model_ids) != len(set(model_ids)):
        raise ManifestError("manifest model id が重複しています。")
    model_id_set = set(model_ids)
    for result in [*manifest["clips"], *manifest["failures"]]:
        if result["model"] not in model_id_set:
            raise ManifestError(
                f"manifest result が存在しない model を参照しています: {result['model']}"
            )
    clip_keys = [_result_key(clip) for clip in manifest["clips"]]
    if len(clip_keys) != len(set(clip_keys)):
        raise ManifestError("manifest clip key が重複しています。")
    failure_keys = [_result_key(failure) for failure in manifest["failures"]]
    if len(failure_keys) != len(set(failure_keys)):
        raise ManifestError("manifest failure key が重複しています。")
    if set(clip_keys) & set(failure_keys):
        raise ManifestError("manifest clips と failures の key が競合しています。")


def _validate_model(model: Any) -> None:
    if not isinstance(model, dict) or set(model) != MODEL_KEYS:
        raise ManifestError("manifest model の項目が v2 と一致しません。")
    if not isinstance(model["capabilities"], dict):
        raise ManifestError("model capabilities は object が必要です。")
    if set(model["capabilities"]) != CAPABILITY_KEYS:
        raise ManifestError("model capabilities の項目が v2 と一致しません。")
    if not all(isinstance(model["capabilities"][key], bool) for key in CAPABILITY_KEYS):
        raise ManifestError("model capabilities は bool が必要です。")
    for key in ("id", "name", "version", "license_note"):
        if not isinstance(model[key], str):
            raise ManifestError(f"model {key} は文字列が必要です。")


def _validate_clip(clip: Any) -> None:
    if not isinstance(clip, dict) or set(clip) != CLIP_KEYS:
        raise ManifestError("manifest clip の項目が v2 と一致しません。")
    for key in ("model", "scenario", "line", "variant", "path", "sha256"):
        if not isinstance(clip[key], str):
            raise ManifestError(f"clip {key} は文字列が必要です。")
    if not isinstance(clip["gen_params"], dict):
        raise ManifestError("clip gen_params は object が必要です。")
    loudness = clip["loudness"]
    if not isinstance(loudness, dict) or set(loudness) != {
        "i_lufs",
        "tp_dbtp",
        "shortfall",
    }:
        raise ManifestError("clip loudness の項目が一致しません。")
    for key in ("i_lufs", "tp_dbtp"):
        value = loudness[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ManifestError(f"clip loudness.{key} は数値が必要です。")
    if not isinstance(loudness["shortfall"], bool):
        raise ManifestError("clip loudness.shortfall は bool が必要です。")
    for key in ("duration_sec", "rtf"):
        value = clip[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ManifestError(f"clip {key} は数値が必要です。")


def _validate_failure(failure: Any) -> None:
    if not isinstance(failure, dict) or set(failure) != FAILURE_KEYS:
        raise ManifestError("manifest failure の項目が v2 と一致しません。")
    for key in ("model", "scenario", "line", "variant"):
        if not isinstance(failure[key], str):
            raise ManifestError(f"failure {key} は文字列が必要です。")
    if (
        not isinstance(failure["reason"], str)
        or failure["reason"] not in FAILURE_REASONS
    ):
        raise ManifestError("failure reason が未対応です。")


def _result_key(result: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(result["model"]),
        str(result["scenario"]),
        str(result["line"]),
        str(result["variant"]),
    )


def _atomic_write_json(path: Path, output: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(output, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except BaseException as error:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                error.add_note(
                    f"manifest 一時ファイルを削除できませんでした: {cleanup_error}"
                )
        raise


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
