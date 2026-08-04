from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from gaya_pipeline.adapters import UnknownAdapterError, create_adapter
from gaya_pipeline.adapters.base import (
    Adapter,
    LineJob,
    TakeContext,
    TakeRecipe,
    require_take_context,
)
from gaya_pipeline.audio import (
    AudioProcessingError,
    AudioTools,
    PostprocessProfile,
    encode_opus,
    find_audio_tools,
    measure_encoded_opus,
    normalize_wav,
    probe_audio,
)
from gaya_pipeline.run_lock import RunLockError, exclusive_run_lock
from gaya_pipeline.take_identity import (
    canonical_json,
    derive_seed,
    generation_input_sha256,
    make_take_id,
)
from gaya_pipeline.take_ledger import (
    TakeLedgerError,
    read_ledger,
    transition_attempt,
    write_ledger_atomic,
)
from gaya_pipeline.take_sidecar import (
    TakeSidecarError,
    validate_take_sidecar,
)
from gaya_pipeline.validation import validate_scenarios


class GenerationError(RuntimeError):
    pass


VARIANT = "dry"
PHASE_B_PROTOCOL = "phase-b-generation-v2"
ANCHOR_MODELS = {
    "irodori-tts-600m-v3-voicedesign",
    "irodori-tts-v4-small",
    "qwen3-tts-12hz-1.7b",
}


@dataclass(frozen=True)
class GenerationRecord:
    scenario_id: str
    line_id: str
    take_index: int
    status: Literal["generated", "skipped"]
    generation_seconds: float
    rtf: float
    take_id: str


@dataclass(frozen=True)
class GenerationFailureRecord:
    scenario_id: str
    line_id: str
    take_index: int
    message: str


@dataclass(frozen=True)
class GenerationSummary:
    run_id: str
    ledger_path: Path
    records: tuple[GenerationRecord, ...]
    failures: tuple[GenerationFailureRecord, ...]
    elapsed_seconds: float

    @property
    def generated_count(self) -> int:
        return sum(record.status == "generated" for record in self.records)

    @property
    def skipped_count(self) -> int:
        return sum(record.status == "skipped" for record in self.records)

    @property
    def failed_count(self) -> int:
        return len(self.failures)


@dataclass(frozen=True)
class _AttemptPlan:
    model_id: str
    job: LineJob
    context: TakeContext
    generation_input_sha256: str
    phase_b_provenance: Mapping[str, Any] | None
    phase_b_provenance_sha256: str | None

    @property
    def slot(self) -> tuple[str, str, str, str, int]:
        return (
            self.model_id,
            self.job.scenario_id,
            self.job.line_id,
            VARIANT,
            self.context.index,
        )


@dataclass(frozen=True)
class _ScenarioSource:
    path: Path
    sha256: str


def run_generation(
    *,
    model_id: str,
    scenarios_dir: Path,
    artifacts_dir: Path,
    voices_dir: Path | None = None,
    scenario_id: str | None = None,
    line_id: str | None = None,
    target_lines: Sequence[tuple[str, str]] | None = None,
    takes: int,
    seed_base: int | None,
    force: bool = False,
    resume_run_id: str | None = None,
    role_anchor_selection_path: Path | None = None,
    role_anchor_plan_sha256: str | None = None,
    role_anchor_selection_sha256: str | None = None,
    completion_plan_sha256: str | None = None,
    role_epochs: Mapping[tuple[str, str], str] | None = None,
    run_kind: Literal["primary", "topup"] | None = None,
    supersedes_run_id: str | None = None,
) -> GenerationSummary:
    if (role_anchor_selection_path is None) != (
        role_anchor_plan_sha256 is None
    ):
        raise GenerationError(
            "role anchor selection pathとfrozen plan SHAは同時に指定してください。",
        )
    if (
        role_anchor_selection_path is not None
        and not role_anchor_selection_path.is_absolute()
    ):
        raise GenerationError("role anchor selectionは絶対pathが必要です。")
    if (
        role_anchor_plan_sha256 is not None
        and (
            len(role_anchor_plan_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in role_anchor_plan_sha256
            )
        )
    ):
        raise GenerationError(
            "role anchor frozen plan SHAは完全な小文字SHA-256が必要です。",
        )
    phase_b_requested = any(
        value is not None
        for value in (
            completion_plan_sha256,
            role_epochs,
            run_kind,
            supersedes_run_id,
            role_anchor_selection_sha256,
        )
    )
    if phase_b_requested and (
        completion_plan_sha256 is None
        or role_epochs is None
        or run_kind is None
    ):
        raise GenerationError(
            "Phase B生成にはfrozen plan SHA、role epochs、run kindが必要です。",
        )
    if resume_run_id is not None and force:
        raise GenerationError("run resumeと--forceは同時に使用できません。")
    if phase_b_requested and force:
        raise GenerationError("Phase B生成では--forceを使用できません。")
    if resume_run_id is not None and not phase_b_requested:
        raise GenerationError("run resumeはPhase B生成だけで使用できます。")
    if resume_run_id is not None:
        _require_path_segment(resume_run_id, "resume_run_id")
    if not phase_b_requested and role_anchor_selection_sha256 is not None:
        raise GenerationError(
            "anchor selection SHAはPhase B provenanceと同時に指定してください。",
        )
    normalized_targets = _validate_cli_inputs(
        scenario_id=scenario_id,
        line_id=line_id,
        target_lines=target_lines,
        takes=takes,
        seed_base=seed_base,
    )
    resume_root = (
        _resume_run_root(artifacts_dir / "takes", resume_run_id)
        if resume_run_id is not None
        else None
    )
    effective_voices_dir = (
        voices_dir
        if voices_dir is not None
        else scenarios_dir.parent / "assets" / "voices"
    )
    validation = validate_scenarios(
        scenarios_dir,
        voices_dir=effective_voices_dir,
    )
    if validation.problems:
        details = "\n".join(str(problem) for problem in validation.problems)
        raise GenerationError(f"シナリオ検証に失敗しました:\n{details}")

    try:
        adapter = (
            create_adapter(model_id)
            if role_anchor_selection_path is None
            else create_adapter(
                model_id,
                role_anchor_selection_path=role_anchor_selection_path,
                role_anchor_plan_sha256=role_anchor_plan_sha256,
            )
        )
        jobs, scenario_sources = _load_jobs(
            scenarios_dir,
            scenario_id=scenario_id,
            line_id=line_id,
            target_lines=normalized_targets,
        )
        recipe = adapter.take_recipe()
        requested_params = dict(adapter.generation_params())
        canonical_json(requested_params)
        contexts = _preflight_contexts(
            adapter=adapter,
            jobs=jobs,
            recipe=recipe,
            takes=takes,
            seed_base=seed_base,
        )
        _preflight_slot_paths(adapter.profile.id, jobs, contexts)
        tools = find_audio_tools()
        profile = PostprocessProfile()
    except GenerationError:
        raise
    except UnknownAdapterError as error:
        raise GenerationError(str(error)) from error
    except Exception as error:
        raise GenerationError(str(error)) from error

    started_at = time.perf_counter()
    phase_b_source = (
        None
        if not phase_b_requested
        else _phase_b_source(
            model_id=adapter.profile.id,
            jobs=jobs,
            completion_plan_sha256=completion_plan_sha256,
            role_epochs=role_epochs,
            run_kind=run_kind,
            supersedes_run_id=supersedes_run_id,
            role_anchor_selection_path=role_anchor_selection_path,
            role_anchor_plan_sha256=role_anchor_plan_sha256,
            role_anchor_selection_sha256=role_anchor_selection_sha256,
        )
    )
    source = _ledger_source(
        jobs=jobs,
        model_id=adapter.profile.id,
        takes=takes,
        seed_base=seed_base,
        recipe=recipe,
        scenario_sources=scenario_sources,
        phase_b=phase_b_source,
    )
    superseded_ledger = (
        _validate_topup_source(
            artifacts_dir=artifacts_dir,
            source=source,
        )
        if phase_b_source is not None
        and phase_b_source["run_kind"] == "topup"
        else None
    )
    takes_root = artifacts_dir / "takes"
    if resume_run_id is not None:
        if resume_root is None:
            raise AssertionError("resume root must be resolved")
        run_root = resume_root
        try:
            with exclusive_run_lock(run_root):
                ledger = _load_resume_ledger(
                    run_root=run_root,
                    resume_run_id=resume_run_id,
                    source=source,
                )
                _prepare_adapter(
                    adapter=adapter,
                    jobs=jobs,
                    artifacts_dir=artifacts_dir,
                    voices_dir=effective_voices_dir,
                )
                _verify_scenario_sources(scenario_sources)
                plans = _build_attempt_plans(
                    adapter=adapter,
                    jobs=jobs,
                    contexts=contexts,
                    requested_params=requested_params,
                    profile=profile,
                    tools=tools,
                    phase_b=phase_b_source,
                )
                if superseded_ledger is not None:
                    _validate_topup_attempt_seeds(
                        artifacts_dir=artifacts_dir,
                        previous=superseded_ledger,
                        plans=plans,
                    )
                _validate_resume_attempts(ledger=ledger, plans=plans)
                return _execute_generation(
                    run_root=run_root,
                    ledger=ledger,
                    adapter=adapter,
                    plans=plans,
                    requested_params=requested_params,
                    profile=profile,
                    tools=tools,
                    started_at=started_at,
                    resume=True,
                )
        except RunLockError as error:
            raise GenerationError(f"run lock に失敗しました: {error}") from error

    _prepare_adapter(
        adapter=adapter,
        jobs=jobs,
        artifacts_dir=artifacts_dir,
        voices_dir=effective_voices_dir,
    )
    _verify_scenario_sources(scenario_sources)
    plans = _build_attempt_plans(
        adapter=adapter,
        jobs=jobs,
        contexts=contexts,
        requested_params=requested_params,
        profile=profile,
        tools=tools,
        phase_b=phase_b_source,
    )
    if superseded_ledger is not None:
        _validate_topup_attempt_seeds(
            artifacts_dir=artifacts_dir,
            previous=superseded_ledger,
            plans=plans,
        )
    reused = (
        None
        if phase_b_source is not None or force
        else _find_reusable_run(takes_root, source, plans)
    )
    new_ledger: dict[str, Any] | None = None
    if reused is None:
        run_id, created_at = _new_run_identity(adapter.profile.id, takes, takes_root)
        run_root = takes_root / run_id
        run_root.mkdir(parents=True, exist_ok=False)
        new_ledger = _new_ledger(
            run_id=run_id,
            created_at=created_at,
            source=source,
            plans=plans,
        )
    else:
        run_root, _discovered_ledger = reused
    try:
        with exclusive_run_lock(run_root):
            ledger_path = run_root / "ledger.json"
            if new_ledger is not None:
                ledger = new_ledger
                write_ledger_atomic(ledger_path, ledger)
            else:
                try:
                    ledger = read_ledger(ledger_path)
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    raise GenerationError(
                        f"run ledger が不正です: {ledger_path}: {error}",
                    ) from error
            return _execute_generation(
                run_root=run_root,
                ledger=ledger,
                adapter=adapter,
                plans=plans,
                requested_params=requested_params,
                profile=profile,
                tools=tools,
                started_at=started_at,
                resume=False,
            )
    except RunLockError as error:
        raise GenerationError(f"run lock に失敗しました: {error}") from error


def _execute_generation(
    *,
    run_root: Path,
    ledger: dict[str, Any],
    adapter: Adapter,
    plans: list[_AttemptPlan],
    requested_params: dict[str, Any],
    profile: PostprocessProfile,
    tools: AudioTools,
    started_at: float,
    resume: bool,
) -> GenerationSummary:
    run_id = str(ledger["run_id"])
    ledger_path = run_root / "ledger.json"
    records: list[GenerationRecord] = []
    failures: list[GenerationFailureRecord] = []
    planned: list[_AttemptPlan] = []
    for plan in plans:
        current = _attempt_for_slot(ledger, plan.slot)
        if current["status"] == "planned":
            planned.append(plan)
            continue
        if resume and current["status"] == "generation_failed":
            failures.append(
                GenerationFailureRecord(
                    scenario_id=plan.job.scenario_id,
                    line_id=plan.job.line_id,
                    take_index=plan.context.index,
                    message=str(current["generation"]["error"]),
                ),
            )
            continue
        try:
            record = _cached_record(
                run_root=run_root,
                attempt=current,
                plan=plan,
                requested_params=requested_params,
                profile=profile,
                tools=tools,
            )
        except (
            AudioProcessingError,
            GenerationError,
            OSError,
            TakeLedgerError,
            TakeSidecarError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise GenerationError(
                f"{plan.job.scenario_id}/{plan.job.line_id}/"
                f"take-{plan.context.index:04d}: {error}",
            ) from error
        records.append(record)

    if resume:
        for plan in planned:
            try:
                _remove_resume_attempt_outputs(run_root, plan)
            except OSError as error:
                raise GenerationError(
                    f"{plan.job.scenario_id}/{plan.job.line_id}/"
                    f"take-{plan.context.index:04d}: 残留出力を削除できません: {error}",
                ) from error

    for plan in planned:
        current = _attempt_for_slot(ledger, plan.slot)
        try:
            replacement, record = _generate_attempt(
                run_id=run_id,
                run_root=run_root,
                adapter=adapter,
                plan=plan,
                requested_params=requested_params,
                profile=profile,
                tools=tools,
            )
        except (
            AudioProcessingError,
            GenerationError,
            OSError,
            TakeLedgerError,
            TakeSidecarError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            _remove_attempt_outputs(run_root, plan)
            replacement = _failed_attempt(current, str(error))
            ledger = transition_attempt(
                ledger,
                slot=plan.slot,
                replacement=replacement,
            )
            write_ledger_atomic(ledger_path, ledger)
            failures.append(
                GenerationFailureRecord(
                    scenario_id=plan.job.scenario_id,
                    line_id=plan.job.line_id,
                    take_index=plan.context.index,
                    message=str(error),
                ),
            )
            continue

        try:
            next_ledger = transition_attempt(
                ledger,
                slot=plan.slot,
                replacement=replacement,
            )
            write_ledger_atomic(ledger_path, next_ledger)
        except (OSError, TakeLedgerError) as error:
            _remove_attempt_outputs(run_root, plan)
            message = f"ledger checkpoint に失敗しました: {error}"
            failed_ledger = transition_attempt(
                ledger,
                slot=plan.slot,
                replacement=_failed_attempt(current, message),
            )
            try:
                write_ledger_atomic(ledger_path, failed_ledger)
            except (OSError, TakeLedgerError) as checkpoint_error:
                raise GenerationError(
                    f"{plan.job.scenario_id}/{plan.job.line_id}/"
                    f"take-{plan.context.index:04d}: "
                    "ledger checkpoint failure を記録できませんでした: "
                    f"{checkpoint_error}",
                ) from checkpoint_error
            ledger = failed_ledger
            failures.append(
                GenerationFailureRecord(
                    scenario_id=plan.job.scenario_id,
                    line_id=plan.job.line_id,
                    take_index=plan.context.index,
                    message=message,
                ),
            )
            continue
        ledger = next_ledger
        records.append(record)

    return GenerationSummary(
        run_id=run_id,
        ledger_path=ledger_path,
        records=tuple(records),
        failures=tuple(failures),
        elapsed_seconds=time.perf_counter() - started_at,
    )


def _prepare_adapter(
    *,
    adapter: Adapter,
    jobs: list[LineJob],
    artifacts_dir: Path,
    voices_dir: Path,
) -> None:
    try:
        adapter.prepare(jobs, artifacts_dir, voices_dir)
    except Exception as error:
        raise GenerationError(f"adapter 準備に失敗しました: {error}") from error


def _validate_cli_inputs(
    *,
    scenario_id: str | None,
    line_id: str | None,
    target_lines: Sequence[tuple[str, str]] | None,
    takes: int,
    seed_base: int | None,
) -> tuple[tuple[str, str], ...] | None:
    normalized_targets: tuple[tuple[str, str], ...] | None = None
    if target_lines is not None:
        if scenario_id is not None or line_id is not None:
            raise GenerationError(
                "target_lines は scenario_id/line_id と同時に指定できません。",
            )
        if isinstance(target_lines, str | bytes) or not isinstance(
            target_lines,
            Sequence,
        ):
            raise GenerationError("target_lines は (scenario, line) の配列が必要です。")
        if not target_lines:
            raise GenerationError("target_lines は空にできません。")
        validated: list[tuple[str, str]] = []
        for index, target in enumerate(target_lines):
            if (
                not isinstance(target, tuple)
                or len(target) != 2
                or not all(isinstance(value, str) and value for value in target)
            ):
                raise GenerationError(
                    f"target_lines[{index}] は空でない (scenario, line) が必要です。",
                )
            validated.append(target)
        if len(set(validated)) != len(validated):
            raise GenerationError("target_lines に重複があります。")
        normalized_targets = tuple(sorted(validated))
    if line_id is not None and scenario_id is None:
        raise GenerationError("--line には --scenario が必要です。")
    if isinstance(takes, bool) or not isinstance(takes, int) or takes < 1:
        raise GenerationError("--takes は 1 以上の整数が必要です。")
    if seed_base is not None and (
        isinstance(seed_base, bool) or not isinstance(seed_base, int)
    ):
        raise GenerationError("--seed-base は整数または null が必要です。")
    return normalized_targets


def _load_jobs(
    scenarios_dir: Path,
    *,
    scenario_id: str | None,
    line_id: str | None,
    target_lines: tuple[tuple[str, str], ...] | None = None,
) -> tuple[list[LineJob], tuple[_ScenarioSource, ...]]:
    target_set = set(target_lines) if target_lines is not None else None
    target_scenarios = (
        {target[0] for target in target_lines}
        if target_lines is not None
        else None
    )
    documents: list[tuple[dict[str, Any], _ScenarioSource]] = []
    for scenario_path in sorted(scenarios_dir.glob("*.yaml")):
        source_bytes = scenario_path.read_bytes()
        document = yaml.safe_load(source_bytes.decode("utf-8"))
        if not isinstance(document, dict):
            raise GenerationError(
                f"シナリオが object ではありません: {scenario_path}",
            )
        if (
            target_scenarios is not None
            and document["id"] in target_scenarios
        ) or (
            target_scenarios is None
            and (scenario_id is None or document["id"] == scenario_id)
        ):
            documents.append(
                (
                    document,
                    _ScenarioSource(
                        path=scenario_path,
                        sha256=hashlib.sha256(source_bytes).hexdigest(),
                    ),
                ),
            )
    if not documents:
        raise GenerationError(f"scenario id が見つかりません: {scenario_id}")

    jobs: list[LineJob] = []
    for document, _source in documents:
        scene = {
            "id": document["id"],
            "title": document["title"],
            **document["scene"],
        }
        if "tags" in document:
            scene["tags"] = document["tags"]
        characters = {
            character["id"]: character for character in document["characters"]
        }
        for line in document["lines"]:
            identity = (str(document["id"]), str(line["id"]))
            if target_set is not None and identity not in target_set:
                continue
            if line_id is not None and line["id"] != line_id:
                continue
            jobs.append(
                LineJob(
                    scene=scene,
                    character=characters[line["character"]],
                    line=line,
                    locale=document["locale"],
                ),
            )
    if target_set is not None:
        jobs.sort(key=lambda job: (job.scenario_id, job.line_id))
        actual = {(job.scenario_id, job.line_id) for job in jobs}
        missing = sorted(target_set - actual)
        if missing:
            raise GenerationError(f"target line が見つかりません: {missing}")
    if not jobs:
        raise GenerationError(
            f"line id が見つかりません: {scenario_id}/{line_id}",
        )
    return jobs, tuple(source for _, source in documents)


def _verify_scenario_sources(sources: tuple[_ScenarioSource, ...]) -> None:
    for source in sources:
        try:
            current_sha256 = hashlib.sha256(source.path.read_bytes()).hexdigest()
        except OSError as error:
            raise GenerationError(
                f"adapter 準備中に scenario source を再検証できませんでした: "
                f"{source.path}: {error}",
            ) from error
        if current_sha256 != source.sha256:
            raise GenerationError(
                f"adapter 準備中に scenario source が変更されました: {source.path}",
            )


def _preflight_contexts(
    *,
    adapter: Adapter,
    jobs: list[LineJob],
    recipe: TakeRecipe,
    takes: int,
    seed_base: int | None,
) -> dict[tuple[str, str, int], TakeContext]:
    if takes > 1 and not recipe.supports_multiple:
        raise GenerationError(
            f"{adapter.profile.id} は複数 take に対応していません。",
        )
    if takes > 1 and recipe.seed_policy != "derived-sha256-v1":
        raise GenerationError(
            "複数 take は derived-sha256-v1 recipe が必要です。",
        )
    if recipe.seed_policy == "none":
        if seed_base is not None:
            raise GenerationError(
                "seed_policy=none の adapter には seed_base=null が必要です。",
            )
    elif seed_base is None:
        raise GenerationError(
            f"seed_policy={recipe.seed_policy} の adapter には"
            "整数の seed_base が必要です。",
        )

    contexts: dict[tuple[str, str, int], TakeContext] = {}
    seeds: dict[tuple[str, str], set[int]] = {}
    for job in jobs:
        for index in range(1, takes + 1):
            if recipe.seed_policy == "none":
                seed = None
            elif recipe.seed_policy == "fixed":
                seed = recipe.single_take_seed
            else:
                if seed_base is None:
                    raise GenerationError(
                        "derived seed recipe に整数の seed_base がありません。",
                    )
                if recipe.seed_range is None:
                    raise GenerationError("adapter recipe に seed range がありません。")
                seed = derive_seed(
                    policy_version=recipe.seed_policy,
                    seed_base=seed_base,
                    model=adapter.profile.id,
                    scenario=job.scenario_id,
                    line=job.line_id,
                    variant=VARIANT,
                    index=index,
                    seed_min=recipe.seed_range[0],
                    seed_max=recipe.seed_range[1],
                )
            context = TakeContext.create(
                index=index,
                seed=seed,
                recipe_version=recipe.version,
                sampling=dict(recipe.sampling),
            )
            require_take_context(context, recipe)
            key = (job.scenario_id, job.line_id, index)
            if key in contexts:
                raise GenerationError(f"attempt slot が重複しています: {key}")
            contexts[key] = context
            if seed is not None:
                seeds.setdefault((job.scenario_id, job.line_id), set()).add(seed)
    if takes > 1 and any(len(values) != takes for values in seeds.values()):
        raise GenerationError("同一 group 内の take seed が重複しています。")
    return contexts


def _preflight_slot_paths(
    model_id: str,
    jobs: list[LineJob],
    contexts: dict[tuple[str, str, int], TakeContext],
) -> None:
    for label, value in (
        ("model", model_id),
        ("variant", VARIANT),
        *(
            (label, value)
            for job in jobs
            for label, value in (
                ("scenario", job.scenario_id),
                ("line", job.line_id),
            )
        ),
    ):
        if (
            not value
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
        ):
            raise GenerationError(f"{label} は安全な path segment が必要です。")
    paths: set[str] = set()
    for job in jobs:
        indices = sorted(
            index
            for scenario, line, index in contexts
            if (scenario, line) == (job.scenario_id, job.line_id)
        )
        for index in indices:
            base = _attempt_base_path(
                model_id,
                job.scenario_id,
                job.line_id,
                index,
            )
            for suffix in (".wav", ".opus", ".json"):
                path = f"{base}{suffix}"
                if path in paths:
                    raise GenerationError(f"出力 path が衝突しています: {path}")
                paths.add(path)


def _phase_b_source(
    *,
    model_id: str,
    jobs: list[LineJob],
    completion_plan_sha256: str | None,
    role_epochs: Mapping[tuple[str, str], str] | None,
    run_kind: Literal["primary", "topup"] | None,
    supersedes_run_id: str | None,
    role_anchor_selection_path: Path | None,
    role_anchor_plan_sha256: str | None,
    role_anchor_selection_sha256: str | None,
) -> dict[str, Any]:
    plan_sha = _require_sha256(
        completion_plan_sha256,
        "Phase B frozen plan SHA",
    )
    if run_kind not in {"primary", "topup"}:
        raise GenerationError("Phase B run kindはprimaryまたはtopupが必要です。")
    if run_kind == "primary":
        if supersedes_run_id is not None:
            raise GenerationError("primary runにsupersedes_run_idは指定できません。")
    elif supersedes_run_id is None:
        raise GenerationError("topup runにはsupersedes_run_idが必要です。")
    else:
        _require_path_segment(supersedes_run_id, "supersedes_run_id")

    if not isinstance(role_epochs, Mapping):
        raise GenerationError("Phase B role epochsはgroup mappingが必要です。")
    expected_keys = {(job.scenario_id, job.line_id) for job in jobs}
    actual_keys: set[tuple[str, str]] = set()
    normalized_epochs: dict[tuple[str, str], str] = {}
    for key, value in role_epochs.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or not all(isinstance(item, str) and item for item in key)
        ):
            raise GenerationError(
                "Phase B role epoch keyは(scenario, line)が必要です。",
            )
        normalized_key = (key[0], key[1])
        actual_keys.add(normalized_key)
        normalized_epochs[normalized_key] = _require_sha256(
            value,
            f"Phase B role epoch {key[0]}/{key[1]}",
        )
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        unexpected = sorted(actual_keys - expected_keys)
        raise GenerationError(
            "Phase B role epochsがexact target groupsと一致しません: "
            f"missing={missing}, unexpected={unexpected}",
        )
    epochs_by_role: dict[tuple[str, str], str] = {}
    for job in jobs:
        role_key = (job.scenario_id, str(job.character["id"]))
        epoch = normalized_epochs[(job.scenario_id, job.line_id)]
        previous_epoch = epochs_by_role.setdefault(role_key, epoch)
        if previous_epoch != epoch:
            raise GenerationError(
                "同一roleのPhase B role epochがline間で漂移しています: "
                f"{role_key[0]}/{role_key[1]}",
            )

    if model_id in ANCHOR_MODELS:
        if (
            role_anchor_selection_path is None
            or role_anchor_plan_sha256 is None
            or role_anchor_selection_sha256 is None
        ):
            raise GenerationError(
                "Qwen/Irodori Phase Bにはanchor selection path/SHAと"
                "frozen plan SHAが必要です。",
            )
        anchor_plan_sha = _require_sha256(
            role_anchor_plan_sha256,
            "anchor source plan SHA",
        )
        selection_sha = _require_sha256(
            role_anchor_selection_sha256,
            "anchor selection SHA",
        )
        try:
            selection_bytes = role_anchor_selection_path.read_bytes()
            marker_bytes = role_anchor_selection_path.with_suffix(
                ".sha256",
            ).read_bytes()
        except OSError as error:
            raise GenerationError(
                f"anchor selection/markerを読めません: {error}",
            ) from error
        actual_selection_sha = hashlib.sha256(selection_bytes).hexdigest()
        if actual_selection_sha != selection_sha:
            raise GenerationError(
                "anchor selection SHAが指定値と一致しません。",
            )
        if marker_bytes != f"{selection_sha}\n".encode("ascii"):
            raise GenerationError(
                "anchor selection隣接SHA markerが一致しません。",
            )
        try:
            selection_document = json.loads(selection_bytes.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise GenerationError(
                "anchor selection JSONが不正です。",
            ) from error
        if (
            canonical_json(selection_document).encode("utf-8")
            != selection_bytes
        ):
            raise GenerationError(
                "anchor selectionはcanonical JSON bytesが必要です。",
            )
        try:
            from gaya_pipeline.completion_anchor import CompletionAnchorError
            from gaya_pipeline.increment_anchor import (
                IncrementAnchorError,
                validate_any_anchor_selection,
            )

            # 人手選抜 (role-anchor-selection-v1) と増分の機械選抜
            # (role-anchor-machine-selection-v1) の両方を受理する。
            validated_selection = validate_any_anchor_selection(
                selection_document,
            )
        except (CompletionAnchorError, IncrementAnchorError) as error:
            raise GenerationError(
                f"anchor selection contractが不正です: {error}",
            ) from error
        if validated_selection["plan_sha256"] != anchor_plan_sha:
            raise GenerationError(
                "anchor selection root plan SHAがanchor source planと一致しません。",
            )
    else:
        if any(
            value is not None
            for value in (
                role_anchor_selection_path,
                role_anchor_plan_sha256,
                role_anchor_selection_sha256,
            )
        ):
            raise GenerationError(
                "anchor selection provenanceはQwen/Irodoriだけに指定できます。",
            )
        selection_sha = None
        anchor_plan_sha = None

    target_groups = [
        {
            "model": model_id,
            "scenario": job.scenario_id,
            "line": job.line_id,
            "variant": VARIANT,
            "role_epoch_sha256": normalized_epochs[
                (job.scenario_id, job.line_id)
            ],
        }
        for job in jobs
    ]
    return {
        "protocol": PHASE_B_PROTOCOL,
        "plan_sha256": plan_sha,
        "run_kind": run_kind,
        "supersedes_run_id": supersedes_run_id,
        "anchor_selection_sha256": selection_sha,
        "anchor_plan_sha256": anchor_plan_sha,
        "target_groups": target_groups,
    }


def _phase_b_attempt_provenance(
    phase_b: Mapping[str, Any],
    *,
    model_id: str,
    scenario: str,
    line: str,
) -> dict[str, Any]:
    matches = [
        group
        for group in phase_b["target_groups"]
        if (
            group["model"],
            group["scenario"],
            group["line"],
            group["variant"],
        )
        == (model_id, scenario, line, VARIANT)
    ]
    if len(matches) != 1:
        raise GenerationError(
            "Phase B target provenanceがattempt groupとexact一致しません。",
        )
    return {
        "protocol": phase_b["protocol"],
        "plan_sha256": phase_b["plan_sha256"],
        "run_kind": phase_b["run_kind"],
        "supersedes_run_id": phase_b["supersedes_run_id"],
        "anchor_selection_sha256": phase_b["anchor_selection_sha256"],
        "anchor_plan_sha256": phase_b["anchor_plan_sha256"],
        "target_group": dict(matches[0]),
    }


def _validate_anchor_receipt(
    model_id: str,
    resolved_input: Mapping[str, Any],
    phase_b_attempt: Mapping[str, Any],
) -> None:
    if model_id not in ANCHOR_MODELS:
        return
    selected = resolved_input.get("selected_anchor")
    selected_required = (
        resolved_input.get("reference_control")
        == "selected_voice_design_anchor"
        if model_id == "qwen3-tts-12hz-1.7b"
        else resolved_input.get("reference_source") == "selected-role-anchor"
    )
    if selected is None:
        if selected_required:
            raise GenerationError(
                "selected anchor referenceにreceiptがありません。",
            )
        return
    if not selected_required:
        raise GenerationError(
            "明示reference inputにselected anchor receiptがあります。",
        )
    if not isinstance(selected, Mapping):
        raise GenerationError("selected anchor receiptはobjectが必要です。")
    expected = {
        "anchor_selection_sha256": phase_b_attempt[
            "anchor_selection_sha256"
        ],
        "anchor_plan_sha256": phase_b_attempt["anchor_plan_sha256"],
        "role_epoch_sha256": phase_b_attempt["target_group"][
            "role_epoch_sha256"
        ],
    }
    actual = {key: selected.get(key) for key in expected}
    if actual != expected:
        raise GenerationError(
            "selected anchor receiptのselection/plan/role epochが"
            "Phase B provenanceと一致しません。",
        )


def _validate_topup_source(
    *,
    artifacts_dir: Path,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    phase_b = source["phase_b"]
    supersedes_run_id = phase_b["supersedes_run_id"]
    assert isinstance(supersedes_run_id, str)
    ledger_path = artifacts_dir / "takes" / supersedes_run_id / "ledger.json"
    try:
        previous = read_ledger(ledger_path)
    except (OSError, json.JSONDecodeError, TakeLedgerError) as error:
        raise GenerationError(
            f"topup supersedes ledgerを検証できません: {ledger_path}: {error}",
        ) from error
    if previous["run_id"] != supersedes_run_id:
        raise GenerationError(
            "topup supersedes_run_idがledger identityと一致しません。",
        )
    previous_source = previous["source"]
    previous_phase_b = previous_source.get("phase_b")
    if not isinstance(previous_phase_b, Mapping):
        raise GenerationError(
            "topup supersedes ledgerにPhase B provenanceがありません。",
        )
    for key in ("protocol", "plan_sha256", "anchor_selection_sha256"):
        if previous_phase_b.get(key) != phase_b[key]:
            raise GenerationError(
                f"topup {key}がsupersedes runと一致しません。",
            )
    if previous_source["model"] != source["model"]:
        raise GenerationError("topup modelがsupersedes runと一致しません。")
    if previous_source["seed_base"] == source["seed_base"]:
        raise GenerationError(
            "topup seed_baseはsupersedes runと異なる値が必要です。",
        )
    previous_groups = {
        (
            group["model"],
            group["scenario"],
            group["line"],
            group["variant"],
        ): group["role_epoch_sha256"]
        for group in previous_phase_b["target_groups"]
    }
    current_groups = {
        (
            group["model"],
            group["scenario"],
            group["line"],
            group["variant"],
        ): group["role_epoch_sha256"]
        for group in phase_b["target_groups"]
    }
    if not set(current_groups).issubset(previous_groups):
        raise GenerationError(
            "topup target groupsがsupersedes runのexact subsetではありません。",
        )
    if any(
        previous_groups[group] != epoch
        for group, epoch in current_groups.items()
    ):
        raise GenerationError(
            "topup role epochがsupersedes runと一致しません。",
        )
    return previous


def _validate_topup_attempt_seeds(
    *,
    artifacts_dir: Path,
    previous: Mapping[str, Any],
    plans: Sequence[_AttemptPlan],
) -> None:
    run_id = previous["run_id"]
    ledger_path = artifacts_dir / "takes" / run_id / "ledger.json"
    try:
        current = read_ledger(ledger_path)
    except (OSError, json.JSONDecodeError, TakeLedgerError) as error:
        raise GenerationError(
            f"topup生成前にsupersedes ledgerを再検証できません: {error}",
        ) from error
    if current != previous:
        raise GenerationError("topup準備中にsupersedes ledgerが変更されました。")
    prior_seeds: dict[tuple[str, str, str, str], set[int]] = {}
    for attempt in previous["attempts"]:
        seed = attempt["generation"]["seed"]
        if seed is None:
            raise GenerationError(
                "topup supersedes attemptに明示seedがありません。",
            )
        group = tuple(
            attempt[key] for key in ("model", "scenario", "line", "variant")
        )
        prior_seeds.setdefault(group, set()).add(seed)
    for plan in plans:
        seed = plan.context.seed
        if seed is None:
            raise GenerationError("topup attemptに明示seedがありません。")
        if seed in prior_seeds.get(plan.slot[:4], set()):
            raise GenerationError(
                "topup seedがsupersedes runの同一groupと重複しています: "
                f"{plan.slot[:4]} seed={seed}",
            )


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise GenerationError(f"{field}は完全な小文字SHA-256が必要です。")
    return value


def _require_path_segment(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise GenerationError(f"{field}は安全なpath segmentが必要です。")
    return value


def _build_attempt_plans(
    *,
    adapter: Adapter,
    jobs: list[LineJob],
    contexts: dict[tuple[str, str, int], TakeContext],
    requested_params: dict[str, Any],
    profile: PostprocessProfile,
    tools: AudioTools,
    phase_b: dict[str, Any] | None,
) -> list[_AttemptPlan]:
    plans: list[_AttemptPlan] = []
    for job in jobs:
        matching = sorted(
            (
                context
                for (scenario, line, _), context in contexts.items()
                if (scenario, line) == (job.scenario_id, job.line_id)
            ),
            key=lambda context: context.index,
        )
        for context in matching:
            try:
                adapter_input = adapter.generation_input(job, context)
                if not isinstance(adapter_input, Mapping):
                    raise GenerationError(
                        "adapter generation inputはobjectが必要です。",
                    )
                resolved_input = dict(adapter_input)
                phase_b_attempt = None
                phase_b_provenance_sha256 = None
                if phase_b is not None:
                    if "phase_b_provenance" in resolved_input:
                        raise GenerationError(
                            "adapter generation inputに予約済み"
                            "phase_b_provenanceがあります。",
                        )
                    phase_b_attempt = _phase_b_attempt_provenance(
                        phase_b,
                        model_id=adapter.profile.id,
                        scenario=job.scenario_id,
                        line=job.line_id,
                    )
                    _validate_anchor_receipt(
                        adapter.profile.id,
                        resolved_input,
                        phase_b_attempt,
                    )
                    resolved_input["phase_b_provenance"] = phase_b_attempt
                    phase_b_provenance_sha256 = hashlib.sha256(
                        canonical_json(phase_b_attempt).encode("utf-8"),
                    ).hexdigest()
                input_sha = generation_input_sha256(
                    model_id=adapter.profile.id,
                    model_version=adapter.profile.version,
                    resolved_input=resolved_input,
                    take_context=context,
                    generation_params=requested_params,
                    postprocess={
                        "profile": profile.as_dict(),
                        "toolchain": tools.as_identity(),
                    },
                )
            except Exception as error:
                raise GenerationError(
                    f"adapter 入力構築に失敗しました: "
                    f"{job.scenario_id}/{job.line_id}/take-{context.index:04d}: "
                    f"{error}",
                ) from error
            plans.append(
                _AttemptPlan(
                    model_id=adapter.profile.id,
                    job=job,
                    context=context,
                    generation_input_sha256=input_sha,
                    phase_b_provenance=phase_b_attempt,
                    phase_b_provenance_sha256=phase_b_provenance_sha256,
                ),
            )
    return plans


def _ledger_source(
    *,
    jobs: list[LineJob],
    model_id: str,
    takes: int,
    seed_base: int | None,
    recipe: TakeRecipe,
    scenario_sources: tuple[_ScenarioSource, ...],
    phase_b: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_files = [
        {
            "path": source.path.name,
            "sha256": source.sha256,
        }
        for source in scenario_sources
    ]
    groups = [
        {
            "model": model_id,
            "scenario": job.scenario_id,
            "line": job.line_id,
            "variant": VARIANT,
        }
        for job in jobs
    ]
    result = {
        "scenario_sha256": hashlib.sha256(
            canonical_json(source_files).encode("utf-8"),
        ).hexdigest(),
        "model": model_id,
        "takes": takes,
        "seed_base": seed_base,
        "recipe_version": recipe.version,
        "groups": groups,
    }
    if phase_b is not None:
        result["phase_b"] = phase_b
    return result


def _find_reusable_run(
    takes_root: Path,
    source: dict[str, Any],
    plans: list[_AttemptPlan],
) -> tuple[Path, dict[str, Any]] | None:
    if not takes_root.exists():
        return None
    if not takes_root.is_dir():
        raise GenerationError(f"takes root が directory ではありません: {takes_root}")
    expected = {plan.slot: plan.generation_input_sha256 for plan in plans}
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    for ledger_path in sorted(takes_root.glob("*/ledger.json")):
        try:
            ledger = read_ledger(ledger_path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise GenerationError(f"run ledger が不正です: {ledger_path}: {error}") from error
        if ledger["source"] != source:
            continue
        actual = {
            tuple(
                attempt[key]
                for key in ("model", "scenario", "line", "variant")
            )
            + (attempt["take_index"],): attempt["generation_input_sha256"]
            for attempt in ledger["attempts"]
        }
        if actual == expected:
            candidates.append((str(ledger["created_at"]), ledger_path.parent, ledger))
    if not candidates:
        return None
    complete = [
        candidate
        for candidate in candidates
        if all(
            attempt["status"] not in {"planned", "generation_failed"}
            for attempt in candidate[2]["attempts"]
        )
    ]
    if not complete:
        return None
    identities = {
        tuple(
            sorted(
                (
                    *(
                        str(attempt[key])
                        for key in ("model", "scenario", "line", "variant")
                    ),
                    int(attempt["take_index"]),
                    str(attempt["take_id"]),
                )
                for attempt in ledger["attempts"]
            ),
        )
        for _, _, ledger in complete
    }
    if len(identities) != 1:
        raise GenerationError(
            "同じ生成入力に複数の異なる whole-run cache があり、"
            "自動選択できません。--force で新しい run を作成してください。",
        )
    _, run_root, ledger = max(complete, key=lambda candidate: candidate[0])
    return run_root, ledger


def _resume_run_root(takes_root: Path, resume_run_id: str) -> Path:
    _require_path_segment(resume_run_id, "resume_run_id")
    resolved_takes_root = takes_root.resolve()
    run_root = (resolved_takes_root / resume_run_id).resolve()
    if not run_root.is_relative_to(resolved_takes_root):
        raise GenerationError("resume run root が artifacts/takes の外です。")
    if not run_root.is_dir():
        raise GenerationError(f"resume run が存在しません: {resume_run_id}")
    return run_root


def _load_resume_ledger(
    *,
    run_root: Path,
    resume_run_id: str,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    ledger_path = run_root / "ledger.json"
    try:
        ledger = read_ledger(ledger_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise GenerationError(
            f"resume run ledger が不正です: {ledger_path}: {error}",
        ) from error
    if ledger["run_id"] != resume_run_id or run_root.name != resume_run_id:
        raise GenerationError("resume run ID がdirectory/ledgerと一致しません。")
    if ledger["source"] != source:
        raise GenerationError("resume run source が現在の生成authorityと一致しません。")
    statuses = {str(attempt["status"]) for attempt in ledger["attempts"]}
    allowed = {"planned", "generated", "generation_failed"}
    if not statuses <= allowed:
        raise GenerationError(
            "resume run はQC前のplanned/generated/generation_failedだけを"
            f"許可します: {sorted(statuses - allowed)}",
        )
    qc_paths = [
        run_root / name
        for name in (
            "qc-report.json",
            "manifest-v4.json",
            "candidate-set.json",
            "candidate-set.sha256",
        )
    ]
    existing_qc = [path.name for path in qc_paths if path.exists()]
    if existing_qc:
        raise GenerationError(
            f"QC状態を持つrunはresumeできません: {sorted(existing_qc)}",
        )
    return ledger


def _validate_resume_attempts(
    *,
    ledger: Mapping[str, Any],
    plans: Sequence[_AttemptPlan],
) -> None:
    expected = {
        plan.slot: (
            plan.generation_input_sha256,
            plan.phase_b_provenance_sha256,
        )
        for plan in plans
    }
    actual = {
        tuple(
            attempt[key]
            for key in ("model", "scenario", "line", "variant")
        )
        + (attempt["take_index"],): (
            attempt["generation_input_sha256"],
            attempt["phase_b_provenance_sha256"],
        )
        for attempt in ledger["attempts"]
    }
    if actual != expected:
        raise GenerationError(
            "resume run slots/input/provenance が現在のplan authorityと"
            "exact一致しません。",
        )


def _new_run_identity(
    model_id: str,
    takes: int,
    takes_root: Path,
) -> tuple[str, str]:
    now = datetime.now(UTC)
    created_at = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
    run_id = f"{now:%Y%m%dT%H%M%S%fZ}-{model_id}-n{takes}"
    if (takes_root / run_id).exists():
        raise GenerationError(f"run id が衝突しました: {run_id}")
    return run_id, created_at


def _new_ledger(
    *,
    run_id: str,
    created_at: str,
    source: dict[str, Any],
    plans: list[_AttemptPlan],
) -> dict[str, Any]:
    attempts = []
    for plan in plans:
        attempts.append(_planned_attempt_for_plan(plan))
    return {
        "format_version": 1,
        "run_id": run_id,
        "created_at": created_at,
        "source": source,
        "attempts": attempts,
    }


def _attempt_for_slot(
    ledger: dict[str, Any],
    slot: tuple[str, str, str, str, int],
) -> dict[str, Any]:
    for attempt in ledger["attempts"]:
        actual = tuple(
            attempt[key] for key in ("model", "scenario", "line", "variant")
        ) + (attempt["take_index"],)
        if actual == slot:
            return attempt
    raise GenerationError(f"ledger に attempt slot がありません: {slot}")


def _attempt_base_path(
    model: str,
    scenario: str,
    line: str,
    index: int,
) -> str:
    return (
        f"audio/{model}/{scenario}/{line}/{VARIANT}/"
        f"take-{index:04d}"
    )


def _paths_for_plan(
    run_root: Path,
    plan: _AttemptPlan,
) -> tuple[Path, Path, Path]:
    base = run_root / _attempt_base_path(
        plan.slot[0],
        plan.slot[1],
        plan.slot[2],
        plan.context.index,
    )
    return (
        base.with_suffix(".wav"),
        base.with_suffix(".opus"),
        base.with_suffix(".json"),
    )


def _temporary_paths_for_plan(
    run_root: Path,
    plan: _AttemptPlan,
) -> tuple[Path, Path, Path, Path]:
    output_dir = _paths_for_plan(run_root, plan)[0].parent
    prefix = f".take-{plan.context.index:04d}"
    return (
        output_dir / f"{prefix}.source.wav",
        output_dir / f"{prefix}.pending.wav",
        output_dir / f"{prefix}.pending.opus",
        output_dir / f"{prefix}.pending.json",
    )


def _remove_attempt_outputs(run_root: Path, plan: _AttemptPlan) -> None:
    for path in _paths_for_plan(run_root, plan):
        path.unlink(missing_ok=True)


def _remove_resume_attempt_outputs(run_root: Path, plan: _AttemptPlan) -> None:
    paths = (
        *_paths_for_plan(run_root, plan),
        *_temporary_paths_for_plan(run_root, plan),
    )
    for path in paths:
        path.unlink(missing_ok=True)


def _cached_record(
    *,
    run_root: Path,
    attempt: dict[str, Any],
    plan: _AttemptPlan,
    requested_params: dict[str, Any],
    profile: PostprocessProfile,
    tools: AudioTools,
) -> GenerationRecord:
    if attempt["status"] == "generation_failed":
        raise GenerationError("generation_failed attempt は再利用できません。")
    wav_path, opus_path, sidecar_path = _paths_for_plan(run_root, plan)
    sidecar = _read_matching_sidecar(
        sidecar_path=sidecar_path,
        wav_path=wav_path,
        opus_path=opus_path,
        plan=plan,
        run_id=str(run_root.name),
        requested_params=requested_params,
        profile=profile,
        tools=tools,
    )
    _validate_ledger_sidecar_join(attempt, sidecar, run_root)
    return _record_from_sidecar(sidecar, "skipped")


def _generate_attempt(
    *,
    run_id: str,
    run_root: Path,
    adapter: Adapter,
    plan: _AttemptPlan,
    requested_params: dict[str, Any],
    profile: PostprocessProfile,
    tools: AudioTools,
) -> tuple[dict[str, Any], GenerationRecord]:
    wav_path, opus_path, sidecar_path = _paths_for_plan(run_root, plan)
    output_dir = wav_path.parent
    source_wav, pending_wav, pending_opus, pending_sidecar = (
        _temporary_paths_for_plan(run_root, plan)
    )
    final_paths = (wav_path, opus_path, sidecar_path)
    if any(path.exists() for path in final_paths):
        raise GenerationError(
            f"新規 attempt の出力 path が既に存在します: {output_dir}",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary_paths = (source_wav, pending_wav, pending_opus, pending_sidecar)
    if any(path.exists() for path in temporary_paths):
        raise GenerationError(
            f"attempt の pending file が残っています: {output_dir}",
        )
    try:
        generation_started = time.perf_counter()
        try:
            realized_params = dict(
                adapter.generate(plan.job, plan.context, source_wav),
            )
        except Exception as error:
            raise GenerationError(f"adapter 生成に失敗しました: {error}") from error
        generation_seconds = time.perf_counter() - generation_started
        if plan.phase_b_provenance is not None:
            if "phase_b_provenance" in realized_params:
                raise GenerationError(
                    "adapter realized paramsに予約済み"
                    "phase_b_provenanceがあります。",
                )
            _validate_anchor_receipt(
                adapter.profile.id,
                realized_params,
                plan.phase_b_provenance,
            )
            realized_params["phase_b_provenance"] = dict(
                plan.phase_b_provenance,
            )
        canonical_json(realized_params)
        _audit_realized_params(plan.context, realized_params)

        source_probe = probe_audio(tools, source_wav)
        if not source_probe.codec_name.startswith("pcm_"):
            raise GenerationError("adapter 出力は PCM WAV である必要があります。")
        rtf = generation_seconds / source_probe.duration_sec
        normalized_loudness = normalize_wav(
            tools,
            source_wav,
            pending_wav,
            profile,
        )
        normalized_probe = probe_audio(tools, pending_wav)
        if (
            not normalized_probe.codec_name.startswith("pcm_")
            or normalized_probe.sample_rate_hz != profile.sample_rate_hz
            or normalized_probe.channels != profile.channels
        ):
            raise GenerationError("正規化 WAV の形式が profile と一致しません。")
        encode_opus(tools, pending_wav, pending_opus, profile)
        opus_probe = probe_audio(tools, pending_opus)
        if (
            opus_probe.codec_name != "opus"
            or opus_probe.sample_rate_hz != profile.sample_rate_hz
            or opus_probe.channels != profile.channels
        ):
            raise GenerationError("Opus の形式が profile と一致しません。")
        encoded_loudness = measure_encoded_opus(tools, pending_opus, profile)
        opus_sha = _sha256_file(pending_opus)
        attempt_requested_params = _requested_params_for_plan(
            requested_params,
            plan,
        )
        sidecar = {
            "format_version": 1,
            "run_id": run_id,
            "model": adapter.profile.id,
            "scenario": plan.job.scenario_id,
            "line": plan.job.line_id,
            "variant": VARIANT,
            "take_index": plan.context.index,
            "take_id": make_take_id(
                generation_input_sha256=plan.generation_input_sha256,
                final_opus_sha256=opus_sha,
            ),
            "generation_input_sha256": plan.generation_input_sha256,
            "wav_sha256": _sha256_file(pending_wav),
            "opus_sha256": opus_sha,
            "duration_sec": round(opus_probe.duration_sec, 6),
            "generation_seconds": round(generation_seconds, 6),
            "rtf": round(rtf, 6),
            "take": {
                "seed": plan.context.seed,
                "recipe_version": plan.context.recipe_version,
                "sampling": plan.context.sampling_dict(),
            },
            "gen_params": {
                "requested": attempt_requested_params,
                "realized": realized_params,
            },
            "postprocess": profile.as_dict(),
            "toolchain": tools.as_identity(),
            "loudness": {
                "normalized_wav": normalized_loudness.as_dict(),
                "encoded_opus": encoded_loudness.as_dict(),
            },
        }
        validate_take_sidecar(sidecar)
        pending_sidecar.write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        pending_wav.replace(wav_path)
        pending_opus.replace(opus_path)
        pending_sidecar.replace(sidecar_path)
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)

    replacement = _generated_attempt(
        _planned_attempt_for_plan(plan),
        sidecar,
        sidecar_sha256=_sha256_file(sidecar_path),
    )
    return replacement, _record_from_sidecar(sidecar, "generated")


def _audit_realized_params(
    context: TakeContext,
    realized: dict[str, Any],
) -> None:
    if context.seed is None:
        return
    if realized.get("seed") != context.seed:
        raise GenerationError(
            "adapter realized seed が要求値と一致しません: "
            f"actual={realized.get('seed')!r}, expected={context.seed!r}",
        )
    expected_sampling = context.sampling_dict()
    if realized.get("sampling") != expected_sampling:
        raise GenerationError(
            "adapter realized sampling が要求値と一致しません: "
            f"actual={realized.get('sampling')!r}, expected={expected_sampling!r}",
        )


def _planned_attempt_for_plan(plan: _AttemptPlan) -> dict[str, Any]:
    result = {
        "model": plan.slot[0],
        "scenario": plan.slot[1],
        "line": plan.slot[2],
        "variant": plan.slot[3],
        "take_index": plan.context.index,
        "generation_input_sha256": plan.generation_input_sha256,
        "generation": {
            "status": "planned",
            "seed": plan.context.seed,
            "sampling": plan.context.sampling_dict(),
        },
        "status": "planned",
    }
    if plan.phase_b_provenance_sha256 is not None:
        result["phase_b_provenance_sha256"] = (
            plan.phase_b_provenance_sha256
        )
    return result


def _generated_attempt(
    planned: dict[str, Any],
    sidecar: dict[str, Any],
    *,
    sidecar_sha256: str,
) -> dict[str, Any]:
    base = _attempt_base_path(
        str(planned["model"]),
        str(planned["scenario"]),
        str(planned["line"]),
        int(planned["take_index"]),
    )
    result = {
        "model": planned["model"],
        "scenario": planned["scenario"],
        "line": planned["line"],
        "variant": planned["variant"],
        "take_index": planned["take_index"],
        "take_id": sidecar["take_id"],
        "generation_input_sha256": planned["generation_input_sha256"],
        "generation": {
            "status": "succeeded",
            "seed": sidecar["take"]["seed"],
            "sampling": sidecar["take"]["sampling"],
            "rtf": sidecar["rtf"],
        },
        "audio": {
            "wav_path": f"{base}.wav",
            "wav_sha256": sidecar["wav_sha256"],
            "opus_path": f"{base}.opus",
            "opus_sha256": sidecar["opus_sha256"],
            "sidecar_sha256": sidecar_sha256,
        },
        "gates": {},
        "features": {"status": "unscored"},
        "status": "generated",
    }
    if "phase_b_provenance_sha256" in planned:
        result["phase_b_provenance_sha256"] = planned[
            "phase_b_provenance_sha256"
        ]
    return result


def _failed_attempt(
    planned: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    result = {
        "model": planned["model"],
        "scenario": planned["scenario"],
        "line": planned["line"],
        "variant": planned["variant"],
        "take_index": planned["take_index"],
        "generation_input_sha256": planned["generation_input_sha256"],
        "generation": {
            "status": "failed",
            "seed": planned["generation"]["seed"],
            "sampling": planned["generation"]["sampling"],
            "error": message,
        },
        "status": "generation_failed",
    }
    if "phase_b_provenance_sha256" in planned:
        result["phase_b_provenance_sha256"] = planned[
            "phase_b_provenance_sha256"
        ]
    return result


def _read_matching_sidecar(
    *,
    sidecar_path: Path,
    wav_path: Path,
    opus_path: Path,
    plan: _AttemptPlan,
    run_id: str,
    requested_params: dict[str, Any],
    profile: PostprocessProfile,
    tools: AudioTools,
) -> dict[str, Any]:
    if not sidecar_path.is_file():
        raise GenerationError(f"take sidecar がありません: {sidecar_path}")
    sidecar = validate_take_sidecar(
        json.loads(sidecar_path.read_text(encoding="utf-8")),
    )
    expected_identity = (
        run_id,
        plan.slot[0],
        plan.slot[1],
        plan.slot[2],
        plan.slot[3],
        plan.context.index,
    )
    actual_identity = (
        sidecar["run_id"],
        sidecar["model"],
        sidecar["scenario"],
        sidecar["line"],
        sidecar["variant"],
        sidecar["take_index"],
    )
    if actual_identity != expected_identity:
        raise GenerationError("take sidecar の run/slot identity が一致しません。")
    if sidecar["generation_input_sha256"] != plan.generation_input_sha256:
        raise GenerationError("take sidecar の generation input が一致しません。")
    expected_take = {
        "seed": plan.context.seed,
        "recipe_version": plan.context.recipe_version,
        "sampling": plan.context.sampling_dict(),
    }
    if sidecar["take"] != expected_take:
        raise GenerationError("take sidecar の take context が一致しません。")
    if sidecar["gen_params"]["requested"] != _requested_params_for_plan(
        requested_params,
        plan,
    ):
        raise GenerationError("take sidecar の requested parameter が一致しません。")
    if sidecar["postprocess"] != profile.as_dict():
        raise GenerationError("take sidecar の postprocess が一致しません。")
    if sidecar["toolchain"] != tools.as_identity():
        raise GenerationError("take sidecar の toolchain が一致しません。")
    if not wav_path.is_file() or not opus_path.is_file():
        raise GenerationError("take sidecar の音声 file がありません。")
    if _sha256_file(wav_path) != sidecar["wav_sha256"]:
        raise GenerationError("take WAV SHA-256 が一致しません。")
    if _sha256_file(opus_path) != sidecar["opus_sha256"]:
        raise GenerationError("take Opus SHA-256 が一致しません。")
    return sidecar


def _requested_params_for_plan(
    requested_params: Mapping[str, Any],
    plan: _AttemptPlan,
) -> dict[str, Any]:
    result = dict(requested_params)
    if plan.phase_b_provenance is None:
        return result
    if "phase_b_provenance" in result:
        raise GenerationError(
            "adapter requested paramsに予約済みphase_b_provenanceがあります。",
        )
    result["phase_b_provenance"] = dict(plan.phase_b_provenance)
    return result


def _validate_ledger_sidecar_join(
    attempt: dict[str, Any],
    sidecar: dict[str, Any],
    run_root: Path,
) -> None:
    sidecar_path = (
        run_root / attempt["audio"]["opus_path"]
    ).with_suffix(".json")
    if _sha256_file(sidecar_path) != attempt["audio"]["sidecar_sha256"]:
        raise GenerationError(
            "take sidecar SHA-256 が ledger と一致しません。",
        )
    if attempt["take_id"] != sidecar["take_id"]:
        raise GenerationError("ledger と sidecar の take_id が一致しません。")
    if attempt["generation_input_sha256"] != sidecar["generation_input_sha256"]:
        raise GenerationError("ledger と sidecar の input SHA が一致しません。")
    for kind in ("wav", "opus"):
        path = run_root / attempt["audio"][f"{kind}_path"]
        if path.as_posix() != (
            run_root / _attempt_base_path(
                sidecar["model"],
                sidecar["scenario"],
                sidecar["line"],
                sidecar["take_index"],
            )
        ).with_suffix(f".{kind}").as_posix():
            raise GenerationError(f"ledger の {kind} path が sidecar と一致しません。")
        if attempt["audio"][f"{kind}_sha256"] != sidecar[f"{kind}_sha256"]:
            raise GenerationError(f"ledger の {kind} SHA が sidecar と一致しません。")
    expected_generation = {
        "status": "succeeded",
        "seed": sidecar["take"]["seed"],
        "sampling": sidecar["take"]["sampling"],
        "rtf": sidecar["rtf"],
    }
    if attempt["generation"] != expected_generation:
        raise GenerationError("ledger と sidecar の generation provenance が一致しません。")


def _record_from_sidecar(
    sidecar: dict[str, Any],
    status: Literal["generated", "skipped"],
) -> GenerationRecord:
    return GenerationRecord(
        scenario_id=str(sidecar["scenario"]),
        line_id=str(sidecar["line"]),
        take_index=int(sidecar["take_index"]),
        status=status,
        generation_seconds=float(sidecar["generation_seconds"]),
        rtf=float(sidecar["rtf"]),
        take_id=str(sidecar["take_id"]),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
