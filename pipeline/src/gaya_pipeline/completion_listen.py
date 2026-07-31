from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from gaya_pipeline.completion_plan import CompletionPlan
from gaya_pipeline.curation import (
    CurationError,
    build_candidate_set,
    canonical_candidate_set_bytes,
    validate_snapshot_bundle,
)
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.take_ledger import TakeLedgerError, read_ledger
from gaya_pipeline.take_manifest_v4 import TakeManifestError, validate_manifest_v4
from gaya_pipeline.validation import validate_scenario_ids


class CompletionListeningError(RuntimeError):
    pass


@dataclass(frozen=True)
class CompletionListeningSummary:
    output_dir: Path
    candidate_set_sha256: str
    model_count: int
    group_count: int
    candidate_count: int


def build_completion_listening_bundle(
    *,
    plan: CompletionPlan,
    run_ids: Sequence[str],
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    output_dir: Path,
) -> CompletionListeningSummary:
    if len(run_ids) != len(set(run_ids)):
        raise CompletionListeningError("supplement run-id が重複しています。")
    if not run_ids:
        raise CompletionListeningError("supplement run-id は1件以上必要です。")
    if output_dir.exists():
        raise CompletionListeningError(
            f"listening output は既存 path を拒否します: {output_dir}",
        )
    output_parent = output_dir.resolve().parent
    if not output_parent.is_dir():
        raise CompletionListeningError(
            f"listening output の親 directory が存在しません: {output_parent}",
        )

    takes_root = _require_directory(artifacts_dir / "takes", "takes root")
    expected_by_model = {
        model: {
            (target.model, target.scenario, target.line, target.variant)
            for target in plan.targets_for_model(model)
        }
        for model in sorted({target.model for target in plan.targets})
    }
    seen_models: set[str] = set()
    models: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    source_audio: dict[str, Path] = {}
    generated_at: list[str] = []

    for run_id in run_ids:
        run_root = _resolve_direct_child(takes_root, run_id, f"source run {run_id}")
        try:
            ledger = read_ledger(run_root / "ledger.json")
            bundle = validate_snapshot_bundle(
                snapshot_path=run_root / "manifest-v4.json",
                candidate_set_path=run_root / "candidate-set.json",
                marker_path=run_root / "candidate-set.sha256",
            )
        except (
            CurationError,
            OSError,
            TakeLedgerError,
            UnicodeError,
            json.JSONDecodeError,
        ) as error:
            raise CompletionListeningError(
                f"supplement run bundle が不正です: {run_id}: {error}",
            ) from error
        if ledger["run_id"] != run_id:
            raise CompletionListeningError(
                f"run-id と ledger.run_id が一致しません: {run_id}",
            )
        run_models = bundle.manifest["models"]
        if len(run_models) != 1:
            raise CompletionListeningError(
                f"supplement run は単一 model が必要です: {run_id}",
            )
        model = str(run_models[0]["id"])
        if model in seen_models:
            raise CompletionListeningError(
                f"初回 listening bundle は model ごとに1 runだけ受理します: {model}",
            )
        expected_groups = expected_by_model.get(model)
        if expected_groups is None:
            raise CompletionListeningError(
                f"plan 対象外 model の run です: {run_id}: {model}",
            )
        actual_groups = {
            _group_key(candidate) for candidate in bundle.manifest["candidates"]
        }
        actual_groups.update(
            _group_key(failure) for failure in bundle.manifest["failures"]
        )
        if actual_groups != expected_groups:
            missing = sorted(expected_groups - actual_groups)
            extra = sorted(actual_groups - expected_groups)
            raise CompletionListeningError(
                f"supplement run target coverage が plan と一致しません: "
                f"{model}: missing={missing}, extra={extra}",
            )
        if bundle.manifest["failures"]:
            raise CompletionListeningError(
                f"supplement run に eligible candidate のない group があります: {model}",
            )
        by_group: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
        for candidate in bundle.manifest["candidates"]:
            by_group.setdefault(_group_key(candidate), []).append(candidate)
        insufficient = sorted(
            identity
            for identity, group_candidates in by_group.items()
            if len(group_candidates) < plan.minimum_eligible_candidates
        )
        if insufficient:
            raise CompletionListeningError(
                f"mechanical-pass candidate が最低数を満たしません: {insufficient}",
            )

        _validate_ledger_source(
            ledger=ledger,
            model=model,
            plan=plan,
            run_id=run_id,
        )
        seen_models.add(model)
        models.append(dict(run_models[0]))
        generated_at.append(str(bundle.manifest["generated_at"]))
        for candidate in bundle.manifest["candidates"]:
            normalized = dict(candidate)
            local_path = _local_audio_path(run_root, normalized)
            _verify_audio_sha(local_path, str(normalized["sha256"]))
            if normalized["path"] in source_audio:
                raise CompletionListeningError(
                    f"supplement candidate path が重複しています: "
                    f"{normalized['path']}",
                )
            source_audio[str(normalized["path"])] = local_path
            candidates.append(normalized)

    if seen_models != set(expected_by_model):
        missing_models = sorted(set(expected_by_model) - seen_models)
        raise CompletionListeningError(
            f"supplement model run が不足しています: {missing_models}",
        )

    candidates.sort(
        key=lambda candidate: (
            _group_key(candidate),
            int(candidate["take_index"]),
        ),
    )
    scenario_sha256, lines = _load_target_lines(
        scenarios_dir=scenarios_dir.resolve(),
        voices_dir=voices_dir.resolve(),
        targets={(target.scenario, target.line) for target in plan.targets},
    )
    candidate_set = build_candidate_set(
        scenario_sha256=scenario_sha256,
        lines=lines,
        models=sorted(models, key=lambda model: str(model["id"])),
        candidates=candidates,
        failures=[],
    )
    candidate_set_bytes = canonical_candidate_set_bytes(candidate_set)
    candidate_set_sha256 = hashlib.sha256(candidate_set_bytes).hexdigest()
    try:
        manifest = validate_manifest_v4(
            {
                "format_version": 4,
                "generated_at": max(generated_at),
                "candidate_set_sha256": candidate_set_sha256,
                "models": candidate_set["models"],
                "candidates": candidates,
                "curations": [],
                "failures": [],
            },
        )
    except (TakeManifestError, TakeLedgerError) as error:
        raise CompletionListeningError(
            f"combined listening manifest が不正です: {error}",
        ) from error
    manifest_bytes = canonical_json(manifest).encode("utf-8")

    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.",
            dir=output_parent,
        ),
    )
    try:
        _write_new_file(temporary / "manifest-v4.json", manifest_bytes)
        _write_new_file(temporary / "candidate-set.json", candidate_set_bytes)
        _write_new_file(
            temporary / "candidate-set.sha256",
            candidate_set_sha256.encode("ascii"),
        )
        _write_new_file(
            temporary / "completion-plan.sha256",
            plan.raw_sha256.encode("ascii"),
        )
        for candidate in candidates:
            destination = temporary / _listening_audio_relative(candidate)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_audio[str(candidate["path"])], destination)
            _verify_audio_sha(destination, str(candidate["sha256"]))
        temporary.replace(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    return CompletionListeningSummary(
        output_dir=output_dir,
        candidate_set_sha256=candidate_set_sha256,
        model_count=len(models),
        group_count=len({_group_key(candidate) for candidate in candidates}),
        candidate_count=len(candidates),
    )


def _validate_ledger_source(
    *,
    ledger: Mapping[str, Any],
    model: str,
    plan: CompletionPlan,
    run_id: str,
) -> None:
    source = ledger.get("source")
    if not isinstance(source, Mapping):
        raise CompletionListeningError(f"ledger.source が不正です: {run_id}")
    if source.get("model") != model:
        raise CompletionListeningError(
            f"ledger.source.model が run manifest と一致しません: {run_id}",
        )
    if source.get("takes") != plan.takes:
        raise CompletionListeningError(
            f"ledger.source.takes が plan と一致しません: {run_id}",
        )
    if source.get("seed_base") != plan.seed_base:
        raise CompletionListeningError(
            f"ledger.source.seed_base が plan と一致しません: {run_id}",
        )
    groups = source.get("groups")
    if not isinstance(groups, list):
        raise CompletionListeningError(f"ledger.source.groups が不正です: {run_id}")
    actual = {
        (str(group.get("scenario")), str(group.get("line")))
        for group in groups
        if isinstance(group, Mapping)
    }
    expected = set(plan.target_lines_for_model(model))
    if actual != expected or len(groups) != len(expected):
        raise CompletionListeningError(
            f"ledger.source.groups が plan target と一致しません: {run_id}",
        )


def _load_target_lines(
    *,
    scenarios_dir: Path,
    voices_dir: Path,
    targets: set[tuple[str, str]],
) -> tuple[str, list[dict[str, Any]]]:
    scenario_ids = sorted({scenario for scenario, _line in targets})
    validation = validate_scenario_ids(
        scenarios_dir,
        scenario_ids,
        voices_dir=voices_dir,
    )
    if validation.problems:
        details = "\n".join(str(problem) for problem in validation.problems)
        raise CompletionListeningError(
            f"target scenario 検証に失敗しました:\n{details}",
        )
    source_files: list[dict[str, str]] = []
    lines: list[dict[str, Any]] = []
    found: set[tuple[str, str]] = set()
    for scenario_id in scenario_ids:
        path = scenarios_dir / f"{scenario_id}.yaml"
        try:
            raw = path.read_bytes()
            document = yaml.safe_load(raw.decode("utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise CompletionListeningError(
                f"target scenario を読み込めません: {path}: {error}",
            ) from error
        if not isinstance(document, dict) or document.get("id") != scenario_id:
            raise CompletionListeningError(
                f"target scenario id が一致しません: {path}",
            )
        source_files.append(
            {
                "path": path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        for line in document["lines"]:
            identity = (scenario_id, str(line["id"]))
            if identity not in targets:
                continue
            if identity in found:
                raise CompletionListeningError(
                    f"target line が重複しています: {identity}",
                )
            found.add(identity)
            lines.append(
                {
                    "scenario": scenario_id,
                    "line": str(line["id"]),
                    "scenario_title": str(document["title"]),
                    "text": str(line["text"]),
                    "delivery": str(line["delivery"]),
                },
            )
    if found != targets:
        raise CompletionListeningError(
            f"target line が scenario source にありません: {sorted(targets - found)}",
        )
    scenario_sha256 = hashlib.sha256(
        canonical_json(source_files).encode("utf-8"),
    ).hexdigest()
    return scenario_sha256, sorted(
        lines,
        key=lambda line: (line["scenario"], line["line"]),
    )


def _local_audio_path(run_root: Path, candidate: Mapping[str, Any]) -> Path:
    relative = Path(
        "audio",
        str(candidate["model"]),
        str(candidate["scenario"]),
        str(candidate["line"]),
        str(candidate["variant"]),
        f"take-{int(candidate['take_index']):04d}.opus",
    )
    path = run_root / relative
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise CompletionListeningError(
            f"supplement Opus が存在しません: {path}",
        ) from error
    if not resolved.is_relative_to(run_root) or not resolved.is_file():
        raise CompletionListeningError(
            f"supplement Opus は run root 内の通常ファイルが必要です: {path}",
        )
    return resolved


def _listening_audio_relative(candidate: Mapping[str, Any]) -> Path:
    return Path(
        "audio",
        str(candidate["model"]),
        str(candidate["scenario"]),
        str(candidate["line"]),
        str(candidate["variant"]),
        f"take-{int(candidate['take_index']):04d}.opus",
    )


def _verify_audio_sha(path: Path, expected: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise CompletionListeningError(
            f"Opus SHA-256 を計算できません: {path}: {error}",
        ) from error
    if actual != expected:
        raise CompletionListeningError(
            f"Opus SHA-256 が manifest と一致しません: {path}",
        )


def _group_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value["model"]),
        str(value["scenario"]),
        str(value["line"]),
        str(value["variant"]),
    )


def _resolve_direct_child(root: Path, name: str, label: str) -> Path:
    candidate = root / name
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise CompletionListeningError(f"{label} が存在しません: {candidate}") from error
    if resolved.parent != root or not resolved.is_dir():
        raise CompletionListeningError(
            f"{label} は root 直下の directory が必要です: {candidate}",
        )
    return resolved


def _require_directory(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise CompletionListeningError(f"{label} が存在しません: {path}") from error
    if not resolved.is_dir():
        raise CompletionListeningError(f"{label} は directory が必要です: {path}")
    return resolved


def _write_new_file(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
    except FileExistsError as error:
        raise CompletionListeningError(
            f"listening file が既に存在します: {path}",
        ) from error
