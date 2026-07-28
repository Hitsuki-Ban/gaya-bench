from __future__ import annotations

import hashlib
import json
import math
import time
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
    scenario_id: str | None = None,
    line_id: str | None = None,
    takes: int,
    seed_base: int,
    force: bool = False,
) -> GenerationSummary:
    _validate_cli_inputs(
        scenario_id=scenario_id,
        line_id=line_id,
        takes=takes,
        seed_base=seed_base,
    )
    validation = validate_scenarios(scenarios_dir)
    if validation.problems:
        details = "\n".join(str(problem) for problem in validation.problems)
        raise GenerationError(f"シナリオ検証に失敗しました:\n{details}")

    try:
        jobs, scenario_sources = _load_jobs(
            scenarios_dir,
            scenario_id=scenario_id,
            line_id=line_id,
        )
        adapter = create_adapter(model_id)
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
    try:
        adapter.prepare(
            jobs,
            artifacts_dir,
            scenarios_dir.parent / "assets" / "voices",
        )
    except Exception as error:
        raise GenerationError(f"adapter 準備に失敗しました: {error}") from error

    _verify_scenario_sources(scenario_sources)
    plans = _build_attempt_plans(
        adapter=adapter,
        jobs=jobs,
        contexts=contexts,
        requested_params=requested_params,
        profile=profile,
        tools=tools,
    )
    source = _ledger_source(
        jobs=jobs,
        model_id=adapter.profile.id,
        takes=takes,
        seed_base=seed_base,
        recipe=recipe,
        scenario_sources=scenario_sources,
    )
    takes_root = artifacts_dir / "takes"
    reused = None if force else _find_reusable_run(takes_root, source, plans)
    if reused is None:
        run_id, created_at = _new_run_identity(adapter.profile.id, takes, takes_root)
        run_root = takes_root / run_id
        ledger = _new_ledger(
            run_id=run_id,
            created_at=created_at,
            source=source,
            plans=plans,
        )
        ledger_path = run_root / "ledger.json"
        write_ledger_atomic(ledger_path, ledger)
    else:
        run_root, ledger = reused
        run_id = str(ledger["run_id"])
        ledger_path = run_root / "ledger.json"

    records: list[GenerationRecord] = []
    failures: list[GenerationFailureRecord] = []
    for plan in plans:
        current = _attempt_for_slot(ledger, plan.slot)
        if current["status"] != "planned":
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
            continue

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


def _validate_cli_inputs(
    *,
    scenario_id: str | None,
    line_id: str | None,
    takes: int,
    seed_base: int,
) -> None:
    if line_id is not None and scenario_id is None:
        raise GenerationError("--line には --scenario が必要です。")
    if isinstance(takes, bool) or not isinstance(takes, int) or takes < 1:
        raise GenerationError("--takes は 1 以上の整数が必要です。")
    if isinstance(seed_base, bool) or not isinstance(seed_base, int):
        raise GenerationError("--seed-base は整数が必要です。")


def _load_jobs(
    scenarios_dir: Path,
    *,
    scenario_id: str | None,
    line_id: str | None,
) -> tuple[list[LineJob], tuple[_ScenarioSource, ...]]:
    documents: list[tuple[dict[str, Any], _ScenarioSource]] = []
    for scenario_path in sorted(scenarios_dir.glob("*.yaml")):
        source_bytes = scenario_path.read_bytes()
        document = yaml.safe_load(source_bytes.decode("utf-8"))
        if not isinstance(document, dict):
            raise GenerationError(
                f"シナリオが object ではありません: {scenario_path}",
            )
        if scenario_id is None or document["id"] == scenario_id:
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
    seed_base: int,
) -> dict[tuple[str, str, int], TakeContext]:
    if takes > 1 and not recipe.supports_multiple:
        raise GenerationError(
            f"{adapter.profile.id} は複数 take に対応していません。",
        )
    if takes > 1 and recipe.seed_policy != "derived-sha256-v1":
        raise GenerationError(
            "複数 take は derived-sha256-v1 recipe が必要です。",
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


def _build_attempt_plans(
    *,
    adapter: Adapter,
    jobs: list[LineJob],
    contexts: dict[tuple[str, str, int], TakeContext],
    requested_params: dict[str, Any],
    profile: PostprocessProfile,
    tools: AudioTools,
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
                resolved_input = adapter.generation_input(job, context)
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
                ),
            )
    return plans


def _ledger_source(
    *,
    jobs: list[LineJob],
    model_id: str,
    takes: int,
    seed_base: int,
    recipe: TakeRecipe,
    scenario_sources: tuple[_ScenarioSource, ...],
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
    return {
        "scenario_sha256": hashlib.sha256(
            canonical_json(source_files).encode("utf-8"),
        ).hexdigest(),
        "model": model_id,
        "takes": takes,
        "seed_base": seed_base,
        "recipe_version": recipe.version,
        "groups": groups,
    }


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
        attempts.append(
            {
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
            },
        )
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


def _remove_attempt_outputs(run_root: Path, plan: _AttemptPlan) -> None:
    for path in _paths_for_plan(run_root, plan):
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
    source_wav = output_dir / f".take-{plan.context.index:04d}.source.wav"
    pending_wav = output_dir / f".take-{plan.context.index:04d}.pending.wav"
    pending_opus = output_dir / f".take-{plan.context.index:04d}.pending.opus"
    pending_sidecar = output_dir / f".take-{plan.context.index:04d}.pending.json"
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
                "requested": requested_params,
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
    return {
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
    return {
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


def _failed_attempt(
    planned: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    return {
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
    if sidecar["gen_params"]["requested"] != requested_params:
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
