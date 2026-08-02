from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import tempfile
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

from gaya_pipeline.adapters.base import LineJob
from gaya_pipeline.adapters.voice_assignments import CLONE_REFERENCE_ASSIGNMENTS
from gaya_pipeline.completion_anchor import (
    AnchorReviewPlan,
    load_anchor_source_plan,
    resolve_selected_anchor,
    validate_anchor_selection,
)
from gaya_pipeline.completion_plan import (
    IRODORI_MODEL,
    QWEN_MODEL,
    CompletionPlan,
    build_frozen_plan_document,
    load_completion_plan,
)
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.validation import validate_scenarios
from gaya_pipeline.voice_assets import validate_voice_metadata

FORMAT_VERSION = 4
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
    "chatterbox-multilingual-v3": ("pipeline/src/gaya_pipeline/adapters/chatterbox.py"),
    "cosyvoice3-0.5b-2512": "pipeline/src/gaya_pipeline/adapters/cosyvoice3.py",
    "gpt-sovits-v2-pro-plus": ("pipeline/src/gaya_pipeline/adapters/gpt_sovits.py"),
    "irodori-tts-600m-v3-voicedesign": (
        "pipeline/src/gaya_pipeline/adapters/irodori_tts.py"
    ),
    "qwen3-tts-12hz-1.7b": ("pipeline/src/gaya_pipeline/adapters/qwen3_tts.py"),
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


@dataclass(frozen=True)
class _AuditRoleAnchorSelection:
    selection_path: Path
    completion_plan_path: Path
    anchor_source_plan_file: str
    completion_plan_sha256: str
    anchor_source_plan_sha256: str
    selection_sha256: str
    candidate_set_sha256: str
    bindings: Mapping[tuple[str, str, str], Mapping[str, Any]]

    def receipt(self) -> dict[str, Any]:
        return {
            "kind": "deterministic_audit_fixture",
            "protocol": "role-anchor-selection-v1",
            "anchor_source_plan": {
                "file": self.anchor_source_plan_file,
                "sha256": self.anchor_source_plan_sha256,
            },
            "completion_plan": {
                "kind": "audit_only_v2",
                "protocol": "role-baseline-plan-v2",
                "sha256": self.completion_plan_sha256,
            },
            "selection_sha256": self.selection_sha256,
            "candidate_set_sha256": self.candidate_set_sha256,
            "group_count": len(self.bindings),
        }


@dataclass(frozen=True)
class _AuditAnchorTarget:
    model: str
    scenario: str
    character: str
    role_identity_sha256: str
    review_role_epoch_sha256: str

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.model, self.scenario, self.character)


def build_role_source_audit(repository_root: Path) -> dict[str, Any]:
    root = repository_root.resolve()
    scenarios_dir = root / "scenarios"
    voices_dir = root / "assets" / "voices"
    manifest_path = (
        root
        / "docs"
        / "research"
        / "full-baseline-completion"
        / "base-manifest-v4.json"
    )

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
    (
        production_inputs,
        audit_voice_sha256s,
        audit_anchor_selection,
        reading_capabilities,
        runtime_transport_probes,
    ) = _prepare_production_generation_inputs(
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
                expected_selected_anchor=audit_anchor_selection.bindings.get(
                    (model, job.scenario_id, str(job.character["id"])),
                ),
                reading_supported=reading_capabilities[model],
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
                    "unsupported_fields": source_conditioning["unsupported_fields"],
                    "speaker": source_conditioning["speaker"],
                    "preset": source_conditioning["preset"],
                    "reference": source_conditioning["reference"],
                    "prompt": source_conditioning["prompt"],
                    "reading": source_conditioning["reading"],
                    "input_identity": source_conditioning["input_identity"],
                    "published_provenance": published_item,
                    "published_comparison": comparison,
                },
            )

    comparison_counts = {
        status: sum(
            receipt["published_comparison"]["status"] == status for receipt in receipts
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
        "reading_receipt_count": len(receipts),
        "runtime_transport_probe_count": len(runtime_transport_probes),
        "explicit_reading_line_count": sum(
            truth["reading"] is not None for truth in map(_line_truth, jobs)
        ),
        "explicit_reading_receipt_count": sum(
            receipt["reading"]["declared_reading"] is not None for receipt in receipts
        ),
        "explicit_reading_applied_receipt_count": sum(
            receipt["reading"]["status"] == "applied" for receipt in receipts
        ),
        "explicit_reading_unsupported_receipt_count": sum(
            receipt["reading"]["status"] == "unsupported" for receipt in receipts
        ),
        "surface_text_receipt_count": sum(
            receipt["reading"]["status"] == "surface_text" for receipt in receipts
        ),
        "model_required_auto_kana_receipt_count": sum(
            receipt["reading"]["status"] == "model_required_auto_kana"
            for receipt in receipts
        ),
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
        "published_conditioning_unverifiable_count": comparison_counts["unverifiable"],
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
        "audit_role_anchor_selection": audit_anchor_selection.receipt(),
        "published_manifest": {
            "file": "data/manifest.json",
            "sha256": _sha256_file(manifest_path),
            "format_version": manifest["format_version"],
            "candidate_set_sha256": manifest["candidate_set_sha256"],
            "generated_at": manifest["generated_at"],
        },
        "adapter_sources": [adapter_sources[model] for model in MODEL_ADAPTER_FILES],
        "summary": summary,
        "scenarios": scenarios,
        "characters": characters,
        "all_references": all_references,
        "assigned_references": assigned_references,
        "runtime_transport_probes": runtime_transport_probes,
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
    kind = character.get("kind", "human")
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
                "role_exact": (gender_status == "exact" and age_status == "exact"),
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
            item["gender_status"] == "unsupported_neutral" for item in references
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


class _CaptureAuditRuntime:
    def __init__(self) -> None:
        self.generation_calls: list[dict[str, Any]] = []

    def prepare(self, *_args: Any, **_kwargs: Any) -> dict[str, float] | None:
        if _kwargs:
            return {
                "allocated_mib": 0.0,
                "reserved_mib": 0.0,
            }
        return None

    def synthesize(self, *_args: Any, **kwargs: Any) -> Any:
        call = dict(kwargs)
        if "tts_text" in kwargs:
            call["transport"] = "cosyvoice3.runtime.synthesize"
            self.generation_calls.append(call)
            return [{"tts_speech": [[0.0, 0.25, -0.25, 0.0]]}]
        if "exaggeration" in kwargs:
            call["transport"] = "chatterbox.runtime.synthesize"
            self.generation_calls.append(call)
            return [[0.0, 0.25, -0.25, 0.0]]

        output_wav = kwargs.get("output_wav")
        if not isinstance(output_wav, Path):
            raise RoleConditioningAuditError(
                "capture audit runtime にoutput_wavがありません。",
            )
        if "reading" in kwargs:
            call["transport"] = "aivisspeech.runtime.synthesize"
            sample_rate = 48_000
            realized: dict[str, Any] = {"sample_rate_hz": sample_rate}
        elif "voice_style" in kwargs:
            call["transport"] = "supertonic3.runtime.synthesize"
            sample_rate = 44_100
            realized = {"sample_rate_hz": sample_rate}
        elif "reference_wav" in kwargs:
            call["transport"] = "gpt_sovits.runtime.synthesize"
            sample_rate = 32_000
            realized = {
                "sample_rate_hz": sample_rate,
                "prompt_text_mode": "reference-free",
                "phase_peak_vram_mib": {
                    "generation": {
                        "allocated_mib": 0.0,
                        "reserved_mib": 0.0,
                    },
                },
            }
        else:
            raise RoleConditioningAuditError(
                "capture audit runtime のsynthesize transportを判定できません。",
            )
        self.generation_calls.append(call)
        _write_pcm16(output_wav, (0.0, 0.25, -0.25, 0.0), sample_rate)
        return realized

    def concatenate_waveforms(self, waveforms: Sequence[Any]) -> list[list[float]]:
        values: list[float] = []
        for waveform in waveforms:
            current = waveform[0] if len(waveform) == 1 else waveform
            values.extend(float(value) for value in current)
        return [values]

    def write_pcm16(
        self,
        path: Path,
        waveform: Sequence[Any],
        sample_rate: int,
    ) -> None:
        values = waveform[0] if len(waveform) == 1 else waveform
        _write_pcm16(path, values, sample_rate)

    def reset_peak_memory_stats(self) -> None:
        return

    def peak_memory_mib(self) -> dict[str, float]:
        return {"allocated_mib": 0.0, "reserved_mib": 0.0}

    def is_out_of_memory(self, _error: BaseException) -> bool:
        return False


class _QwenAuditRuntime:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._repositories: dict[Path, str] = {}
        self.generation_calls: list[dict[str, Any]] = []

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

    def create_voice_clone_prompt(
        self,
        _model: Any,
        *,
        ref_audio: str,
        ref_text: str,
    ) -> dict[str, str]:
        if not ref_audio or not ref_text:
            raise RoleConditioningAuditError(
                "Qwen audit runtime に不正な clone reference が渡されました。",
            )
        return {"ref_audio": ref_audio, "ref_text": ref_text}

    def generate_voice_clone(
        self,
        _model: Any,
        *,
        text: str,
        language: str,
        voice_clone_prompt: Any,
        sampling: Mapping[str, Any],
    ) -> tuple[list[list[float]], int]:
        if (
            not text
            or language != "Japanese"
            or not isinstance(voice_clone_prompt, Mapping)
            or not sampling
        ):
            raise RoleConditioningAuditError(
                "Qwen audit runtime に不正な clone input が渡されました。",
            )
        self.generation_calls.append(
            {
                "transport": "qwen3_tts.generate_voice_clone",
                "text": text,
                "language": language,
                "voice_clone_prompt": dict(voice_clone_prompt),
                "sampling": dict(sampling),
            },
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
    def __init__(self) -> None:
        self.generation_calls: list[dict[str, Any]] = []

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
        if not text or not caption:
            raise RoleConditioningAuditError(
                "Irodori audit runtime に空の input が渡されました。",
            )
        self.generation_calls.append(
            {
                "transport": "irodori_tts.runtime.synthesize",
                "text": text,
                "caption": caption,
                "reference_wav": reference_wav,
                "output_wav": output_wav,
                "seed": seed,
            },
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
        self.generation_calls: list[dict[str, Any]] = []

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
        if not text:
            raise RoleConditioningAuditError(
                "VoxCPM2 audit runtime に空の input が渡されました。",
            )
        self.generation_calls.append(
            {
                "transport": "voxcpm2.runtime.generate",
                "text": text,
                "reference_wav_path": reference_wav_path,
                "seed": seed,
            },
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


def _build_audit_role_anchor_selection(
    *,
    root: Path,
    output_dir: Path,
) -> _AuditRoleAnchorSelection:
    plan_path = (
        root
        / "docs"
        / "research"
        / "full-baseline-completion"
        / "anchor-source-plan-v1.json"
    )
    for path, label in ((plan_path, "frozen Phase A source plan"),):
        if not path.exists():
            raise RoleConditioningAuditError(f"{label} がありません: {path}")
    plan = load_anchor_source_plan(
        plan_path=plan_path.resolve(),
    )
    anchor_texts, anchor_targets = _audit_anchor_targets(plan)
    candidate_set_sha256 = plan.anchor_candidate_set_sha256
    groups: list[dict[str, Any]] = []
    output_dir.mkdir(parents=True, exist_ok=False)
    audio_dir = output_dir / "audio"
    audio_dir.mkdir()
    for target in anchor_targets:
        role = plan.role(target.scenario, target.character)
        model_revision = plan.models[target.model]
        anchor_id = _canonical_sha256(
            {
                "protocol": "role-conditioning-audit-selected-anchor-v1",
                "plan_sha256": plan.anchor_source_plan_sha256,
                "model": target.model,
                "model_revision": model_revision,
                "scenario": target.scenario,
                "character": target.character,
                "role_identity_sha256": target.role_identity_sha256,
            },
        )
        alternate_id = _canonical_sha256(
            {
                "protocol": "role-conditioning-audit-alternate-anchor-v1",
                "anchor_id": anchor_id,
            },
        )
        audio_path = audio_dir / f"{anchor_id}.wav"
        level = 0.1 + int(anchor_id[:2], 16) / 1_024
        sample_rate = 24_000 if target.model == "qwen3-tts-12hz-1.7b" else 48_000
        _write_pcm16(
            audio_path,
            (0.0, level, -level, 0.0),
            sample_rate,
        )
        audio_sha256 = _sha256_file(audio_path)
        decision = {
            "id": _canonical_sha256(
                {
                    "protocol": "role-conditioning-audit-decision-group-v1",
                    "anchor_id": anchor_id,
                },
            ),
            "model": target.model,
            "scenario": target.scenario,
            "character": target.character,
            "line": None,
            "role_epoch_sha256": target.review_role_epoch_sha256,
            "group_sha256": _canonical_sha256(
                {
                    "protocol": "role-conditioning-audit-review-group-v1",
                    "anchor_id": anchor_id,
                    "alternate_id": alternate_id,
                },
            ),
            "heard_candidate_ids": [anchor_id, alternate_id],
            "selected_candidate_id": anchor_id,
            "no_usable_candidate": False,
            "rubric": {
                "content": "pass",
                "prompt_leakage": "pass",
                "reading": "not_applicable",
                "pitch_accent": "not_applicable",
                "gender": "pass",
                "age": "pass",
                "archetype": "pass",
                "voice_identity": "pass",
                "delivery": "not_applicable",
                "naturalness_quality": 5,
                "notes": "deterministic role-conditioning audit fixture",
            },
            "confirmed": True,
        }
        decision_sha256 = _canonical_sha256(decision)
        role_epoch_sha256 = _canonical_sha256(
            {
                "protocol": "selected-role-epoch-v1",
                "model": target.model,
                "model_revision": model_revision,
                "scenario": target.scenario,
                "character": target.character,
                "role_identity_sha256": target.role_identity_sha256,
                "review_role_epoch_sha256": target.review_role_epoch_sha256,
                "anchor_id": anchor_id,
                "audio_sha256": audio_sha256,
                "decision_sha256": decision_sha256,
            },
        )
        anchor_text = anchor_texts[target.model]
        groups.append(
            {
                "model": target.model,
                "model_revision": model_revision,
                "scenario": target.scenario,
                "character": target.character,
                "role_identity": {
                    "scenario": role.scenario,
                    "character": role.character,
                    "role": dict(role.role),
                    "reference_voice": role.reference_voice,
                    "scene_setting": role.scene_setting,
                },
                "role_identity_sha256": role.role_identity_sha256,
                "review_role_epoch_sha256": target.review_role_epoch_sha256,
                "role_epoch_sha256": role_epoch_sha256,
                "anchor_id": anchor_id,
                "attempt": 1,
                "seed": int(anchor_id[:8], 16),
                "audio_path": f"audio/{anchor_id}.wav",
                "audio_sha256": audio_sha256,
                "anchor_text": anchor_text,
                "anchor_text_sha256": hashlib.sha256(
                    anchor_text.encode("utf-8"),
                ).hexdigest(),
                "decision": decision,
                "decision_sha256": decision_sha256,
            },
        )
    selection = validate_anchor_selection(
        {
            "format_version": 1,
            "protocol": "role-anchor-selection-v1",
            "plan_sha256": plan.anchor_source_plan_sha256,
            "candidate_set_sha256": candidate_set_sha256,
            "groups": groups,
        },
    )
    selection_path = (output_dir / "role-anchor-selection-v1.json").resolve()
    raw = canonical_json(selection).encode("utf-8")
    selection_path.write_bytes(raw)
    selection_sha256 = hashlib.sha256(raw).hexdigest()
    selection_path.with_suffix(".sha256").write_bytes(
        f"{selection_sha256}\n".encode("ascii"),
    )
    completion_plan_path, completion_plan = _build_audit_completion_plan(
        root=root,
        output_dir=output_dir,
        anchor_selection_sha256=selection_sha256,
    )
    if (
        completion_plan.anchor_source_plan_sha256 != plan.anchor_source_plan_sha256
        or completion_plan.anchor_candidate_set_sha256 != candidate_set_sha256
        or completion_plan.anchor_selection_sha256 != selection_sha256
    ):
        raise RoleConditioningAuditError(
            "audit-only v2 plan のanchor authorityがPhase A fixtureと一致しません。",
        )
    bindings: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for target in anchor_targets:
        role = plan.role(target.scenario, target.character)
        selected = resolve_selected_anchor(
            selection_path=selection_path,
            plan_sha256=plan.anchor_source_plan_sha256,
            model=target.model,
            model_revision=plan.models[target.model],
            role=role,
        )
        bindings[target.identity] = selected.receipt()
    if len(bindings) != 106:
        raise RoleConditioningAuditError(
            "audit role anchor selection は 2 models × 53 roles が必要です。",
        )
    return _AuditRoleAnchorSelection(
        selection_path=selection_path,
        completion_plan_path=completion_plan_path,
        anchor_source_plan_file=plan_path.relative_to(root).as_posix(),
        completion_plan_sha256=completion_plan.plan_id,
        anchor_source_plan_sha256=plan.anchor_source_plan_sha256,
        selection_sha256=selection_sha256,
        candidate_set_sha256=candidate_set_sha256,
        bindings=bindings,
    )


def _build_audit_completion_plan(
    *,
    root: Path,
    output_dir: Path,
    anchor_selection_sha256: str,
) -> tuple[Path, CompletionPlan]:
    base_manifest_path = (
        root
        / "docs"
        / "research"
        / "full-baseline-completion"
        / "base-manifest-v4.json"
    ).resolve()
    scenarios_dir = (root / "scenarios").resolve()
    voices_dir = (root / "assets" / "voices").resolve()
    for path, label in (
        (base_manifest_path, "base manifest"),
        (scenarios_dir, "scenarios directory"),
        (voices_dir, "voices directory"),
    ):
        if not path.exists():
            raise RoleConditioningAuditError(f"{label} がありません: {path}")
    document = build_frozen_plan_document(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        anchor_selection_sha256=anchor_selection_sha256,
    )
    plan_path = (output_dir / "audit-completion-plan-v2.json").resolve()
    plan_path.write_bytes(canonical_json(document).encode("utf-8"))
    plan = load_completion_plan(
        plan_path,
        base_manifest_path=base_manifest_path,
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
    )
    return plan_path, plan


def _audit_anchor_targets(
    plan: AnchorReviewPlan,
) -> tuple[dict[str, str], tuple[_AuditAnchorTarget, ...]]:
    from gaya_pipeline.adapters.irodori_tts import ROLE_ANCHOR_TEXT
    from gaya_pipeline.adapters.qwen3_tts import REFERENCE_TEXT

    anchor_texts = {
        IRODORI_MODEL: ROLE_ANCHOR_TEXT,
        QWEN_MODEL: REFERENCE_TEXT,
    }
    targets: list[_AuditAnchorTarget] = []
    for model, anchor_text in sorted(anchor_texts.items()):
        model_revision = plan.models.get(model)
        if model_revision is None:
            raise RoleConditioningAuditError(
                f"completion plan にanchor modelがありません: {model}",
            )
        anchor_text_sha256 = hashlib.sha256(
            anchor_text.encode("utf-8"),
        ).hexdigest()
        for role in plan.roles:
            if role.reference_voice is not None:
                continue
            review_role_epoch_sha256 = _canonical_sha256(
                {
                    "model": model,
                    "model_revision": model_revision,
                    "scenario": role.scenario,
                    "character": role.character,
                    "role_identity_sha256": role.role_identity_sha256,
                    "anchor_text_sha256": anchor_text_sha256,
                },
            )
            targets.append(
                _AuditAnchorTarget(
                    model=model,
                    scenario=role.scenario,
                    character=role.character,
                    role_identity_sha256=role.role_identity_sha256,
                    review_role_epoch_sha256=review_role_epoch_sha256,
                ),
            )
    targets.sort(key=lambda target: target.identity)
    counts = {
        model: sum(target.model == model for target in targets)
        for model in anchor_texts
    }
    if counts != {IRODORI_MODEL: 53, QWEN_MODEL: 53}:
        raise RoleConditioningAuditError(
            "audit role anchor target は2 models × 53 no-ref rolesが必要です。",
        )
    return anchor_texts, tuple(targets)


def _prepare_production_generation_inputs(
    *,
    root: Path,
    jobs: Sequence[LineJob],
) -> tuple[
    dict[tuple[str, str, str], Mapping[str, Any]],
    dict[str, str],
    _AuditRoleAnchorSelection,
    dict[str, bool],
    list[dict[str, Any]],
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
    reading_capabilities: dict[str, bool] = {}
    runtime_transport_probes: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="gaya-role-audit-") as raw_temp:
        temporary_root = Path(raw_temp)
        voices_dir = temporary_root / "voices"
        audit_voice_sha256s = _build_audit_voice_kit(
            root / "assets" / "voices",
            voices_dir,
        )
        audit_anchor_selection = _build_audit_role_anchor_selection(
            root=root,
            output_dir=temporary_root / "role-anchor-selection",
        )
        supertonic_root = temporary_root / "supertonic-model"
        supertonic_root.mkdir()
        vox_root = temporary_root / "voxcpm-model"
        vox_root.mkdir()
        aivis_runtime = _CaptureAuditRuntime()
        chatter_runtime = _CaptureAuditRuntime()
        cosy_runtime = _CaptureAuditRuntime()
        gpt_runtime = _CaptureAuditRuntime()
        irodori_runtime = _IrodoriAuditRuntime()
        qwen_runtime = _QwenAuditRuntime(temporary_root)
        supertonic_runtime = _CaptureAuditRuntime()
        vox_runtime = _VoxAuditRuntime()
        runtimes: dict[str, Any] = {
            "aivisspeech-kohaku": aivis_runtime,
            "chatterbox-multilingual-v3": chatter_runtime,
            "cosyvoice3-0.5b-2512": cosy_runtime,
            "gpt-sovits-v2-pro-plus": gpt_runtime,
            "irodori-tts-600m-v3-voicedesign": irodori_runtime,
            "qwen3-tts-12hz-1.7b": qwen_runtime,
            "supertonic-3": supertonic_runtime,
            "voxcpm2": vox_runtime,
        }
        adapters: dict[str, Any] = {
            "aivisspeech-kohaku": aivisspeech.AivisSpeechAdapter(
                runtime=aivis_runtime,
            ),
            "chatterbox-multilingual-v3": chatterbox.ChatterboxAdapter(
                runtime=chatter_runtime,
                model_root=temporary_root,
            ),
            "cosyvoice3-0.5b-2512": cosyvoice3.CosyVoice3Adapter(
                runtime=cosy_runtime,
            ),
            "gpt-sovits-v2-pro-plus": gpt_sovits.GPTSoVITSAdapter(
                runtime=gpt_runtime,
                upstream_root=temporary_root,
            ),
            "irodori-tts-600m-v3-voicedesign": (
                irodori_tts.IrodoriTTSAdapter(
                    runtime=irodori_runtime,
                    role_anchor_selection_path=(audit_anchor_selection.selection_path),
                    role_anchor_plan_sha256=(
                        audit_anchor_selection.anchor_source_plan_sha256
                    ),
                )
            ),
            "qwen3-tts-12hz-1.7b": qwen3_tts.Qwen3TTSAdapter(
                runtime=qwen_runtime,
                role_anchor_selection_path=(audit_anchor_selection.selection_path),
                role_anchor_plan_sha256=(
                    audit_anchor_selection.anchor_source_plan_sha256
                ),
            ),
            "supertonic-3": supertonic3.Supertonic3Adapter(
                runtime=supertonic_runtime,
            ),
            "voxcpm2": voxcpm2.VoxCPM2Adapter(
                runtime=vox_runtime,
                model_root=vox_root,
            ),
        }
        for model, adapter in adapters.items():
            if adapter.profile.id != model:
                raise RoleConditioningAuditError(
                    f"adapter profile ID が model key と一致しません: {model}",
                )
            reading_capabilities[model] = adapter.profile.capabilities.reading
            artifacts_dir = temporary_root / "artifacts" / model
            try:
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
            except Exception as error:
                raise RoleConditioningAuditError(
                    f"{model} production prepare に失敗しました: {error}",
                ) from error
            context = adapter.take_recipe().single_take_context()
            for job in jobs:
                value = adapter.generation_input(job, context)
                if not isinstance(value, Mapping):
                    raise RoleConditioningAuditError(
                        f"{model}.generation_input() が object ではありません。",
                    )
                result[(model, job.scenario_id, job.line_id)] = dict(value)
            if model == "chatterbox-multilingual-v3":
                adapter._model = {"audit": model}
                adapter._runtime_load_peak = {
                    "allocated_mib": 0.0,
                    "reserved_mib": 0.0,
                }
            elif model == "cosyvoice3-0.5b-2512":
                adapter._model = {"audit": model}
                adapter._runtime_load_peak = {
                    "allocated_mib": 0.0,
                    "reserved_mib": 0.0,
                }
                adapter._model_identity = {
                    "speech_tokenizer_providers": [],
                    "campplus_providers": [],
                }

            runtime = runtimes[model]
            for job in jobs:
                calls_before = len(runtime.generation_calls)
                output_wav = (
                    temporary_root
                    / "runtime-probes"
                    / model
                    / job.scenario_id
                    / f"{job.line_id}.wav"
                )
                try:
                    adapter.generate(job, context, output_wav)
                except Exception as error:
                    raise RoleConditioningAuditError(
                        f"{model} runtime transport probe "
                        f"({job.scenario_id}/{job.line_id}) に失敗しました: "
                        f"{error}",
                    ) from error
                new_calls = runtime.generation_calls[calls_before:]
                if len(new_calls) != 1:
                    raise RoleConditioningAuditError(
                        f"{model} runtime transport probe "
                        f"({job.scenario_id}/{job.line_id}) は1回の生成呼び出しが"
                        f"必要です: actual={len(new_calls)}",
                    )
                runtime_transport_probes.append(
                    _runtime_transport_probe_receipt(
                        model=model,
                        job=job,
                        generation_input=result[(model, job.scenario_id, job.line_id)],
                        runtime_call=new_calls[0],
                    ),
                )
    expected_count = len(MODEL_ADAPTER_FILES) * len(jobs)
    if len(result) != expected_count:
        raise RoleConditioningAuditError(
            "production generation_input receipt coverage が不足しています。",
        )
    if len(runtime_transport_probes) != expected_count:
        raise RoleConditioningAuditError(
            "runtime transport probe は8 models × 161 linesすべてが必要です。",
        )
    return (
        result,
        audit_voice_sha256s,
        audit_anchor_selection,
        reading_capabilities,
        runtime_transport_probes,
    )


def _runtime_transport_probe_receipt(
    *,
    model: str,
    job: LineJob,
    generation_input: Mapping[str, Any],
    runtime_call: Mapping[str, Any],
) -> dict[str, Any]:
    contracts = {
        "aivisspeech-kohaku": (
            "text",
            "text",
            "aivisspeech.runtime.synthesize",
            {
                "text",
                "reading",
                "speaker_id",
                "intonation_scale",
                "tempo_dynamics_scale",
                "output_wav",
                "transport",
            },
        ),
        "chatterbox-multilingual-v3": (
            "text",
            "text",
            "chatterbox.runtime.synthesize",
            {
                "text",
                "reference_wav",
                "exaggeration",
                "seed",
                "transport",
            },
        ),
        "cosyvoice3-0.5b-2512": (
            "tts_text",
            "tts_text",
            "cosyvoice3.runtime.synthesize",
            {
                "tts_text",
                "instruction",
                "reference_wav",
                "seed",
                "transport",
            },
        ),
        "gpt-sovits-v2-pro-plus": (
            "text",
            "text",
            "gpt_sovits.runtime.synthesize",
            {
                "text",
                "reference_wav",
                "output_wav",
                "seed",
                "transport",
            },
        ),
        "irodori-tts-600m-v3-voicedesign": (
            "text",
            "text",
            "irodori_tts.runtime.synthesize",
            {
                "text",
                "caption",
                "reference_wav",
                "output_wav",
                "seed",
                "transport",
            },
        ),
        "qwen3-tts-12hz-1.7b": (
            "text",
            "text",
            "qwen3_tts.generate_voice_clone",
            {
                "text",
                "language",
                "voice_clone_prompt",
                "sampling",
                "transport",
            },
        ),
        "supertonic-3": (
            "tts_text",
            "text",
            "supertonic3.runtime.synthesize",
            {
                "text",
                "voice_style",
                "output_wav",
                "seed",
                "transport",
            },
        ),
        "voxcpm2": (
            "model_text",
            "text",
            "voxcpm2.runtime.generate",
            {
                "text",
                "reference_wav_path",
                "seed",
                "transport",
            },
        ),
    }
    try:
        generation_field, runtime_field, expected_transport, expected_keys = contracts[
            model
        ]
    except KeyError as error:
        raise RoleConditioningAuditError(
            f"未対応のruntime transport probe modelです: {model}",
        ) from error

    surface_text = _required_string(job.line, "text", f"{model}.probe")
    declared_value = job.line.get("reading")
    if declared_value is None:
        declared_reading: str | None = None
    elif isinstance(declared_value, str) and declared_value.strip():
        declared_reading = declared_value
    else:
        raise RoleConditioningAuditError(
            f"{model}.probe.reading はnon-empty stringまたはnullが必要です。",
        )
    generation_text = _required_string(
        generation_input,
        generation_field,
        f"{model}.generation_input",
    )
    runtime_text = _required_string(
        runtime_call,
        runtime_field,
        f"{model}.runtime_call",
    )
    transport = _required_string(
        runtime_call,
        "transport",
        f"{model}.runtime_call",
    )
    if set(runtime_call) != expected_keys:
        raise RoleConditioningAuditError(
            f"{model} runtime transport の引数がexact contractと一致しません: "
            f"actual={sorted(runtime_call)}, expected={sorted(expected_keys)}",
        )
    if transport != expected_transport:
        raise RoleConditioningAuditError(
            f"{model} runtime transport が一致しません: "
            f"actual={transport}, expected={expected_transport}",
        )
    if runtime_text != generation_text:
        raise RoleConditioningAuditError(
            f"{model} runtime transport がgeneration_inputと一致しません: "
            f"actual={runtime_text!r}, expected={generation_text!r}",
        )

    runtime_context: dict[str, Any] = {}
    runtime_reading = runtime_call.get("reading")
    if model == "aivisspeech-kohaku":
        reading_policy = (
            "separate_reading"
            if declared_reading is not None
            else "engine_g2p_from_surface"
        )
        if generation_text != surface_text or runtime_reading != declared_reading:
            raise RoleConditioningAuditError(
                f"{model} runtime transport がsurface textとreadingを"
                "分離していません。",
            )
        style = generation_input.get("speaker_style")
        if not isinstance(style, Mapping):
            raise RoleConditioningAuditError(
                f"{model}.generation_input.speaker_styleが不正です。",
            )
        runtime_context = {
            "speaker_id": runtime_call["speaker_id"],
            "intonation_scale": runtime_call["intonation_scale"],
            "tempo_dynamics_scale": runtime_call["tempo_dynamics_scale"],
        }
        expected_context = {
            "speaker_id": style.get("id"),
            "intonation_scale": generation_input.get("intonation_scale"),
            "tempo_dynamics_scale": generation_input.get(
                "tempo_dynamics_scale",
            ),
        }
        if runtime_context != expected_context:
            raise RoleConditioningAuditError(
                f"{model} runtime controlがgeneration_inputと一致しません。",
            )
    elif model == "cosyvoice3-0.5b-2512":
        reading_policy = (
            "reading_as_tts_text"
            if declared_reading is not None
            else "auto_kana_as_tts_text"
        )
        if "reading" in runtime_call:
            raise RoleConditioningAuditError(
                f"{model} runtime transport に未知のreading fieldがあります。",
            )
        if declared_reading is not None and generation_text != declared_reading:
            raise RoleConditioningAuditError(
                f"{model} runtime transport が明示readingをtts_textへ渡していません。",
            )
        runtime_context = {
            "instruction": _required_string(
                runtime_call,
                "instruction",
                f"{model}.runtime_call",
            ),
        }
        if runtime_context["instruction"] != _required_string(
            generation_input,
            "instruction",
            f"{model}.generation_input",
        ):
            raise RoleConditioningAuditError(
                f"{model} runtime instructionがgeneration_inputと一致しません。",
            )
    else:
        if "reading" in runtime_call:
            raise RoleConditioningAuditError(
                f"{model} runtime transport に未対応reading fieldが混入しています。",
            )
        reading_policy = "surface_derived_no_external_reading"
        if model == "chatterbox-multilingual-v3":
            runtime_context = {"exaggeration": runtime_call["exaggeration"]}
            if runtime_context["exaggeration"] != generation_input.get(
                "exaggeration",
            ):
                raise RoleConditioningAuditError(
                    f"{model} runtime exaggerationがgeneration_inputと一致しません。",
                )
        elif model == "irodori-tts-600m-v3-voicedesign":
            runtime_context = {
                "caption": _required_string(
                    runtime_call,
                    "caption",
                    f"{model}.runtime_call",
                ),
            }
            if runtime_context["caption"] != _required_string(
                generation_input,
                "caption",
                f"{model}.generation_input",
            ):
                raise RoleConditioningAuditError(
                    f"{model} runtime captionがgeneration_inputと一致しません。",
                )
        elif model == "qwen3-tts-12hz-1.7b":
            prompt = runtime_call.get("voice_clone_prompt")
            if not isinstance(prompt, Mapping) or set(prompt) != {
                "ref_audio",
                "ref_text",
            }:
                raise RoleConditioningAuditError(
                    f"{model} runtime clone promptが不正です。",
                )
            runtime_context = {
                "language": _required_string(
                    runtime_call,
                    "language",
                    f"{model}.runtime_call",
                ),
                "reference_text": _required_string(
                    prompt,
                    "ref_text",
                    f"{model}.runtime_call.voice_clone_prompt",
                ),
            }
            expected_context = {
                "language": _required_string(
                    generation_input,
                    "language",
                    f"{model}.generation_input",
                ),
                "reference_text": _required_string(
                    generation_input,
                    "reference_text",
                    f"{model}.generation_input",
                ),
            }
            if runtime_context != expected_context:
                raise RoleConditioningAuditError(
                    f"{model} runtime clone promptがgeneration_inputと一致しません。",
                )
        elif model == "supertonic-3":
            runtime_context = {
                "voice_style": _required_string(
                    runtime_call,
                    "voice_style",
                    f"{model}.runtime_call",
                ),
            }
            if runtime_context["voice_style"] != _required_string(
                generation_input,
                "voice_style",
                f"{model}.generation_input",
            ):
                raise RoleConditioningAuditError(
                    f"{model} runtime voice styleがgeneration_inputと一致しません。",
                )
        if declared_reading is not None and runtime_text == declared_reading:
            raise RoleConditioningAuditError(
                f"{model} runtime transport が未対応readingでsurface textを"
                "置換しています。",
            )

    return {
        "model": model,
        "scenario": job.scenario_id,
        "line": job.line_id,
        "transport": transport,
        "generation_input_field": generation_field,
        "runtime_field": runtime_field,
        "reading_policy": reading_policy,
        "source_text": surface_text,
        "declared_reading": declared_reading,
        "generation_input_text": generation_text,
        "runtime_text": runtime_text,
        "runtime_reading": runtime_reading,
        "runtime_context": runtime_context,
        "status": "match",
    }


def _source_conditioning_receipt(
    *,
    model: str,
    job: LineJob,
    truth: Mapping[str, Any],
    voices: Mapping[str, Mapping[str, Any]],
    production_input: Mapping[str, Any],
    audit_voice_sha256s: Mapping[str, str],
    expected_selected_anchor: Mapping[str, Any] | None,
    reading_supported: bool,
) -> dict[str, Any]:
    unsupported = {field: "unsupported" for field in CONDITIONING_FIELDS}
    speaker: dict[str, Any] | None = None
    preset: dict[str, Any] | None = None
    reference: dict[str, Any] | None = None
    prompt: dict[str, Any] | None = None
    payload = dict(production_input)
    reading = _reading_receipt(
        model=model,
        truth=truth,
        payload=payload,
        reading_supported=reading_supported,
    )

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
            if "selected_anchor" in payload:
                raise RoleConditioningAuditError(
                    "Irodori explicit reference に selected_anchor が混入しています。",
                )
        else:
            selected_anchor = _validated_selected_anchor(
                model=model,
                payload=payload,
                expected=expected_selected_anchor,
                reference_sha256=reference_sha256,
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
            "selected_anchor": (
                None if declared_reference is not None else selected_anchor
            ),
        }
        prompt = _prompt_receipt(
            text=caption,
            fields=CONDITIONING_FIELDS,
            kind="target_caption",
        )
        field_transport = {field: "target_caption" for field in CONDITIONING_FIELDS}
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
                source_id if truth["declared_reference_voice"] is not None else None
            ),
            "sha256": None,
            "source_sha256": None,
            "prepare_state_sha256": reference_sha256,
            "audit_fixture_source_sha256": None,
        }
        if truth["declared_reference_voice"] is None:
            selected_anchor = _validated_selected_anchor(
                model=model,
                payload=payload,
                expected=expected_selected_anchor,
                reference_sha256=reference_sha256,
            )
            if source_id != selected_anchor["anchor_id"]:
                raise RoleConditioningAuditError(
                    "Qwen production generation_input の reference_source_id が"
                    " selected anchor と一致しません。",
                )
            reference["selected_anchor"] = selected_anchor
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
            if "selected_anchor" in payload:
                raise RoleConditioningAuditError(
                    "Qwen explicit reference に selected_anchor が混入しています。",
                )
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
        "reading": reading,
        "input_identity": {
            "sha256": _sha256_json(payload),
            "payload": payload,
        },
    }


def _validated_selected_anchor(
    *,
    model: str,
    payload: Mapping[str, Any],
    expected: Mapping[str, Any] | None,
    reference_sha256: str,
) -> dict[str, Any]:
    actual = payload.get("selected_anchor")
    if not isinstance(actual, Mapping):
        raise RoleConditioningAuditError(
            f"{model} production generation_input に selected_anchor receipt"
            " がありません。",
        )
    if expected is None:
        raise RoleConditioningAuditError(
            f"{model} に対応する audit selected anchor binding がありません。",
        )
    expected_keys = {
        "anchor_selection_sha256",
        "anchor_plan_sha256",
        "anchor_candidate_set_sha256",
        "anchor_id",
        "anchor_attempt",
        "anchor_seed",
        "anchor_audio_sha256",
        "anchor_text_sha256",
        "anchor_decision_sha256",
        "role_identity_sha256",
        "role_epoch_sha256",
    }
    if set(actual) != expected_keys:
        raise RoleConditioningAuditError(
            f"{model} selected_anchor receipt fields が正式 resolver と一致しません。",
        )
    for key in expected_keys:
        if actual.get(key) != expected.get(key):
            raise RoleConditioningAuditError(
                f"{model} selected_anchor receipt が正式 resolver と"
                f"一致しません: {key}",
            )
    if actual["anchor_audio_sha256"] != reference_sha256:
        raise RoleConditioningAuditError(
            f"{model} selected_anchor audio SHA が reference SHA と一致しません。",
        )
    return dict(actual)


def _reading_receipt(
    *,
    model: str,
    truth: Mapping[str, Any],
    payload: Mapping[str, Any],
    reading_supported: bool,
) -> dict[str, Any]:
    surface = _required_string(truth, "text", f"{model}.role_truth")
    declared = truth.get("reading")
    if declared is not None and (not isinstance(declared, str) or not declared.strip()):
        raise RoleConditioningAuditError(
            f"{model} の line.reading が不正です。",
        )

    expected_capability = model in {
        "aivisspeech-kohaku",
        "cosyvoice3-0.5b-2512",
    }
    if reading_supported is not expected_capability:
        raise RoleConditioningAuditError(
            f"{model} の reading capability が公式 input contract と一致しません。",
        )

    reading_field: str | None = None
    reading_input: str | None = None
    if model == "aivisspeech-kohaku":
        model_text_field = "text"
        model_text = _required_string(payload, model_text_field, model)
        _require_reading_value(model, model_text_field, model_text, surface)
        _require_reading_value(
            model,
            "reading_source",
            _required_string(payload, "reading_source", model),
            "line.reading" if declared is not None else "line.text",
        )
        surface_transport = "audio_query.text"
        if declared is not None:
            reading_field = "reading"
            reading_input = _required_string(payload, reading_field, model)
            _require_reading_value(model, reading_field, reading_input, declared)
            _require_reading_value(
                model,
                "reading_control",
                _required_string(payload, "reading_control", model),
                "accent_phrases",
            )
            status = "applied"
            reading_transport = "accent_phrases"
        else:
            if "reading" in payload or "reading_control" in payload:
                raise RoleConditioningAuditError(
                    "AivisSpeech の未指定行に reading control が混入しています。",
                )
            status = "surface_text"
            reading_transport = "engine_g2p_from_surface"
    elif model == "cosyvoice3-0.5b-2512":
        _require_reading_value(
            model,
            "source_text",
            _required_string(payload, "source_text", model),
            surface,
        )
        model_text_field = "tts_text"
        model_text = _required_string(payload, model_text_field, model)
        reading_field = model_text_field
        reading_input = model_text
        surface_transport = "source_text"
        source = _required_string(payload, "reading_source", model)
        if declared is not None:
            _require_reading_value(model, model_text_field, model_text, declared)
            _require_reading_value(model, "reading_source", source, "line.reading")
            status = "applied"
            reading_transport = "line.reading_to_tts_text"
        else:
            _require_reading_value(
                model,
                "reading_source",
                source,
                "pyopenjtalk.g2p(kana=True)",
            )
            status = "model_required_auto_kana"
            reading_transport = "pyopenjtalk_to_tts_text"
    else:
        if reading_supported:
            raise RoleConditioningAuditError(
                f"{model} は外部 reading input を持ちません。",
            )
        if "reading" in payload or "reading_control" in payload:
            raise RoleConditioningAuditError(
                f"{model} に未対応 reading control が混入しています。",
            )
        reading_source = payload.get("reading_source")
        if reading_source is not None:
            _require_reading_value(
                model,
                "reading_source",
                reading_source,
                "line.text",
            )

        if model in {
            "chatterbox-multilingual-v3",
            "gpt-sovits-v2-pro-plus",
            "qwen3-tts-12hz-1.7b",
        }:
            model_text_field = "text"
            model_text = _required_string(payload, model_text_field, model)
            _require_reading_value(model, model_text_field, model_text, surface)
            surface_transport = model_text_field
        elif model == "irodori-tts-600m-v3-voicedesign":
            emoji = payload.get("emotion_emoji")
            if emoji is not None and (not isinstance(emoji, str) or not emoji):
                raise RoleConditioningAuditError(
                    "Irodori emotion_emoji は string または null が必要です。",
                )
            model_text_field = "text"
            model_text = _required_string(payload, model_text_field, model)
            _require_reading_value(
                model,
                model_text_field,
                model_text,
                f"{emoji or ''}{surface}",
            )
            surface_transport = (
                "emotion_emoji_prefixed_text" if emoji is not None else "text"
            )
        elif model == "supertonic-3":
            _require_reading_value(
                model,
                "source_text",
                _required_string(payload, "source_text", model),
                surface,
            )
            model_text_field = "tts_text"
            model_text = _required_string(payload, model_text_field, model)
            _require_reading_value(model, model_text_field, model_text, surface)
            surface_transport = "source_text_and_tts_text"
        elif model == "voxcpm2":
            _require_reading_value(
                model,
                "source_text",
                _required_string(payload, "source_text", model),
                surface,
            )
            _require_reading_value(
                model,
                "text",
                _required_string(payload, "text", model),
                surface,
            )
            control = _required_string(payload, "control", model)
            model_text_field = "model_text"
            model_text = _required_string(payload, model_text_field, model)
            _require_reading_value(
                model,
                model_text_field,
                model_text,
                f"({control}){surface}",
            )
            surface_transport = "control_prefixed_model_text"
        else:
            raise RoleConditioningAuditError(f"未対応 model です: {model}")

        status = "unsupported" if declared is not None else "surface_text"
        reading_transport = (
            "unsupported"
            if declared is not None
            else "model_text_frontend_from_surface"
        )

    return {
        "surface_text": surface,
        "declared_reading": declared,
        "capability_reading": reading_supported,
        "model_text_field": model_text_field,
        "model_text": model_text,
        "surface_transport": surface_transport,
        "reading_field": reading_field,
        "reading_input": reading_input,
        "reading_transport": reading_transport,
        "status": status,
    }


def _require_reading_value(
    model: str,
    field: str,
    actual: Any,
    expected: Any,
) -> None:
    if actual != expected:
        raise RoleConditioningAuditError(
            f"{model} の reading contract が不正です: "
            f"{field}={actual!r}, expected={expected!r}",
        )


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
    if (
        isinstance(speaker, Mapping)
        and evidence.get("speaker_uuid") != speaker["speaker_uuid"]
    ):
        return {
            "status": "mismatch",
            "reason": "published fixed speaker differs from adapter source",
        }
    preset = source["preset"]
    if (
        isinstance(preset, Mapping)
        and evidence.get("voice_style") != preset["voice_style"]
    ):
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
    if model == "voxcpm2" and source["prompt"] is not None:
        provenance = evidence.get("reference_provenance")
        if not isinstance(provenance, Mapping):
            return {
                "status": "unverifiable",
                "reason": ("published VoxCPM2 sidecar lacks voice-design identity"),
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
                        f"published VoxCPM2 voice-design identity differs for {field}"
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


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


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
