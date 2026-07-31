from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import tempfile
import wave
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

from gaya_pipeline.adapters.base import LineJob
from gaya_pipeline.adapters.voice_assignments import CLONE_REFERENCE_ASSIGNMENTS
from gaya_pipeline.validation import validate_scenarios
from gaya_pipeline.voice_assets import validate_voice_metadata

FORMAT_VERSION = 2
ROLE_FIELDS = (
    "name",
    "kind",
    "gender",
    "age",
    "archetype",
    "voice",
    "personality",
)
CONDITIONING_FIELDS = (*ROLE_FIELDS, "scene_setting")
MODEL_ADAPTER_FILES = {
    "aivisspeech-kohaku": "pipeline/src/gaya_pipeline/adapters/aivisspeech.py",
    "chatterbox-multilingual-v3": (
        "pipeline/src/gaya_pipeline/adapters/chatterbox.py"
    ),
    "cosyvoice3-0.5b-2512": "pipeline/src/gaya_pipeline/adapters/cosyvoice3.py",
    "gpt-sovits-v2-pro-plus": (
        "pipeline/src/gaya_pipeline/adapters/gpt_sovits.py"
    ),
    "irodori-tts-600m-v3-voicedesign": (
        "pipeline/src/gaya_pipeline/adapters/irodori_tts.py"
    ),
    "qwen3-tts-12hz-1.7b": (
        "pipeline/src/gaya_pipeline/adapters/qwen3_tts.py"
    ),
    "supertonic-3": "pipeline/src/gaya_pipeline/adapters/supertonic3.py",
    "voxcpm2": "pipeline/src/gaya_pipeline/adapters/voxcpm2.py",
}
CLONE_MODELS = {
    "chatterbox-multilingual-v3",
    "cosyvoice3-0.5b-2512",
    "gpt-sovits-v2-pro-plus",
}
_HEX_64 = frozenset("0123456789abcdef")


class RoleConditioningAuditError(RuntimeError):
    pass


def build_role_source_audit(repository_root: Path) -> dict[str, Any]:
    root = repository_root.resolve()
    scenarios_dir = root / "scenarios"
    voices_dir = root / "assets" / "voices"
    manifest_path = root / "data" / "manifest.json"

    _validate_sources(scenarios_dir, voices_dir)
    voices, voice_metadata_receipt = _load_voices(root, voices_dir)
    jobs, characters, scenarios = _load_corpus(root, scenarios_dir)
    references, reference_problems = _build_reference_receipts(
        characters,
        voices,
    )
    adapter_sources = _adapter_source_receipts(root)
    manifest, published = _load_published_provenance(
        manifest_path,
        expected_models=set(MODEL_ADAPTER_FILES),
        jobs=jobs,
    )
    production_inputs, audit_voice_sha256s = _prepare_production_generation_inputs(
        root=root,
        jobs=jobs,
    )

    receipts: list[dict[str, Any]] = []
    for job in jobs:
        truth = _line_truth(job)
        for model in MODEL_ADAPTER_FILES:
            source_conditioning = _source_conditioning_receipt(
                model=model,
                job=job,
                truth=truth,
                voices=voices,
                production_input=production_inputs[
                    (model, job.scenario_id, job.line_id)
                ],
                audit_voice_sha256s=audit_voice_sha256s,
            )
            published_item = published[(model, job.scenario_id, job.line_id)]
            comparison = _compare_published_conditioning(
                model=model,
                source=source_conditioning,
                published=published_item,
            )
            receipts.append(
                {
                    "model": model,
                    "scenario": job.scenario_id,
                    "line": job.line_id,
                    "character": truth["character"],
                    "role_truth": truth,
                    "adapter_source": adapter_sources[model],
                    "field_transport": source_conditioning["field_transport"],
                    "unsupported_fields": source_conditioning[
                        "unsupported_fields"
                    ],
                    "speaker": source_conditioning["speaker"],
                    "preset": source_conditioning["preset"],
                    "reference": source_conditioning["reference"],
                    "prompt": source_conditioning["prompt"],
                    "input_identity": source_conditioning["input_identity"],
                    "published_provenance": published_item,
                    "published_comparison": comparison,
                },
            )

    comparison_counts = {
        status: sum(
            receipt["published_comparison"]["status"] == status
            for receipt in receipts
        )
        for status in ("match", "mismatch", "unverifiable", "failure")
    }
    all_references = references
    assigned_references = [
        item for item in references if item["source"] == "adapter_assignment"
    ]
    problems = reference_problems
    summary = {
        "scenario_count": len(scenarios),
        "character_count": len(characters),
        "line_count": len(jobs),
        "model_count": len(MODEL_ADAPTER_FILES),
        "conditioning_receipt_count": len(receipts),
        "explicit_reference_character_count": sum(
            item["source"] == "scenario" for item in references
        ),
        "assigned_reference_character_count": len(assigned_references),
        **_reference_summary("all_reference", all_references),
        **_reference_summary("assigned_reference", assigned_references),
        "published_candidate_count": sum(
            item["status"] == "candidate" for item in published.values()
        ),
        "published_failure_count": sum(
            item["status"] == "failure" for item in published.values()
        ),
        "published_conditioning_match_count": comparison_counts["match"],
        "published_conditioning_mismatch_count": comparison_counts["mismatch"],
        "published_conditioning_unverifiable_count": comparison_counts[
            "unverifiable"
        ],
        "published_conditioning_failure_count": comparison_counts["failure"],
        "problem_count": len(problems),
    }
    if summary["conditioning_receipt_count"] != 8 * 161:
        raise RoleConditioningAuditError(
            "conditioning receipt は 8 models × 161 lines が必要です。",
        )

    return {
        "format_version": FORMAT_VERSION,
        "scenario_schema": {
            "file": "scenarios/schema/scenario.schema.json",
            "sha256": _sha256_file(
                scenarios_dir / "schema" / "scenario.schema.json",
            ),
        },
        "voice_metadata": voice_metadata_receipt,
        "published_manifest": {
            "file": manifest_path.relative_to(root).as_posix(),
            "sha256": _sha256_file(manifest_path),
            "format_version": manifest["format_version"],
            "candidate_set_sha256": manifest["candidate_set_sha256"],
            "generated_at": manifest["generated_at"],
        },
        "adapter_sources": [
            adapter_sources[model] for model in MODEL_ADAPTER_FILES
        ],
        "summary": summary,
        "scenarios": scenarios,
        "characters": characters,
        "all_references": all_references,
        "assigned_references": assigned_references,
        "conditioning_receipts": receipts,
        "problems": problems,
    }


def write_role_source_audit(
    report: Mapping[str, Any],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8",
        ),
    )


def _validate_sources(scenarios_dir: Path, voices_dir: Path) -> None:
    validation = validate_scenarios(scenarios_dir)
    if validation.problems:
        details = "\n".join(str(problem) for problem in validation.problems)
        raise RoleConditioningAuditError(
            f"シナリオ検証に失敗しました:\n{details}",
        )
    voice_validation = validate_voice_metadata(voices_dir)
    if voice_validation.problems:
        details = "\n".join(
            f"{problem.file.as_posix()} [{problem.target}] {problem.reason}"
            for problem in voice_validation.problems
        )
        raise RoleConditioningAuditError(
            f"参照音声検証に失敗しました:\n{details}",
        )


def _load_voices(
    root: Path,
    voices_dir: Path,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, str]]:
    metadata_path = voices_dir / "metadata.yaml"
    metadata = _load_yaml_mapping(metadata_path)
    values = metadata.get("voices")
    if not isinstance(values, list) or any(
        not isinstance(item, Mapping) for item in values
    ):
        raise RoleConditioningAuditError(
            f"voices metadata の voices が不正です: {metadata_path}",
        )
    voices: dict[str, Mapping[str, Any]] = {}
    for value in values:
        assert isinstance(value, Mapping)
        voice_id = _required_string(value, "id", "voice")
        if voice_id in voices:
            raise RoleConditioningAuditError(
                f"voices metadata id が重複しています: {voice_id}",
            )
        voices[voice_id] = value
    return voices, {
        "file": metadata_path.relative_to(root).as_posix(),
        "sha256": _sha256_file(metadata_path),
    }


def _load_corpus(
    root: Path,
    scenarios_dir: Path,
) -> tuple[list[LineJob], list[dict[str, Any]], list[dict[str, str]]]:
    jobs: list[LineJob] = []
    characters: list[dict[str, Any]] = []
    scenarios: list[dict[str, str]] = []
    for path in sorted(scenarios_dir.glob("*.yaml")):
        document = _load_yaml_mapping(path)
        scenario_id = _required_string(document, "id", path.name)
        locale = _required_string(document, "locale", scenario_id)
        scene_value = document.get("scene")
        character_values = document.get("characters")
        line_values = document.get("lines")
        if (
            not isinstance(scene_value, Mapping)
            or not isinstance(character_values, list)
            or not isinstance(line_values, list)
        ):
            raise RoleConditioningAuditError(
                f"scene / characters / lines が不正です: {path}",
            )
        scene = {
            "id": scenario_id,
            "title": _required_string(document, "title", scenario_id),
            **scene_value,
        }
        by_id: dict[str, Mapping[str, Any]] = {}
        line_ids: dict[str, list[str]] = {}
        for value in character_values:
            if not isinstance(value, Mapping):
                raise RoleConditioningAuditError(
                    f"character が object ではありません: {path}",
                )
            character_id = _required_string(
                value,
                "id",
                f"{scenario_id}.character",
            )
            if character_id in by_id:
                raise RoleConditioningAuditError(
                    f"character id が重複しています: {scenario_id}/{character_id}",
                )
            by_id[character_id] = value
            line_ids[character_id] = []
        for value in line_values:
            if not isinstance(value, Mapping):
                raise RoleConditioningAuditError(
                    f"line が object ではありません: {path}",
                )
            line_id = _required_string(value, "id", f"{scenario_id}.line")
            character_id = _required_string(
                value,
                "character",
                f"{scenario_id}/{line_id}",
            )
            if character_id not in by_id:
                raise RoleConditioningAuditError(
                    f"未登録 character です: {scenario_id}/{line_id}",
                )
            line_ids[character_id].append(line_id)
            jobs.append(
                LineJob(
                    scene=scene,
                    character=by_id[character_id],
                    line=value,
                    locale=locale,
                ),
            )
        for character_id, character in by_id.items():
            truth = _character_truth(scenario_id, character)
            characters.append(
                {
                    **truth,
                    "declared_reference_voice": character["reference_voice"],
                    "line_ids": line_ids[character_id],
                },
            )
        scenarios.append(
            {
                "id": scenario_id,
                "file": path.relative_to(root).as_posix(),
                "sha256": _sha256_file(path),
            },
        )
    return jobs, characters, scenarios


def _character_truth(
    scenario_id: str,
    character: Mapping[str, Any],
) -> dict[str, str]:
    missing = [
        field
        for field in (
            "id",
            "name",
            "gender",
            "age",
            "archetype",
            "voice",
            "personality",
            "reference_voice",
        )
        if field not in character
    ]
    if missing:
        raise RoleConditioningAuditError(
            f"{scenario_id} の role field が不足しています: {missing}",
        )
    kind = character["kind"] if "kind" in character else "human"
    if not isinstance(kind, str) or not kind:
        raise RoleConditioningAuditError(
            f"{scenario_id}.character.kind が不正です。",
        )
    return {
        "scenario": scenario_id,
        "character": _required_string(character, "id", scenario_id),
        "name": _required_string(character, "name", scenario_id),
        "kind": kind,
        "gender": _required_string(character, "gender", scenario_id),
        "age": _required_string(character, "age", scenario_id),
        "archetype": _required_string(character, "archetype", scenario_id),
        "voice": _required_string(character, "voice", scenario_id),
        "personality": _required_string(character, "personality", scenario_id),
    }


def _line_truth(job: LineJob) -> dict[str, Any]:
    truth = _character_truth(job.scenario_id, job.character)
    return {
        **truth,
        "scene_setting": _required_string(job.scene, "setting", job.scenario_id),
        "text": _required_string(job.line, "text", job.line_id),
        "reading": job.line.get("reading"),
        "emotion": _required_string(job.line, "emotion", job.line_id),
        "intensity": job.line["intensity"],
        "delivery": _required_string(job.line, "delivery", job.line_id),
        "declared_reference_voice": job.character["reference_voice"],
    }


def _build_reference_receipts(
    characters: Sequence[Mapping[str, Any]],
    voices: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    expected_assignments = {
        (str(item["scenario"]), str(item["character"]))
        for item in characters
        if item["declared_reference_voice"] is None
    }
    if set(CLONE_REFERENCE_ASSIGNMENTS) != expected_assignments:
        raise RoleConditioningAuditError(
            "clone reference assignment coverage が scenario と一致しません。",
        )
    receipts: list[dict[str, Any]] = []
    problems: list[dict[str, str]] = []
    for character in characters:
        key = (str(character["scenario"]), str(character["character"]))
        declared = character["declared_reference_voice"]
        if declared is None:
            voice_id = CLONE_REFERENCE_ASSIGNMENTS[key]
            source = "adapter_assignment"
        elif isinstance(declared, str):
            voice_id = declared
            source = "scenario"
        else:
            raise RoleConditioningAuditError(
                f"reference_voice が不正です: {key[0]}/{key[1]}",
            )
        try:
            voice = voices[voice_id]
        except KeyError as error:
            raise RoleConditioningAuditError(
                f"未登録 reference voice です: {voice_id}",
            ) from error
        voice_identity = voice.get("voice")
        if not isinstance(voice_identity, Mapping):
            raise RoleConditioningAuditError(
                f"reference voice identity がありません: {voice_id}",
            )
        gender = str(character["gender"])
        reference_gender = _required_string(
            voice_identity,
            "gender",
            voice_id,
        )
        if gender == "neutral":
            gender_status = "unsupported_neutral"
        elif gender == reference_gender:
            gender_status = "exact"
        else:
            gender_status = "mismatch"
            problems.append(
                {
                    "target": f"{key[0]}/{key[1]}",
                    "reason": (
                        "clone reference gender mismatch: "
                        f"character={gender}, reference={reference_gender}"
                    ),
                },
            )
        age = str(character["age"])
        reference_age = _required_string(voice_identity, "age", voice_id)
        age_status = "exact" if age == reference_age else "approximate"
        receipts.append(
            {
                "scenario": key[0],
                "character": key[1],
                "line_count": len(character["line_ids"]),
                "source": source,
                "reference_voice": voice_id,
                "reference_sha256": _required_sha256(
                    voice,
                    "sha256",
                    voice_id,
                ),
                "character_gender": gender,
                "reference_gender": reference_gender,
                "gender_status": gender_status,
                "character_age": age,
                "reference_age": reference_age,
                "age_status": age_status,
                "role_exact": (
                    gender_status == "exact" and age_status == "exact"
                ),
            },
        )
    return receipts, problems


def _reference_summary(
    prefix: str,
    references: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    return {
        f"{prefix}_character_count": len(references),
        f"{prefix}_gender_exact_character_count": sum(
            item["gender_status"] == "exact" for item in references
        ),
        f"{prefix}_gender_unsupported_neutral_character_count": sum(
            item["gender_status"] == "unsupported_neutral"
            for item in references
        ),
        f"{prefix}_gender_mismatch_character_count": sum(
            item["gender_status"] == "mismatch" for item in references
        ),
        f"{prefix}_age_exact_character_count": sum(
            item["age_status"] == "exact" for item in references
        ),
        f"{prefix}_age_approximate_character_count": sum(
            item["age_status"] == "approximate" for item in references
        ),
        f"{prefix}_role_exact_character_count": sum(
            bool(item["role_exact"]) for item in references
        ),
    }


def _adapter_source_receipts(root: Path) -> dict[str, dict[str, str]]:
    receipts: dict[str, dict[str, str]] = {}
    for model, relative in MODEL_ADAPTER_FILES.items():
        path = root / relative
        if not path.is_file():
            raise RoleConditioningAuditError(
                f"adapter source がありません: {path}",
            )
        receipts[model] = {
            "model": model,
            "file": relative,
            "sha256": _sha256_file(path),
        }
    return receipts


class _PrepareOnlyAuditRuntime:
    def prepare(self, *_args: Any, **_kwargs: Any) -> dict[str, float] | None:
        if _kwargs:
            return {
                "allocated_mib": 0.0,
                "reserved_mib": 0.0,
            }
        return None

    def is_out_of_memory(self, _error: BaseException) -> bool:
        return False


class _QwenAuditRuntime:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._repositories: dict[Path, str] = {}

    def snapshot_download(self, repo_id: str, revision: str) -> Path:
        path = self._root / "snapshots" / repo_id.replace("/", "--") / revision
        path.mkdir(parents=True, exist_ok=True)
        self._repositories[path] = repo_id
        return path

    def load_model(self, snapshot_path: Path) -> dict[str, str]:
        return {"repository": self._repositories[snapshot_path]}

    def generate_voice_design(
        self,
        _model: Any,
        *,
        text: str,
        language: str,
        instruct: str,
        sampling: Mapping[str, Any],
    ) -> tuple[list[list[float]], int]:
        if not text or language != "Japanese" or not instruct or not sampling:
            raise RoleConditioningAuditError(
                "Qwen audit runtime に不正な VoiceDesign input が渡されました。",
            )
        return ([[0.0, 0.25, -0.25, 0.0]], 24_000)

    def write_pcm16(
        self,
        path: Path,
        samples: Sequence[float],
        sample_rate: int,
    ) -> None:
        _write_pcm16(path, samples, sample_rate)

    def seed(self, _seed: int) -> None:
        return

    def reset_peak_memory_stats(self) -> None:
        return

    def peak_memory_mib(self) -> dict[str, float]:
        return {"allocated_mib": 0.0, "reserved_mib": 0.0}

    def release_model(self) -> None:
        return

    def is_out_of_memory(self, _error: BaseException) -> bool:
        return False


class _IrodoriAuditRuntime:
    def prepare(self) -> dict[str, float]:
        return {"allocated_mib": 0.0, "reserved_mib": 0.0}

    def synthesize(
        self,
        *,
        text: str,
        caption: str,
        reference_wav: Path | None,
        output_wav: Path,
        seed: int,
    ) -> dict[str, Any]:
        del reference_wav
        if not text or not caption:
            raise RoleConditioningAuditError(
                "Irodori audit runtime に空の input が渡されました。",
            )
        _write_pcm16(output_wav, (0.0, 0.25, -0.25, 0.0), 48_000)
        return {
            "phase_peak_vram_mib": {
                "generation": {
                    "allocated_mib": 0.0,
                    "reserved_mib": 0.0,
                },
            },
            "seed": seed,
            "sample_rate_hz": 48_000,
            "silentcipher_watermark_stage_executed": True,
        }

    def is_out_of_memory(self, _error: BaseException) -> bool:
        return False


class _VoxAuditRuntime:
    def __init__(self) -> None:
        self._phase = 0

    def load_model(self, _snapshot_path: Path) -> dict[str, str]:
        return {"model": "voxcpm2"}

    def model_identity(self, _model: Any) -> dict[str, Any]:
        from gaya_pipeline.adapters import voxcpm2

        return {
            "architecture": voxcpm2.ARCHITECTURE,
            "sample_rate_hz": voxcpm2.SAMPLE_RATE_HZ,
            "device": voxcpm2.DEVICE,
            "dtype": voxcpm2.DTYPE,
        }

    def generate(
        self,
        _model: Any,
        *,
        text: str,
        reference_wav_path: Path | None,
        seed: int,
    ) -> list[float]:
        del reference_wav_path, seed
        if not text:
            raise RoleConditioningAuditError(
                "VoxCPM2 audit runtime に空の input が渡されました。",
            )
        return [0.0, 0.25, -0.25, 0.0]

    def write_pcm16(
        self,
        path: Path,
        samples: Sequence[float],
        sample_rate: int,
    ) -> None:
        _write_pcm16(path, samples, sample_rate)

    def reset_peak_memory_stats(self) -> None:
        self._phase += 1

    def peak_memory_mib(self) -> dict[str, float]:
        return {
            "allocated_mib": float(self._phase),
            "reserved_mib": float(self._phase),
        }

    def is_out_of_memory(self, _error: BaseException) -> bool:
        return False


def _write_pcm16(
    path: Path,
    samples: Sequence[float],
    sample_rate: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(
            b"".join(
                struct.pack(
                    "<h",
                    round(max(-1.0, min(1.0, float(sample))) * 32_767),
                )
                for sample in samples
            ),
        )


def _build_audit_voice_kit(
    canonical_voices_dir: Path,
    output_dir: Path,
) -> dict[str, str]:
    metadata_path = canonical_voices_dir / "metadata.yaml"
    schema_path = canonical_voices_dir / "metadata.schema.json"
    metadata = dict(_load_yaml_mapping(metadata_path))
    values = metadata.get("voices")
    if not isinstance(values, list) or any(
        not isinstance(value, Mapping) for value in values
    ):
        raise RoleConditioningAuditError(
            f"voices metadata の voices が不正です: {metadata_path}",
        )
    output_dir.mkdir(parents=True, exist_ok=False)
    audit_sha256s: dict[str, str] = {}
    audit_voices: list[dict[str, Any]] = []
    for index, raw_value in enumerate(values):
        assert isinstance(raw_value, Mapping)
        value = dict(raw_value)
        voice_id = _required_string(value, "id", "audit voice")
        relative_file = _required_string(value, "file", voice_id)
        duration = value.get("duration_sec")
        if not isinstance(duration, (int, float)):
            raise RoleConditioningAuditError(
                f"{voice_id}.duration_sec は number が必要です。",
            )
        frame_count = round(float(duration) * 48_000)
        if frame_count <= 0:
            raise RoleConditioningAuditError(
                f"{voice_id}.duration_sec は正数が必要です。",
            )
        wav_path = output_dir / relative_file
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        sample = struct.pack("<h", 1_000 * (index + 1))
        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(48_000)
            wav_file.writeframes(sample * frame_count)
        audit_sha256 = _sha256_file(wav_path)
        value["sha256"] = audit_sha256
        audit_sha256s[voice_id] = audit_sha256
        audit_voices.append(value)
    metadata["voices"] = audit_voices
    (output_dir / "metadata.yaml").write_text(
        yaml.safe_dump(
            metadata,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    if not schema_path.is_file():
        raise RoleConditioningAuditError(
            f"voice metadata schema がありません: {schema_path}",
        )
    (output_dir / "metadata.schema.json").write_bytes(schema_path.read_bytes())
    return audit_sha256s


def _prepare_production_generation_inputs(
    *,
    root: Path,
    jobs: Sequence[LineJob],
) -> tuple[
    dict[tuple[str, str, str], Mapping[str, Any]],
    dict[str, str],
]:
    from gaya_pipeline.adapters import (
        aivisspeech,
        chatterbox,
        cosyvoice3,
        gpt_sovits,
        irodori_tts,
        qwen3_tts,
        supertonic3,
        voxcpm2,
    )

    result: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="gaya-role-audit-") as raw_temp:
        temporary_root = Path(raw_temp)
        voices_dir = temporary_root / "voices"
        audit_voice_sha256s = _build_audit_voice_kit(
            root / "assets" / "voices",
            voices_dir,
        )
        supertonic_root = temporary_root / "supertonic-model"
        supertonic_root.mkdir()
        vox_root = temporary_root / "voxcpm-model"
        vox_root.mkdir()
        adapters: dict[str, Any] = {
            "aivisspeech-kohaku": aivisspeech.AivisSpeechAdapter(
                runtime=_PrepareOnlyAuditRuntime(),
            ),
            "chatterbox-multilingual-v3": chatterbox.ChatterboxAdapter(
                runtime=_PrepareOnlyAuditRuntime(),
                model_root=temporary_root,
            ),
            "cosyvoice3-0.5b-2512": cosyvoice3.CosyVoice3Adapter(
                runtime=_PrepareOnlyAuditRuntime(),
            ),
            "gpt-sovits-v2-pro-plus": gpt_sovits.GPTSoVITSAdapter(
                runtime=_PrepareOnlyAuditRuntime(),
                upstream_root=temporary_root,
            ),
            "irodori-tts-600m-v3-voicedesign": (
                irodori_tts.IrodoriTTSAdapter(
                    runtime=_IrodoriAuditRuntime(),
                    reading_converter=lambda text: text,
                )
            ),
            "qwen3-tts-12hz-1.7b": qwen3_tts.Qwen3TTSAdapter(
                runtime=_QwenAuditRuntime(temporary_root),
            ),
            "supertonic-3": supertonic3.Supertonic3Adapter(
                runtime=_PrepareOnlyAuditRuntime(),
            ),
            "voxcpm2": voxcpm2.VoxCPM2Adapter(
                runtime=_VoxAuditRuntime(),
                reading_converter=lambda text: text,
                model_root=vox_root,
            ),
        }
        for model, adapter in adapters.items():
            artifacts_dir = temporary_root / "artifacts" / model
            if model == "supertonic-3":
                with mock.patch.dict(
                    os.environ,
                    {supertonic3.MODEL_ROOT_ENV: str(supertonic_root)},
                ):
                    adapter.prepare(jobs, artifacts_dir, voices_dir)
            elif model == "voxcpm2":
                with mock.patch.object(
                    voxcpm2,
                    "_validate_model_snapshot",
                    lambda _path: None,
                ):
                    adapter.prepare(jobs, artifacts_dir, voices_dir)
            else:
                adapter.prepare(jobs, artifacts_dir, voices_dir)
            context = adapter.take_recipe().single_take_context()
            for job in jobs:
                value = adapter.generation_input(job, context)
                if not isinstance(value, Mapping):
                    raise RoleConditioningAuditError(
                        f"{model}.generation_input() が object ではありません。",
                    )
                result[(model, job.scenario_id, job.line_id)] = dict(value)
    expected_count = len(MODEL_ADAPTER_FILES) * len(jobs)
    if len(result) != expected_count:
        raise RoleConditioningAuditError(
            "production generation_input receipt coverage が不足しています。",
        )
    return result, audit_voice_sha256s


def _source_conditioning_receipt(
    *,
    model: str,
    job: LineJob,
    truth: Mapping[str, Any],
    voices: Mapping[str, Mapping[str, Any]],
    production_input: Mapping[str, Any],
    audit_voice_sha256s: Mapping[str, str],
) -> dict[str, Any]:
    unsupported = {field: "unsupported" for field in CONDITIONING_FIELDS}
    speaker: dict[str, Any] | None = None
    preset: dict[str, Any] | None = None
    reference: dict[str, Any] | None = None
    prompt: dict[str, Any] | None = None
    payload = dict(production_input)

    if model == "aivisspeech-kohaku":
        style = payload.get("speaker_style")
        if not isinstance(style, Mapping):
            raise RoleConditioningAuditError(
                "Aivis production generation_input に speaker_style がありません。",
            )
        speaker = {
            "speaker_uuid": _required_string(
                payload,
                "speaker_uuid",
                model,
            ),
            "style": dict(style),
            "selection_source": "fixed_speaker_emotion_style",
        }
        field_transport = unsupported
    elif model in CLONE_MODELS:
        voice_id = _required_string(
            payload,
            "reference_voice",
            f"{model}.generation_input",
        )
        selection_source = _required_string(
            payload,
            "reference_selection_source",
            f"{model}.generation_input",
        )
        expected_declared = truth["declared_reference_voice"]
        expected_voice = (
            str(expected_declared)
            if expected_declared is not None
            else CLONE_REFERENCE_ASSIGNMENTS[
                (str(truth["scenario"]), str(truth["character"]))
            ]
        )
        if voice_id != expected_voice:
            raise RoleConditioningAuditError(
                f"{model} production generation_input の reference_voice が"
                f" assignment と一致しません: {voice_id} != {expected_voice}",
            )
        voice = voices[voice_id]
        canonical_sha256 = _required_sha256(voice, "sha256", voice_id)
        audit_fixture_sha256 = _required_sha256(
            audit_voice_sha256s,
            voice_id,
            "audit voice fixture",
        )
        if model == "gpt-sovits-v2-pro-plus":
            source_sha256 = _required_sha256(
                payload,
                "reference_source_sha256",
                f"{model}.generation_input",
            )
            reference_sha256 = _required_sha256(
                payload,
                "reference_clip_sha256",
                f"{model}.generation_input",
            )
            if source_sha256 != audit_fixture_sha256:
                raise RoleConditioningAuditError(
                    "GPT-SoVITS production input の source SHA が audit fixture"
                    " と一致しません。",
                )
            if reference_sha256 == audit_fixture_sha256:
                raise RoleConditioningAuditError(
                    "GPT-SoVITS production input は固定5秒 clip SHA が必要です。",
                )
        else:
            reference_sha256 = _required_sha256(
                payload,
                "reference_sha256",
                f"{model}.generation_input",
            )
            if reference_sha256 != audit_fixture_sha256:
                raise RoleConditioningAuditError(
                    f"{model} production generation_input の reference SHA が"
                    " audit fixture と一致しません。",
                )
        reference = {
            "control": "voice_clone",
            "source": selection_source,
            "voice": voice_id,
            "sha256": canonical_sha256,
            "source_sha256": canonical_sha256,
            "prepare_state_sha256": reference_sha256,
            "audit_fixture_source_sha256": audit_fixture_sha256,
        }
        field_transport = {
            **unsupported,
            "gender": "reference_audio",
            "age": "reference_audio",
            "voice": "reference_audio",
        }
    elif model == "irodori-tts-600m-v3-voicedesign":
        role_identity = payload.get("role_identity")
        _require_role_identity(
            model,
            role_identity,
            truth,
            include_scene=False,
        )
        caption = _required_string(
            payload,
            "caption",
            f"{model}.generation_input",
        )
        _require_prompt_values(
            model,
            caption,
            truth,
            fields=CONDITIONING_FIELDS,
        )
        reference_voice = payload.get("reference_voice")
        if reference_voice is not None and not isinstance(reference_voice, str):
            raise RoleConditioningAuditError(
                "Irodori reference_voice は string または null が必要です。",
            )
        reference_sha256 = _required_sha256(
            payload,
            "reference_sha256",
            f"{model}.generation_input",
        )
        declared_reference = truth["declared_reference_voice"]
        canonical_sha256: str | None = None
        audit_fixture_sha256: str | None = None
        if declared_reference is not None:
            voice_id = str(declared_reference)
            if reference_voice != voice_id:
                raise RoleConditioningAuditError(
                    "Irodori production generation_input が scenario reference"
                    " を使用していません。",
                )
            canonical_sha256 = _required_sha256(
                voices[voice_id],
                "sha256",
                voice_id,
            )
            audit_fixture_sha256 = _required_sha256(
                audit_voice_sha256s,
                voice_id,
                "audit voice fixture",
            )
            if reference_sha256 != audit_fixture_sha256:
                raise RoleConditioningAuditError(
                    "Irodori production generation_input の reference SHA が"
                    " audit fixture と一致しません。",
                )
        reference = {
            "control": _required_string(
                payload,
                "reference_control",
                f"{model}.generation_input",
            ),
            "source": _required_string(
                payload,
                "reference_source",
                f"{model}.generation_input",
            ),
            "voice": reference_voice,
            "sha256": canonical_sha256,
            "source_sha256": canonical_sha256,
            "prepare_state_sha256": reference_sha256,
            "audit_fixture_source_sha256": audit_fixture_sha256,
        }
        prompt = _prompt_receipt(
            text=caption,
            fields=CONDITIONING_FIELDS,
            kind="target_caption",
        )
        field_transport = {
            field: "target_caption" for field in CONDITIONING_FIELDS
        }
    elif model == "qwen3-tts-12hz-1.7b":
        identity = payload.get("character_identity")
        _require_role_identity(model, identity, truth, include_scene=True)
        control = _required_string(
            payload,
            "reference_control",
            f"{model}.generation_input",
        )
        source_id = _required_string(
            payload,
            "reference_source_id",
            f"{model}.generation_input",
        )
        reference_sha256 = _required_sha256(
            payload,
            "reference_sha256",
            f"{model}.generation_input",
        )
        reference = {
            "control": control,
            "source": source_id,
            "voice": (
                source_id
                if truth["declared_reference_voice"] is not None
                else None
            ),
            "sha256": None,
            "source_sha256": None,
            "prepare_state_sha256": reference_sha256,
            "audit_fixture_source_sha256": None,
        }
        if truth["declared_reference_voice"] is None:
            prompt = {
                "kind": "voice_design_character_identity",
                "fields": list(CONDITIONING_FIELDS),
                "text": None,
                "sha256": None,
            }
            field_transport = {
                field: "voice_design_prompt" for field in CONDITIONING_FIELDS
            }
        else:
            voice_id = str(truth["declared_reference_voice"])
            if source_id != voice_id:
                raise RoleConditioningAuditError(
                    "Qwen production generation_input が scenario reference を"
                    "使用していません。",
                )
            canonical_sha256 = _required_sha256(
                voices[voice_id],
                "sha256",
                voice_id,
            )
            audit_fixture_sha256 = _required_sha256(
                audit_voice_sha256s,
                voice_id,
                "audit voice fixture",
            )
            if reference_sha256 != audit_fixture_sha256:
                raise RoleConditioningAuditError(
                    "Qwen production generation_input の reference SHA が"
                    " audit fixture と一致しません。",
                )
            reference["sha256"] = canonical_sha256
            reference["source_sha256"] = canonical_sha256
            reference["audit_fixture_source_sha256"] = audit_fixture_sha256
            field_transport = {
                **unsupported,
                "gender": "reference_audio",
                "age": "reference_audio",
                "voice": "reference_audio",
            }
    elif model == "supertonic-3":
        preset = {
            "voice_style": _required_string(
                payload,
                "voice_style",
                f"{model}.generation_input",
            ),
            "voice_style_sha256": _required_sha256(
                payload,
                "voice_style_sha256",
                f"{model}.generation_input",
            ),
            "selection_source": _required_string(
                payload,
                "voice_selection_source",
                f"{model}.generation_input",
            ),
        }
        field_transport = {
            **unsupported,
            "gender": "preset_voice",
            "voice": "preset_voice",
        }
    elif model == "voxcpm2":
        provenance = payload.get("reference_provenance")
        if not isinstance(provenance, Mapping):
            raise RoleConditioningAuditError(
                "VoxCPM2 production generation_input に reference_provenance"
                " がありません。",
            )
        reference_kind = _required_string(
            payload,
            "reference_kind",
            f"{model}.generation_input",
        )
        reference_source = _required_string(
            payload,
            "reference_selection_source",
            f"{model}.generation_input",
        )
        reference_voice = payload.get("reference_voice")
        reference_sha256 = _required_sha256(
            payload,
            "reference_sha256",
            f"{model}.generation_input",
        )
        if truth["declared_reference_voice"] is None:
            design_identity = provenance.get("identity")
            _require_selected_identity(
                model,
                design_identity,
                truth,
                fields=("gender", "age", "archetype", "voice", "personality"),
            )
            reference = {
                "control": "voice_design_then_clone",
                "source": reference_source,
                "voice": None,
                "sha256": None,
                "source_sha256": None,
                "prepare_state_sha256": reference_sha256,
                "audit_fixture_source_sha256": None,
            }
            assert isinstance(design_identity, Mapping)
            instruction = _required_string(
                design_identity,
                "instruction",
                "VoxCPM2 voice_design identity",
            )
            prompt = _prompt_receipt(
                text=instruction,
                fields=("gender", "age", "archetype", "voice", "personality"),
                kind="voice_design_prompt",
            )
            field_transport = {
                **unsupported,
                "gender": "voice_design_prompt",
                "age": "voice_design_prompt",
                "archetype": "voice_design_prompt",
                "voice": "voice_design_prompt",
                "personality": "voice_design_prompt",
            }
        else:
            voice_id = str(truth["declared_reference_voice"])
            canonical_sha256 = _required_sha256(
                voices[voice_id],
                "sha256",
                voice_id,
            )
            audit_fixture_sha256 = _required_sha256(
                audit_voice_sha256s,
                voice_id,
                "audit voice fixture",
            )
            if (
                reference_kind != "asset"
                or reference_voice != voice_id
                or reference_sha256 != audit_fixture_sha256
            ):
                raise RoleConditioningAuditError(
                    "VoxCPM2 production generation_input が scenario reference"
                    " と一致しません。",
                )
            reference = {
                "control": "explicit_audio_reference",
                "source": reference_source,
                "voice": voice_id,
                "sha256": canonical_sha256,
                "source_sha256": canonical_sha256,
                "prepare_state_sha256": reference_sha256,
                "audit_fixture_source_sha256": audit_fixture_sha256,
            }
            field_transport = {
                **unsupported,
                "gender": "reference_audio",
                "age": "reference_audio",
                "voice": "reference_audio",
            }
    else:
        raise RoleConditioningAuditError(f"未対応 model です: {model}")

    unsupported_fields = [
        field
        for field in CONDITIONING_FIELDS
        if field_transport[field] == "unsupported"
    ]
    return {
        "field_transport": field_transport,
        "unsupported_fields": unsupported_fields,
        "speaker": speaker,
        "preset": preset,
        "reference": reference,
        "prompt": prompt,
        "input_identity": {
            "sha256": _sha256_json(payload),
            "payload": payload,
        },
    }


def _require_role_identity(
    model: str,
    actual: Any,
    truth: Mapping[str, Any],
    *,
    include_scene: bool,
) -> None:
    fields = list(ROLE_FIELDS)
    if include_scene:
        fields.append("scene_setting")
    _require_selected_identity(model, actual, truth, fields=fields)


def _require_selected_identity(
    model: str,
    actual: Any,
    truth: Mapping[str, Any],
    *,
    fields: Sequence[str],
) -> None:
    if not isinstance(actual, Mapping):
        raise RoleConditioningAuditError(
            f"{model} の role identity receipt が object ではありません。",
        )
    expected = {
        "scenario": truth["scenario"],
        "character": truth["character"],
        **{field: truth[field] for field in fields},
    }
    for key, value in expected.items():
        if actual.get(key) != value:
            raise RoleConditioningAuditError(
                f"{model} が role field を正しく伝達していません: "
                f"{key}=expected {value!r}, actual {actual.get(key)!r}",
            )


def _prompt_receipt(
    *,
    text: str,
    fields: Sequence[str],
    kind: str,
) -> dict[str, Any]:
    if not text:
        raise RoleConditioningAuditError(f"{kind} prompt が空です。")
    return {
        "kind": kind,
        "fields": list(fields),
        "text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _require_prompt_values(
    model: str,
    prompt: str,
    truth: Mapping[str, Any],
    *,
    fields: Sequence[str],
) -> None:
    _require_prompt_literals(
        model,
        prompt,
        tuple(str(truth[field]) for field in fields),
    )


def _require_prompt_literals(
    model: str,
    prompt: str,
    expected: Sequence[str],
) -> None:
    missing = [value for value in expected if value not in prompt]
    if missing:
        raise RoleConditioningAuditError(
            f"{model} が role field を prompt へ伝達していません: {missing}",
        )


def _load_published_provenance(
    manifest_path: Path,
    *,
    expected_models: set[str],
    jobs: Sequence[LineJob],
) -> tuple[Mapping[str, Any], dict[tuple[str, str, str], dict[str, Any]]]:
    manifest = _load_json_mapping(manifest_path)
    models = manifest.get("models")
    candidates = manifest.get("candidates")
    failures = manifest.get("failures")
    if (
        not isinstance(models, list)
        or not isinstance(candidates, list)
        or not isinstance(failures, list)
    ):
        raise RoleConditioningAuditError(
            f"published manifest の構造が不正です: {manifest_path}",
        )
    actual_models = {
        _required_string(model, "id", "manifest.model")
        for model in models
        if isinstance(model, Mapping)
    }
    if actual_models != expected_models:
        raise RoleConditioningAuditError(
            "published manifest の model set が監査対象8モデルと一致しません。",
        )
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise RoleConditioningAuditError("manifest candidate が不正です。")
        key = _manifest_group_key(candidate)
        if key in result:
            raise RoleConditioningAuditError(
                f"published candidate が重複しています: {key}",
            )
        gen_params = candidate.get("gen_params")
        if not isinstance(gen_params, Mapping):
            raise RoleConditioningAuditError(
                f"candidate gen_params が不正です: {key}",
            )
        requested = gen_params.get("requested")
        realized = gen_params.get("realized")
        if not isinstance(requested, Mapping) or not isinstance(realized, Mapping):
            raise RoleConditioningAuditError(
                f"candidate sidecar provenance が不正です: {key}",
            )
        result[key] = {
            "status": "candidate",
            "take_id": _required_sha256(candidate, "take_id", str(key)),
            "take_index": candidate["take_index"],
            "generation_input_sha256": _required_sha256(
                candidate,
                "generation_input_sha256",
                str(key),
            ),
            "audio_sha256": _required_sha256(candidate, "sha256", str(key)),
            "audio_path": _required_string(candidate, "path", str(key)),
            "requested_params_sha256": _sha256_json(requested),
            "realized_params_sha256": _sha256_json(realized),
            "recipe_version": gen_params.get("recipe_version"),
            "seed": gen_params.get("seed"),
            "conditioning_evidence": _published_conditioning_evidence(realized),
        }
    for failure in failures:
        if not isinstance(failure, Mapping):
            raise RoleConditioningAuditError("manifest failure が不正です。")
        key = _manifest_group_key(failure)
        if key in result:
            raise RoleConditioningAuditError(
                f"published candidate/failure が重複しています: {key}",
            )
        result[key] = {
            "status": "failure",
            "reason": _required_string(failure, "reason", str(key)),
        }
    expected = {
        (model, job.scenario_id, job.line_id)
        for model in expected_models
        for job in jobs
    }
    if set(result) != expected:
        missing = sorted(expected - set(result))
        extra = sorted(set(result) - expected)
        raise RoleConditioningAuditError(
            "published manifest coverage が 8×161 と一致しません: "
            f"missing={missing[:3]}, extra={extra[:3]}",
        )
    return manifest, result


def _manifest_group_key(value: Mapping[str, Any]) -> tuple[str, str, str]:
    variant = _required_string(value, "variant", "manifest group")
    if variant != "dry":
        raise RoleConditioningAuditError(
            f"published manifest variant は dry 固定です: {variant}",
        )
    return (
        _required_string(value, "model", "manifest group"),
        _required_string(value, "scenario", "manifest group"),
        _required_string(value, "line", "manifest group"),
    )


def _published_conditioning_evidence(
    realized: Mapping[str, Any],
) -> dict[str, Any]:
    keys = (
        "speaker_uuid",
        "speaker_style_name",
        "speaker_style_id",
        "voice_style",
        "voice_selection_source",
        "reference_kind",
        "reference_selection_source",
        "reference_voice",
        "reference_sha256",
        "reference_source_sha256",
        "reference_clip_sha256",
        "reference_control",
        "reference_source_id",
        "reference_provenance",
        "character_identity",
        "role_identity",
        "caption",
        "reference_caption",
        "control",
    )
    return {key: realized[key] for key in keys if key in realized}


def _compare_published_conditioning(
    *,
    model: str,
    source: Mapping[str, Any],
    published: Mapping[str, Any],
) -> dict[str, Any]:
    if published["status"] == "failure":
        return {
            "status": "failure",
            "reason": "published group has no candidate provenance",
        }
    evidence = published["conditioning_evidence"]
    assert isinstance(evidence, Mapping)
    reference = source["reference"]
    if isinstance(reference, Mapping):
        expected_voice = reference["voice"]
        expected_sha = reference["sha256"]
        if (
            expected_voice is not None
            and evidence.get("reference_voice") != expected_voice
        ):
            return {
                "status": "mismatch",
                "reason": "published reference_voice differs from adapter source",
            }
        published_sha = (
            evidence.get("reference_source_sha256")
            if model == "gpt-sovits-v2-pro-plus"
            else (
                evidence.get("reference_sha256")
                or evidence.get("reference_source_sha256")
            )
        )
        if expected_sha is not None and published_sha != expected_sha:
            return {
                "status": "mismatch",
                "reason": "published reference SHA differs from adapter source",
            }
    speaker = source["speaker"]
    if isinstance(speaker, Mapping):
        if evidence.get("speaker_uuid") != speaker["speaker_uuid"]:
            return {
                "status": "mismatch",
                "reason": "published fixed speaker differs from adapter source",
            }
    preset = source["preset"]
    if isinstance(preset, Mapping):
        if evidence.get("voice_style") != preset["voice_style"]:
            return {
                "status": "mismatch",
                "reason": "published preset differs from adapter source",
            }
    if model == "qwen3-tts-12hz-1.7b":
        expected = source["input_identity"]["payload"]["character_identity"]
        if evidence.get("character_identity") != expected:
            return {
                "status": "mismatch",
                "reason": "published Qwen sidecar lacks current character identity",
            }
    if model == "irodori-tts-600m-v3-voicedesign":
        expected = source["input_identity"]["payload"]["role_identity"]
        if evidence.get("role_identity") != expected:
            return {
                "status": "mismatch",
                "reason": "published Irodori sidecar lacks current role identity",
            }
    if (
        model == "voxcpm2"
        and source["prompt"] is not None
    ):
        provenance = evidence.get("reference_provenance")
        if not isinstance(provenance, Mapping):
            return {
                "status": "unverifiable",
                "reason": (
                    "published VoxCPM2 sidecar lacks voice-design identity"
                ),
            }
        identity = provenance.get("identity")
        expected_payload = source["input_identity"]["payload"]
        expected_provenance = expected_payload.get("reference_provenance")
        expected_identity = (
            expected_provenance.get("identity")
            if isinstance(expected_provenance, Mapping)
            else None
        )
        if not isinstance(identity, Mapping) or not isinstance(
            expected_identity,
            Mapping,
        ):
            return {
                "status": "mismatch",
                "reason": "published VoxCPM2 voice-design identity is invalid",
            }
        for field in ("gender", "age", "archetype", "voice", "personality"):
            if identity.get(field) != expected_identity.get(field):
                return {
                    "status": "mismatch",
                    "reason": (
                        "published VoxCPM2 voice-design identity differs "
                        f"for {field}"
                    ),
                }
    return {"status": "match", "reason": "published conditioning evidence matches"}


def _required_string(
    value: Mapping[str, Any],
    key: str,
    target: str,
) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise RoleConditioningAuditError(
            f"{target}.{key} は非空 string が必要です。",
        )
    return result


def _required_sha256(
    value: Mapping[str, Any],
    key: str,
    target: str,
) -> str:
    result = _required_string(value, key, target)
    if len(result) != 64 or any(character not in _HEX_64 for character in result):
        raise RoleConditioningAuditError(
            f"{target}.{key} は lowercase SHA-256 が必要です。",
        )
    return result


def _load_yaml_mapping(path: Path) -> Mapping[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RoleConditioningAuditError(
            f"YAML を読めません: {path}: {error}",
        ) from error
    if not isinstance(value, Mapping):
        raise RoleConditioningAuditError(
            f"YAML root は object が必要です: {path}",
        )
    return value


def _load_json_mapping(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RoleConditioningAuditError(
            f"JSON を読めません: {path}: {error}",
        ) from error
    if not isinstance(value, Mapping):
        raise RoleConditioningAuditError(
            f"JSON root は object が必要です: {path}",
        )
    return value


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="8モデル×161行の役柄 conditioning receipt を監査します。",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = build_role_source_audit(args.repository_root)
    write_role_source_audit(report, args.output)
    if report["problems"]:
        raise RoleConditioningAuditError(
            f"role conditioning audit に {len(report['problems'])} 件の問題があります。",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
