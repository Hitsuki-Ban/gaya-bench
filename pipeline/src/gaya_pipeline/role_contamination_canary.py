from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gaya_pipeline.adapters import create_adapter
from gaya_pipeline.adapters.base import LineJob, TakeContext, TakeRecipe
from gaya_pipeline.generation import _load_jobs
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.validation import validate_scenarios

FORMAT_VERSION = 2
SESSION_REPORT_KIND = "role-contamination-session"
MATRIX_REPORT_KIND = "role-contamination-clean-process-matrix"
DEFAULT_SEED = 177

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SESSION_LABELS = (
    "isolated-a-1",
    "isolated-a-2",
    "isolated-b-1",
    "isolated-b-2",
    "forward",
    "reverse",
    "aba",
    "bab",
)
_VERDICT_RANK = {"pass": 0, "review": 1, "fail": 2}


class RoleContaminationCanaryError(RuntimeError):
    pass


def run_role_contamination_canary(
    *,
    model_id: str,
    scenarios_dir: Path,
    voices_dir: Path,
    run_root: Path,
    targets: Sequence[str],
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    target_a, target_b = _validate_matrix_targets(targets)
    if run_root.exists():
        raise RoleContaminationCanaryError(
            f"run root は新規 path が必要です: {run_root}",
        )
    _validate_scenario_directory(scenarios_dir)
    _load_target_jobs(scenarios_dir, (target_a, target_b))

    run_root.mkdir(parents=True)
    specs_dir = run_root / "session-specs"
    specs_dir.mkdir()
    session_reports: list[dict[str, Any]] = []
    for label, sequence in _matrix_sequences(target_a, target_b):
        session_id = f"{label}-{uuid.uuid4()}"
        session_root = run_root / "sessions" / session_id
        spec = {
            "format_version": FORMAT_VERSION,
            "session_id": session_id,
            "session_token": str(uuid.uuid4()),
            "label": label,
            "model_id": model_id,
            "scenarios_dir": str(scenarios_dir.resolve()),
            "voices_dir": str(voices_dir.resolve()),
            "session_root": str(session_root.resolve()),
            "sequence": list(sequence),
            "seed": seed,
        }
        spec_path = specs_dir / f"{label}.json"
        _write_json_new(spec_path, spec)
        session_reports.append(_launch_session_process(spec_path))

    report = aggregate_role_contamination_reports(session_reports)
    _write_json_new(run_root / "report.json", report)
    return report


def aggregate_role_contamination_reports(
    session_reports: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not session_reports:
        raise RoleContaminationCanaryError(
            "session report が1件以上必要です。",
        )
    reports = [dict(report) for report in session_reports]
    for report in reports:
        if report.get("format_version") == 1:
            raise RoleContaminationCanaryError(
                "format_version=1 は単一 process の旧 report であり、"
                "clean-process matrix に集約できません。matrix を再実行してください。",
            )
        if report.get("format_version") != FORMAT_VERSION:
            raise RoleContaminationCanaryError(
                f"未対応の session format_version です: "
                f"{report.get('format_version')}",
            )
        if report.get("report_kind") != SESSION_REPORT_KIND:
            raise RoleContaminationCanaryError(
                f"session report_kind が不正です: {report.get('report_kind')}",
            )

    by_label: dict[str, dict[str, Any]] = {}
    session_ids: set[str] = set()
    session_tokens: set[str] = set()
    process_instances: set[str] = set()
    process_ids: set[int] = set()
    for report in reports:
        label = _required_string(report, "label", "session report")
        if label in by_label:
            raise RoleContaminationCanaryError(
                f"session label が重複しています: {label}",
            )
        by_label[label] = report
        _add_unique_session_value(
            session_ids,
            _required_string(report, "session_id", label),
            "session_id",
        )
        _add_unique_session_value(
            session_tokens,
            _required_string(report, "session_token", label),
            "session_token",
        )
        _add_unique_session_value(
            process_instances,
            _required_string(report, "process_instance_id", label),
            "process_instance_id",
        )
        if report.get("prepare_invocation_count") != 1:
            raise RoleContaminationCanaryError(
                f"{label} は prepare を1回だけ実行する必要があります。",
            )
        process_id = report.get("process_id")
        parent_process_id = report.get("parent_process_id")
        if (
            isinstance(process_id, bool)
            or not isinstance(process_id, int)
            or process_id <= 0
            or isinstance(parent_process_id, bool)
            or not isinstance(parent_process_id, int)
            or parent_process_id <= 0
            or process_id == parent_process_id
        ):
            raise RoleContaminationCanaryError(
                f"{label} の process 証跡が不正です。",
            )
        if process_id in process_ids:
            raise RoleContaminationCanaryError(
                f"process_id が session 間で重複しています: {process_id}",
            )
        process_ids.add(process_id)

    if set(by_label) != set(_SESSION_LABELS):
        raise RoleContaminationCanaryError(
            "clean-process matrix の session coverage が不正です: "
            f"expected={list(_SESSION_LABELS)}, actual={sorted(by_label)}",
        )
    target_a, target_b = _matrix_targets(by_label)
    expected_sequences = dict(_matrix_sequences(target_a, target_b))
    all_attempts: list[dict[str, Any]] = []
    for label in _SESSION_LABELS:
        report = by_label[label]
        sequence = report.get("sequence")
        if sequence != list(expected_sequences[label]):
            raise RoleContaminationCanaryError(
                f"{label} の sequence が matrix と一致しません: {sequence}",
            )
        attempts = report.get("attempts")
        if not isinstance(attempts, list) or len(attempts) != len(sequence):
            raise RoleContaminationCanaryError(
                f"{label} の attempt coverage が不正です。",
            )
        for position, (attempt, target) in enumerate(
            zip(attempts, sequence, strict=True),
            1,
        ):
            if not isinstance(attempt, dict):
                raise RoleContaminationCanaryError(
                    f"{label} attempt は object が必要です。",
                )
            if (
                attempt.get("session_id") != report["session_id"]
                or attempt.get("session_label") != label
                or attempt.get("position") != position
                or attempt.get("target") != target
            ):
                raise RoleContaminationCanaryError(
                    f"{label} attempt の session/position/target が不正です。",
                )
            all_attempts.append(attempt)

    model = reports[0].get("model")
    requested_params = reports[0].get("requested_params")
    comparison_seed = reports[0].get("comparison_seed")
    for report in reports[1:]:
        if (
            report.get("model") != model
            or report.get("requested_params") != requested_params
            or report.get("comparison_seed") != comparison_seed
        ):
            raise RoleContaminationCanaryError(
                "session 間で model/requested params/comparison seed が一致しません。",
            )

    target_results = [
        _aggregate_target(target, all_attempts)
        for target in (target_a, target_b)
    ]
    verdict = max(
        (str(result["verdict"]) for result in target_results),
        key=lambda value: _VERDICT_RANK[value],
    )
    return {
        "format_version": FORMAT_VERSION,
        "report_kind": MATRIX_REPORT_KIND,
        "created_at": datetime.now(UTC).isoformat(),
        "verdict": verdict,
        "verdict_policy": {
            "pass": (
                "isolated repeat と全 sequence が同一 input/seed/sampling で "
                "bit-identical"
            ),
            "review": (
                "等価な実験 topology の反復が不一致、または差異が未反復で、"
                "非決定性と状態汚染を機械的に分離できない"
            ),
            "fail": (
                "全ての反復可能 topology は内部で安定しているが、isolated "
                "baseline に対して安定した出力または generation input の偏移がある"
            ),
        },
        "targets": [target_a, target_b],
        "comparison_seed": comparison_seed,
        "model": model,
        "requested_params": requested_params,
        "target_results": target_results,
        "sessions": [by_label[label] for label in _SESSION_LABELS],
    }


def _aggregate_target(
    target: str,
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    target_attempts = [
        dict(attempt) for attempt in attempts if attempt["target"] == target
    ]
    if not target_attempts:
        raise RoleContaminationCanaryError(
            f"target attempt がありません: {target}",
        )
    context_keys = {
        canonical_json(
            {
                "seed": attempt["take_context"]["seed"],
                "sampling": attempt["take_context"]["sampling"],
            },
        )
        for attempt in target_attempts
    }
    if len(context_keys) != 1:
        raise RoleContaminationCanaryError(
            f"target の seed/sampling が session 間で一致しません: {target}",
        )

    input_hashes = {
        _required_sha256(attempt, "generation_input_sha256", target)
        for attempt in target_attempts
    }
    comparison_groups: list[dict[str, Any]] = []
    for input_sha256 in sorted(input_hashes):
        grouped = [
            _attempt_evidence(attempt)
            for attempt in target_attempts
            if attempt["generation_input_sha256"] == input_sha256
        ]
        comparison_groups.append(
            {
                "generation_input_sha256": input_sha256,
                "attempt_count": len(grouped),
                "wav_sha256_values": sorted(
                    {str(attempt["wav_sha256"]) for attempt in grouped},
                ),
                "attempts": grouped,
            },
        )

    topology_attempts = _target_topology_attempts(target, target_attempts)
    topology_results: list[dict[str, Any]] = []
    for topology, grouped_attempts in topology_attempts.items():
        topology_input_hashes = sorted(
            {
                _required_sha256(
                    attempt,
                    "generation_input_sha256",
                    target,
                )
                for attempt in grouped_attempts
            },
        )
        topology_wav_hashes = sorted(
            {
                _required_sha256(attempt, "wav_sha256", target)
                for attempt in grouped_attempts
            },
        )
        replicated = len(grouped_attempts) > 1
        topology_results.append(
            {
                "topology": topology,
                "attempt_count": len(grouped_attempts),
                "replicated": replicated,
                "stable": (
                    len(topology_input_hashes) == 1
                    and len(topology_wav_hashes) == 1
                    if replicated
                    else None
                ),
                "generation_input_sha256_values": topology_input_hashes,
                "wav_sha256_values": topology_wav_hashes,
                "attempts": [
                    _attempt_evidence(attempt)
                    for attempt in grouped_attempts
                ],
            },
        )

    by_topology = {
        str(result["topology"]): result for result in topology_results
    }
    isolated_result = by_topology["isolated"]
    isolated_stable = isolated_result["stable"] is True
    unstable_topologies = [
        result
        for result in topology_results
        if result["replicated"] and result["stable"] is not True
    ]
    if unstable_topologies:
        return {
            "target": target,
            "verdict": "review",
            "reason": (
                "等価な実験 topology の clean-process repeat が安定せず、"
                "非決定性と状態汚染を分離できない"
            ),
            "bit_identical": False,
            "isolated_stable": isolated_stable,
            "comparison_groups": comparison_groups,
            "topology_groups": topology_results,
            "difference_evidence": {
                "unstable_topologies": unstable_topologies,
                "isolated_wav_sha256_values": isolated_result[
                    "wav_sha256_values"
                ],
                "all_wav_sha256_values": sorted(
                    {
                        _required_sha256(attempt, "wav_sha256", target)
                        for attempt in target_attempts
                    },
                ),
            },
        }

    baseline_input_sha256 = str(
        isolated_result["generation_input_sha256_values"][0],
    )
    baseline_wav_sha256 = str(isolated_result["wav_sha256_values"][0])
    shifted_replicated = [
        result
        for result in topology_results
        if result["topology"] != "isolated"
        and result["replicated"]
        and (
            result["generation_input_sha256_values"]
            != [baseline_input_sha256]
            or result["wav_sha256_values"] != [baseline_wav_sha256]
        )
    ]
    if shifted_replicated:
        mismatched_attempts = [
            attempt
            for result in shifted_replicated
            for attempt in result["attempts"]
        ]
        return {
            "target": target,
            "verdict": "fail",
            "reason": (
                "全ての反復可能 topology は内部で安定しているが、"
                "isolated baseline に対する安定した偏移がある"
            ),
            "bit_identical": False,
            "isolated_stable": True,
            "comparison_groups": comparison_groups,
            "topology_groups": topology_results,
            "difference_evidence": {
                "isolated_baseline_generation_input_sha256": (
                    baseline_input_sha256
                ),
                "isolated_baseline_sha256": baseline_wav_sha256,
                "generation_input_sha256_values": sorted(input_hashes),
                "shifted_topologies": shifted_replicated,
                "mismatched_attempts": mismatched_attempts,
            },
        }

    shifted_singletons = [
        result
        for result in topology_results
        if not result["replicated"]
        and (
            result["generation_input_sha256_values"]
            != [baseline_input_sha256]
            or result["wav_sha256_values"] != [baseline_wav_sha256]
        )
    ]
    if shifted_singletons:
        return {
            "target": target,
            "verdict": "review",
            "reason": (
                "未反復 topology だけに差異があり、状態汚染として再現確認できない"
            ),
            "bit_identical": False,
            "isolated_stable": True,
            "comparison_groups": comparison_groups,
            "topology_groups": topology_results,
            "difference_evidence": {
                "isolated_baseline_generation_input_sha256": (
                    baseline_input_sha256
                ),
                "isolated_baseline_sha256": baseline_wav_sha256,
                "unreplicated_shifted_topologies": shifted_singletons,
            },
        }
    return {
        "target": target,
        "verdict": "pass",
        "reason": (
            "isolated repeat と全 sequence が同一 input/seed/sampling で bit-identical"
        ),
        "bit_identical": True,
        "isolated_stable": True,
        "comparison_groups": comparison_groups,
        "topology_groups": topology_results,
        "difference_evidence": {
            "isolated_baseline_generation_input_sha256": (
                baseline_input_sha256
            ),
            "isolated_baseline_sha256": baseline_wav_sha256,
            "mismatched_attempts": [],
        },
    }


def _target_topology_attempts(
    target: str,
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    is_target_a = any(
        attempt["session_label"] == "isolated-a-1"
        for attempt in attempts
    )
    if is_target_a:
        topology_members = {
            "isolated": {
                ("isolated-a-1", 1),
                ("isolated-a-2", 1),
            },
            "multi-target-clean": {
                ("forward", 1),
                ("aba", 1),
            },
            "preceded-other": {
                ("reverse", 2),
                ("bab", 2),
            },
            "cycle-repeat": {("aba", 3)},
        }
    else:
        topology_members = {
            "isolated": {
                ("isolated-b-1", 1),
                ("isolated-b-2", 1),
            },
            "multi-target-clean": {
                ("reverse", 1),
                ("bab", 1),
            },
            "preceded-other": {
                ("forward", 2),
                ("aba", 2),
            },
            "cycle-repeat": {("bab", 3)},
        }
    grouped = {
        topology: [
            attempt
            for attempt in attempts
            if (attempt["session_label"], attempt["position"]) in members
        ]
        for topology, members in topology_members.items()
    }
    expected_counts = {
        "isolated": 2,
        "multi-target-clean": 2,
        "preceded-other": 2,
        "cycle-repeat": 1,
    }
    actual_counts = {
        topology: len(grouped_attempts)
        for topology, grouped_attempts in grouped.items()
    }
    if actual_counts != expected_counts:
        raise RoleContaminationCanaryError(
            f"target topology coverage が不正です: {target}: "
            f"expected={expected_counts}, actual={actual_counts}",
        )
    return grouped


def _attempt_evidence(attempt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "session_id": attempt["session_id"],
        "session_label": attempt["session_label"],
        "position": attempt["position"],
        "target": attempt["target"],
        "generation_input_sha256": attempt["generation_input_sha256"],
        "realized_sha256": attempt["realized_sha256"],
        "wav": attempt["wav"],
        "wav_sha256": attempt["wav_sha256"],
    }


def _launch_session_process(spec_path: Path) -> dict[str, Any]:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gaya_pipeline.role_contamination_canary",
            "_session",
            "--spec",
            str(spec_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        details = stderr.strip() or stdout.strip()
        raise RoleContaminationCanaryError(
            f"clean-process session に失敗しました: {spec_path}: {details}",
        )
    spec = _read_json_object(spec_path, "session spec")
    report_path = Path(str(spec["session_root"])) / "report.json"
    report = _read_json_object(report_path, "session report")
    _validate_session_against_spec(
        report,
        spec,
        expected_process_id=process.pid,
    )
    return report


def _execute_session(
    spec: Mapping[str, Any],
    *,
    adapter_factory: Callable[[str], Any] | None = None,
    process_id: int | None = None,
    parent_process_id: int | None = None,
    process_instance_id: str | None = None,
) -> dict[str, Any]:
    _validate_session_spec(spec)
    session_root = Path(str(spec["session_root"]))
    if session_root.exists():
        raise RoleContaminationCanaryError(
            f"session root は新規 path が必要です: {session_root}",
        )
    scenarios_dir = Path(str(spec["scenarios_dir"]))
    voices_dir = Path(str(spec["voices_dir"]))
    sequence = tuple(str(target) for target in spec["sequence"])
    _validate_scenario_directory(scenarios_dir)
    jobs_by_target = _load_target_jobs(scenarios_dir, sequence)

    create = create_adapter if adapter_factory is None else adapter_factory
    adapter = create(str(spec["model_id"]))
    recipe = adapter.take_recipe()
    context = _comparison_context(recipe, int(spec["seed"]))
    requested_params = dict(adapter.generation_params())
    canonical_json(requested_params)
    session_root.mkdir(parents=True)
    prepare_jobs = [jobs_by_target[target] for target in dict.fromkeys(sequence)]
    adapter.prepare(
        prepare_jobs,
        session_root / "adapter-artifacts",
        voices_dir,
    )

    attempts: list[dict[str, Any]] = []
    for position, target in enumerate(sequence, 1):
        job = jobs_by_target[target]
        generation_input = dict(adapter.generation_input(job, context))
        input_sha256 = hashlib.sha256(
            canonical_json(generation_input).encode("utf-8"),
        ).hexdigest()
        output_wav = (
            session_root
            / "audio"
            / f"{position:02d}-{target.replace('/', '--')}.wav"
        )
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        realized = dict(adapter.generate(job, context, output_wav))
        if not output_wav.is_file():
            raise RoleContaminationCanaryError(
                f"adapter output がありません: {output_wav}",
            )
        attempts.append(
            {
                "session_id": spec["session_id"],
                "session_label": spec["label"],
                "position": position,
                "target": target,
                "take_context": {
                    "index": context.index,
                    "seed": context.seed,
                    "recipe_version": context.recipe_version,
                    "sampling": context.sampling_dict(),
                },
                "generation_input": generation_input,
                "generation_input_sha256": input_sha256,
                "realized": realized,
                "realized_sha256": hashlib.sha256(
                    canonical_json(realized).encode("utf-8"),
                ).hexdigest(),
                "wav": output_wav.relative_to(session_root).as_posix(),
                "wav_sha256": _sha256_file(output_wav),
            },
        )

    report = {
        "format_version": FORMAT_VERSION,
        "report_kind": SESSION_REPORT_KIND,
        "created_at": datetime.now(UTC).isoformat(),
        "session_id": spec["session_id"],
        "session_token": spec["session_token"],
        "label": spec["label"],
        "process_id": os.getpid() if process_id is None else process_id,
        "parent_process_id": (
            os.getppid() if parent_process_id is None else parent_process_id
        ),
        "process_instance_id": (
            str(uuid.uuid4())
            if process_instance_id is None
            else process_instance_id
        ),
        "prepare_invocation_count": 1,
        "prepare_targets": list(dict.fromkeys(sequence)),
        "sequence": list(sequence),
        "comparison_seed": context.seed,
        "model": adapter.profile.as_manifest_entry(),
        "requested_params": requested_params,
        "attempts": attempts,
    }
    _write_json_new(session_root / "report.json", report)
    return report


def _validate_session_against_spec(
    report: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    expected_process_id: int,
) -> None:
    for key in ("session_id", "session_token", "label", "sequence"):
        if report.get(key) != spec.get(key):
            raise RoleContaminationCanaryError(
                f"session report.{key} が spec と一致しません。",
            )
    if report.get("parent_process_id") != expected_process_id:
        raise RoleContaminationCanaryError(
            "session report.parent_process_id が起動した venv redirector と"
            "一致しません: "
            f"expected={expected_process_id}, "
            f"actual={report.get('parent_process_id')}",
        )
    if report.get("process_id") == expected_process_id:
        raise RoleContaminationCanaryError(
            "session report.process_id は venv redirector ではなく、"
            "実際の base Python process である必要があります: "
            f"redirector={expected_process_id}",
        )


def _validate_session_spec(spec: Mapping[str, Any]) -> None:
    expected = {
        "format_version",
        "session_id",
        "session_token",
        "label",
        "model_id",
        "scenarios_dir",
        "voices_dir",
        "session_root",
        "sequence",
        "seed",
    }
    if set(spec) != expected or spec.get("format_version") != FORMAT_VERSION:
        raise RoleContaminationCanaryError(
            "session spec の構造または format_version が不正です。",
        )
    if spec.get("label") not in _SESSION_LABELS:
        raise RoleContaminationCanaryError(
            f"session label が不正です: {spec.get('label')}",
        )
    for key in (
        "session_id",
        "session_token",
        "model_id",
        "scenarios_dir",
        "voices_dir",
        "session_root",
    ):
        _required_string(spec, key, "session spec")
    sequence = spec.get("sequence")
    if not isinstance(sequence, list) or not sequence:
        raise RoleContaminationCanaryError(
            "session spec.sequence は1件以上必要です。",
        )
    for target in sequence:
        if not isinstance(target, str):
            raise RoleContaminationCanaryError(
                "session spec.sequence は target string が必要です。",
            )
        _parse_target(target)
    seed = spec.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise RoleContaminationCanaryError(
            "session spec.seed が不正です。",
        )


def _validate_matrix_targets(targets: Sequence[str]) -> tuple[str, str]:
    if len(targets) != 2:
        raise RoleContaminationCanaryError(
            "clean-process matrix には異なる target A/B が2件必要です。",
        )
    target_a, target_b = (str(target) for target in targets)
    _parse_target(target_a)
    _parse_target(target_b)
    if target_a == target_b:
        raise RoleContaminationCanaryError(
            "clean-process matrix の target A/B は異なる必要があります。",
        )
    return target_a, target_b


def _matrix_sequences(
    target_a: str,
    target_b: str,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return (
        ("isolated-a-1", (target_a,)),
        ("isolated-a-2", (target_a,)),
        ("isolated-b-1", (target_b,)),
        ("isolated-b-2", (target_b,)),
        ("forward", (target_a, target_b)),
        ("reverse", (target_b, target_a)),
        ("aba", (target_a, target_b, target_a)),
        ("bab", (target_b, target_a, target_b)),
    )


def _matrix_targets(
    by_label: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str]:
    isolated_a = by_label["isolated-a-1"].get("sequence")
    isolated_b = by_label["isolated-b-1"].get("sequence")
    if (
        not isinstance(isolated_a, list)
        or len(isolated_a) != 1
        or not isinstance(isolated_b, list)
        or len(isolated_b) != 1
        or not isinstance(isolated_a[0], str)
        or not isinstance(isolated_b[0], str)
    ):
        raise RoleContaminationCanaryError(
            "isolated session の target coverage が不正です。",
        )
    return _validate_matrix_targets((isolated_a[0], isolated_b[0]))


def _load_target_jobs(
    scenarios_dir: Path,
    targets: Sequence[str],
) -> dict[str, LineJob]:
    jobs_by_target: dict[str, LineJob] = {}
    for target in dict.fromkeys(targets):
        scenario_id, line_id = _parse_target(target)
        jobs, _sources = _load_jobs(
            scenarios_dir,
            scenario_id=scenario_id,
            line_id=line_id,
        )
        if len(jobs) != 1:
            raise RoleContaminationCanaryError(
                f"target が一意ではありません: {target}",
            )
        jobs_by_target[target] = jobs[0]
    return jobs_by_target


def _validate_scenario_directory(scenarios_dir: Path) -> None:
    validation = validate_scenarios(scenarios_dir)
    if validation.problems:
        details = "\n".join(str(problem) for problem in validation.problems)
        raise RoleContaminationCanaryError(
            f"シナリオ検証に失敗しました:\n{details}",
        )


def _comparison_context(recipe: TakeRecipe, seed: int) -> TakeContext:
    if recipe.seed_policy != "derived-sha256-v1":
        return recipe.single_take_context()
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise RoleContaminationCanaryError("seed は integer が必要です。")
    if recipe.seed_range is None or not recipe.seed_range[0] <= seed <= recipe.seed_range[1]:
        raise RoleContaminationCanaryError(
            f"seed は {recipe.seed_range} の範囲が必要です。",
        )
    return TakeContext(
        index=1,
        seed=seed,
        recipe_version=recipe.version,
        sampling=recipe.sampling,
    )


def _parse_target(value: str) -> tuple[str, str]:
    parts = value.split("/")
    if (
        len(parts) != 2
        or any(_IDENTIFIER.fullmatch(part) is None for part in parts)
    ):
        raise RoleContaminationCanaryError(
            f"target は <scenario>/<line> 形式が必要です: {value}",
        )
    return parts[0], parts[1]


def _add_unique_session_value(
    values: set[str],
    value: str,
    field: str,
) -> None:
    if value in values:
        raise RoleContaminationCanaryError(
            f"{field} が session 間で重複しています: {value}",
        )
    values.add(value)


def _required_string(
    value: Mapping[str, Any],
    key: str,
    owner: str,
) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise RoleContaminationCanaryError(
            f"{owner}.{key} は non-empty string が必要です。",
        )
    return item


def _required_sha256(
    value: Mapping[str, Any],
    key: str,
    owner: str,
) -> str:
    item = _required_string(value, key, owner)
    if re.fullmatch(r"[0-9a-f]{64}", item) is None:
        raise RoleContaminationCanaryError(
            f"{owner}.{key} は SHA-256 が必要です。",
        )
    return item


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path, owner: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RoleContaminationCanaryError(
            f"{owner} を読み込めません: {path}: {error}",
        ) from error
    if not isinstance(value, dict):
        raise RoleContaminationCanaryError(
            f"{owner} は object が必要です: {path}",
        )
    return value


def _write_json_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RoleContaminationCanaryError(
            f"出力 path は新規 file が必要です: {path}",
        )
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="独立 process matrix で跨項目の役柄汚染を監査します。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    matrix = subparsers.add_parser("matrix")
    matrix.add_argument("--model", required=True)
    matrix.add_argument("--scenarios", type=Path, required=True)
    matrix.add_argument("--voices", type=Path, required=True)
    matrix.add_argument("--run-root", type=Path, required=True)
    matrix.add_argument("--target", action="append", required=True)
    matrix.add_argument("--seed", type=int, default=DEFAULT_SEED)

    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument(
        "--session-report",
        action="append",
        type=Path,
        required=True,
    )
    aggregate.add_argument("--output", type=Path, required=True)

    session = subparsers.add_parser("_session")
    session.add_argument("--spec", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "matrix":
        run_role_contamination_canary(
            model_id=args.model,
            scenarios_dir=args.scenarios,
            voices_dir=args.voices,
            run_root=args.run_root,
            targets=args.target,
            seed=args.seed,
        )
        return 0
    if args.command == "aggregate":
        reports = [
            _read_json_object(path, "session report")
            for path in args.session_report
        ]
        _write_json_new(
            args.output,
            aggregate_role_contamination_reports(reports),
        )
        return 0
    spec = _read_json_object(args.spec, "session spec")
    _execute_session(spec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
