from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from gaya_pipeline.completion_listen import (
    CompletionListeningError,
    CompletionScenarioAuthority,
    CompletionSourceResolution,
    _load_completion_scenario_authority,
    _local_audio_path,
    completion_group_sha256,
    resolve_completion_sources,
)
from gaya_pipeline.completion_plan import (
    CompletionPlan,
    require_production_completion_plan,
)
from gaya_pipeline.completion_selection import (
    AUTO_SELECTION_POLICY,
    GENDER_SCREENING_PROTOCOL,
    canonical_completion_decision_bytes,
    validate_completion_decision,
)
from gaya_pipeline.curation import (
    CurationError,
    build_candidate_set,
    canonical_candidate_set_bytes,
)
from gaya_pipeline.take_identity import canonical_json


class CompletionAutoDecisionError(RuntimeError):
    pass


BATCH_INPUT_KIND = "phase-b-auto-selection-input-v1"
BATCH_OUTPUT_KIND = "phase-b-auto-selection-ranking-v1"
BATCH_POLICY = "qc-content-then-pasqa-then-duration-v1"
QUALITY_SIGNALS_PROTOCOL = "role-quality-signals-v1"
PASQA_VOCAB_SHA256 = (
    "0f816a4669c0cb22d9af80b7cb20414995971039f4a6db4330098c26218d6841"
)
PASQA_MAX_DURATION_SEC = 10.0
EXPECTED_GROUP_COUNT = 597
EXPECTED_CANDIDATE_COUNT = 2_307
SMALL_KANA_PRONUNCIATION = {
    "ヲ": "オ",
    "ァ": "ア",
    "ィ": "イ",
    "ゥ": "ウ",
    "ェ": "エ",
    "ォ": "オ",
    "ヵ": "カ",
    "ヶ": "ケ",
    "ヮ": "ワ",
}


@dataclass(frozen=True)
class CompletionAutoDecisionSummary:
    output_dir: Path
    decision_sha256: str
    ranking_report_sha256: str
    quality_signals_sha256: str
    group_count: int
    candidate_count: int
    review_required_count: int


def canonical_completion_quality_signals_bytes(value: Any) -> bytes:
    return canonical_json(validate_completion_quality_signals(value)).encode("utf-8")


def validate_completion_quality_signals(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "format_version",
        "protocol",
        "plan_sha256",
        "decision_sha256",
        "groups",
    }:
        raise CompletionAutoDecisionError("quality signals exact rootが不正です。")
    if value["format_version"] != 1 or value["protocol"] != QUALITY_SIGNALS_PROTOCOL:
        raise CompletionAutoDecisionError("quality signals protocolが不正です。")
    for field in ("plan_sha256", "decision_sha256"):
        _require_sha256(value[field], f"quality signals.{field}")
    groups = value["groups"]
    if not isinstance(groups, list) or len(groups) != EXPECTED_GROUP_COUNT:
        raise CompletionAutoDecisionError("quality signalsはexact 597 groupが必要です。")
    identities: set[tuple[str, str, str, str]] = set()
    normalized_groups: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict) or set(group) != {
            "model",
            "scenario",
            "line",
            "variant",
            "protocol",
            "expected_gender",
            "median_f0_hz",
            "status",
            "signal",
            "qc_report_sha256",
        }:
            raise CompletionAutoDecisionError("quality signal group exact contractが不正です。")
        identity = _group_key(group)
        if identity in identities or any(not item for item in identity):
            raise CompletionAutoDecisionError("quality signal group identityが不正です。")
        identities.add(identity)
        if group["protocol"] != GENDER_SCREENING_PROTOCOL:
            raise CompletionAutoDecisionError("quality signal screening protocolが不正です。")
        expected_gender = group["expected_gender"]
        status = group["status"]
        signal = group["signal"]
        median = group["median_f0_hz"]
        if expected_gender not in {"female", "male", "neutral"}:
            raise CompletionAutoDecisionError("quality signal expected genderが不正です。")
        if status not in {"pass", "review_required", "not_applicable"}:
            raise CompletionAutoDecisionError("quality signal statusが不正です。")
        if median is not None and (
            isinstance(median, bool)
            or not isinstance(median, (int, float))
            or not math.isfinite(float(median))
            or float(median) <= 0
        ):
            raise CompletionAutoDecisionError("quality signal median F0が不正です。")
        expected_signal = None
        expected_status = "pass"
        if expected_gender == "neutral":
            expected_status = "not_applicable"
        elif median is None:
            expected_status = "review_required"
            expected_signal = "gender_f0_unavailable"
        elif expected_gender == "female" and float(median) < 165.0:
            expected_status = "review_required"
            expected_signal = "gender_f0_below_expected"
        elif expected_gender == "male" and float(median) > 180.0:
            expected_status = "review_required"
            expected_signal = "gender_f0_above_expected"
        if status != expected_status or signal != expected_signal:
            raise CompletionAutoDecisionError("quality signal status/signalがF0 policyと不一致です。")
        _require_sha256(group["qc_report_sha256"], "quality signal.qc_report_sha256")
        normalized_groups.append(dict(group))
    return {
        "format_version": 1,
        "protocol": QUALITY_SIGNALS_PROTOCOL,
        "plan_sha256": value["plan_sha256"],
        "decision_sha256": value["decision_sha256"],
        "groups": normalized_groups,
    }


def create_completion_auto_decision(
    *,
    plan: CompletionPlan,
    primary_run_ids: Sequence[str],
    topup_run_ids: Sequence[str],
    anchor_selection_path: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    pasqa_project_dir: Path,
    pasqa_model_dir: Path,
    output_dir: Path,
) -> CompletionAutoDecisionSummary:
    require_production_completion_plan(plan)
    try:
        return _create_completion_auto_decision(
            plan=plan,
            primary_run_ids=primary_run_ids,
            topup_run_ids=topup_run_ids,
            anchor_selection_path=anchor_selection_path,
            artifacts_dir=artifacts_dir,
            scenarios_dir=scenarios_dir,
            voices_dir=voices_dir,
            pasqa_project_dir=pasqa_project_dir,
            pasqa_model_dir=pasqa_model_dir,
            output_dir=output_dir,
        )
    except CompletionAutoDecisionError:
        raise
    except (
        CompletionListeningError,
        CurationError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise CompletionAutoDecisionError(
            f"auto decision入力契約が不正です: {error}"
        ) from error


def _create_completion_auto_decision(
    *,
    plan: CompletionPlan,
    primary_run_ids: Sequence[str],
    topup_run_ids: Sequence[str],
    anchor_selection_path: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    pasqa_project_dir: Path,
    pasqa_model_dir: Path,
    output_dir: Path,
) -> CompletionAutoDecisionSummary:
    if output_dir.exists():
        raise CompletionAutoDecisionError(
            f"auto decision outputは既存pathを拒否します: {output_dir}"
        )
    parent = output_dir.resolve().parent
    if not parent.is_dir():
        raise CompletionAutoDecisionError(
            f"auto decision outputの親directoryがありません: {parent}"
        )
    pasqa_project = pasqa_project_dir.resolve()
    if not (pasqa_project / "pyproject.toml").is_file() or not (
        pasqa_project / "uv.lock"
    ).is_file():
        raise CompletionAutoDecisionError(
            f"PASQA project契約がありません: {pasqa_project}"
        )
    model_dir = pasqa_model_dir.resolve()
    vocab_path = model_dir / "vocab.txt"
    vocab_bytes = _read_bytes(vocab_path, "PASQA vocab")
    if hashlib.sha256(vocab_bytes).hexdigest() != PASQA_VOCAB_SHA256:
        raise CompletionAutoDecisionError("PASQA vocab SHA-256が固定値と不一致です。")
    vocab = _load_vocab(vocab_bytes)

    scenario_authority = _load_completion_scenario_authority(
        scenarios_dir=scenarios_dir.resolve(),
        voices_dir=voices_dir.resolve(),
        plan=plan,
    )
    resolution = resolve_completion_sources(
        plan=plan,
        primary_run_ids=primary_run_ids,
        topup_run_ids=topup_run_ids,
        anchor_selection_path=anchor_selection_path,
        artifacts_dir=artifacts_dir,
        scenario_authority=scenario_authority,
    )
    candidate_set = _replacement_candidate_set(
        plan=plan,
        resolution=resolution,
        scenario_authority=scenario_authority,
    )
    candidate_set_bytes = canonical_candidate_set_bytes(candidate_set)
    candidate_set_sha256 = hashlib.sha256(candidate_set_bytes).hexdigest()
    candidates_by_group = _candidates_by_group(candidate_set["candidates"])
    if (
        len(candidates_by_group) != EXPECTED_GROUP_COUNT
        or len(candidate_set["candidates"]) != EXPECTED_CANDIDATE_COUNT
    ):
        raise CompletionAutoDecisionError(
            "auto decisionはexact 597 group / 2,307 candidateが必要です。"
        )
    qc_attempts = _load_qc_attempts(resolution)

    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=parent))
    try:
        ranking_dir = temporary / "ranking"
        audio_dir = ranking_dir / "audio"
        audio_dir.mkdir(parents=True)
        batch_groups: list[dict[str, Any]] = []
        for identity, candidates in sorted(candidates_by_group.items()):
            mora_tokens = _group_mora_tokens(
                identity=identity,
                candidates=candidates,
                qc_attempts=qc_attempts,
                vocab=vocab,
            )
            batch_candidates: list[dict[str, Any]] = []
            source = resolution.group_sources[identity]
            for candidate in candidates:
                duration = float(candidate["duration_sec"])
                pasqa_audio_path: str | None = None
                if duration <= PASQA_MAX_DURATION_SEC:
                    source_audio = _local_audio_path(source.root, candidate)
                    staged = audio_dir / f"{candidate['take_id']}.wav"
                    _convert_to_pasqa_wav(source_audio, staged)
                    pasqa_audio_path = staged.relative_to(ranking_dir).as_posix()
                batch_candidates.append(
                    {
                        "take_id": candidate["take_id"],
                        "source_audio_sha256": candidate["sha256"],
                        "gate_content": candidate["gate"]["content"],
                        "duration_seconds": duration,
                        "pasqa_audio_path": pasqa_audio_path,
                    }
                )
            batch_groups.append(
                {
                    "group": {
                        "model_id": identity[0],
                        "scenario_id": identity[1],
                        "line_id": identity[2],
                        "variant": identity[3],
                    },
                    "mora_tokens": mora_tokens,
                    "candidates": batch_candidates,
                }
            )
        batch_input = {
            "format_version": 1,
            "kind": BATCH_INPUT_KIND,
            "groups": batch_groups,
        }
        batch_input_bytes = canonical_json(batch_input).encode("utf-8")
        batch_input_path = ranking_dir / "input.json"
        _write_new(batch_input_path, batch_input_bytes)
        ranking_report_path = ranking_dir / "report.json"
        _run_pasqa_batch(
            pasqa_project_dir=pasqa_project,
            pasqa_model_dir=model_dir,
            input_path=batch_input_path,
            output_path=ranking_report_path,
        )
        ranking_report_bytes = _read_bytes(ranking_report_path, "ranking report")
        ranking_report = _validate_ranking_report(
            json.loads(ranking_report_bytes),
            batch_input_sha256=hashlib.sha256(batch_input_bytes).hexdigest(),
            candidates_by_group=candidates_by_group,
        )
        ranking_report_sha256 = hashlib.sha256(ranking_report_bytes).hexdigest()

        decision_groups = _build_decision_groups(
            plan=plan,
            resolution=resolution,
            scenario_authority=scenario_authority,
            candidate_set=candidate_set,
            candidates_by_group=candidates_by_group,
            qc_attempts=qc_attempts,
            ranking_report=ranking_report,
        )
        decision = validate_completion_decision(
            {
                "format_version": 1,
                "protocol": "role-baseline-decision-v1",
                "plan_sha256": plan.plan_id,
                "anchor_selection_sha256": resolution.anchor_selection_sha256,
                "candidate_set_sha256": candidate_set_sha256,
                "ranking_report_sha256": ranking_report_sha256,
                "groups": decision_groups,
            }
        )
        decision_bytes = canonical_completion_decision_bytes(decision)
        decision_sha256 = hashlib.sha256(decision_bytes).hexdigest()
        quality_signals = _quality_signals_document(
            plan=plan,
            decision=decision,
            decision_sha256=decision_sha256,
        )
        quality_signals_bytes = canonical_completion_quality_signals_bytes(
            quality_signals,
        )
        quality_signals_sha256 = hashlib.sha256(quality_signals_bytes).hexdigest()

        _write_new(temporary / "role-baseline-decision-v1.json", decision_bytes)
        _write_new(
            temporary / "role-baseline-decision-v1.sha256",
            decision_sha256.encode("ascii"),
        )
        _write_new(
            temporary / "role-quality-signals-v1.json",
            quality_signals_bytes,
        )
        _write_new(
            temporary / "role-quality-signals-v1.sha256",
            quality_signals_sha256.encode("ascii"),
        )
        temporary.replace(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    review_required_count = sum(
        group["screening"]["status"] == "review_required"
        for group in decision["groups"]
    )
    return CompletionAutoDecisionSummary(
        output_dir=output_dir,
        decision_sha256=decision_sha256,
        ranking_report_sha256=ranking_report_sha256,
        quality_signals_sha256=quality_signals_sha256,
        group_count=len(decision["groups"]),
        candidate_count=sum(len(group["candidates"]) for group in decision["groups"]),
        review_required_count=review_required_count,
    )


def _replacement_candidate_set(
    *,
    plan: CompletionPlan,
    resolution: CompletionSourceResolution,
    scenario_authority: CompletionScenarioAuthority,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    models: dict[str, dict[str, Any]] = {}
    for identity, run in sorted(resolution.group_sources.items()):
        group_candidates = [
            dict(candidate)
            for candidate in run.manifest["candidates"]
            if _group_key(candidate) == identity
        ]
        minimum = plan.policy_for_model(identity[0]).minimum_eligible_candidates
        if len(group_candidates) < minimum:
            raise CompletionAutoDecisionError(
                f"mechanical-pass candidateが{minimum}件未満です: {identity}"
            )
        candidates.extend(group_candidates)
        models[run.model] = dict(run.manifest["models"][0])
    candidates.sort(key=lambda item: (_group_key(item), int(item["take_index"])))
    return build_candidate_set(
        scenario_sha256=scenario_authority.scenario_sha256,
        lines=list(scenario_authority.lines),
        models=[models[model] for model in sorted(models)],
        candidates=candidates,
        failures=[],
    )


def _load_qc_attempts(
    resolution: CompletionSourceResolution,
) -> dict[tuple[str, str, str, str, int], tuple[dict[str, Any], str]]:
    result: dict[tuple[str, str, str, str, int], tuple[dict[str, Any], str]] = {}
    reports: dict[str, dict[tuple[str, str, str, str, int], dict[str, Any]]] = {}
    for run in {source.run_id: source for source in resolution.group_sources.values()}.values():
        path = run.root / "qc-report.json"
        raw = _read_bytes(path, "QC report")
        if hashlib.sha256(raw).hexdigest() != run.qc_report_sha256:
            raise CompletionAutoDecisionError(f"QC report SHAが不一致です: {run.run_id}")
        document = json.loads(raw)
        attempts = document.get("attempts")
        if not isinstance(attempts, list):
            raise CompletionAutoDecisionError(f"QC report attemptsが不正です: {run.run_id}")
        by_slot: dict[tuple[str, str, str, str, int], dict[str, Any]] = {}
        for attempt in attempts:
            slot = (
                str(attempt["model"]),
                str(attempt["scenario"]),
                str(attempt["line"]),
                str(attempt["variant"]),
                int(attempt["take_index"]),
            )
            if slot in by_slot:
                raise CompletionAutoDecisionError(
                    f"同一QC report内でattemptが重複しています: {run.run_id}: {slot}"
                )
            by_slot[slot] = dict(attempt)
        reports[run.run_id] = by_slot
    for identity, run in resolution.group_sources.items():
        for candidate in run.manifest["candidates"]:
            if _group_key(candidate) != identity:
                continue
            slot = (*identity, int(candidate["take_index"]))
            attempt = reports[run.run_id].get(slot)
            if attempt is None:
                raise CompletionAutoDecisionError(
                    f"final sourceのQC attemptがありません: {run.run_id}: {slot}"
                )
            if slot in result:
                raise CompletionAutoDecisionError(f"final QC attemptが重複しています: {slot}")
            result[slot] = (attempt, run.qc_report_sha256)
    return result


def _group_mora_tokens(
    *,
    identity: tuple[str, str, str, str],
    candidates: Sequence[Mapping[str, Any]],
    qc_attempts: Mapping[
        tuple[str, str, str, str, int],
        tuple[dict[str, Any], str],
    ],
    vocab: frozenset[str],
) -> list[str]:
    readings: set[str] = set()
    for candidate in candidates:
        slot = (*identity, int(candidate["take_index"]))
        attempt, _report_sha = qc_attempts[slot]
        expected = attempt["content"]["expected_reading"]
        normalized = expected["normalized"]
        if not isinstance(normalized, str) or not normalized:
            raise CompletionAutoDecisionError(f"QC expected readingがありません: {slot}")
        readings.add(normalized)
    if len(readings) != 1:
        raise CompletionAutoDecisionError(f"group内のQC expected readingが不一致です: {identity}")
    return _tokenize_mora(next(iter(readings)), vocab)


def _tokenize_mora(text: str, vocab: frozenset[str]) -> list[str]:
    ordered = sorted(vocab, key=lambda token: (-len(token), token))
    result: list[str] = []
    offset = 0
    while offset < len(text):
        matched = next(
            (token for token in ordered if text.startswith(token, offset)),
            None,
        )
        if matched is not None:
            result.append(matched)
            offset += len(matched)
            continue
        normalized = SMALL_KANA_PRONUNCIATION.get(text[offset])
        if normalized is None or normalized not in vocab:
            raise CompletionAutoDecisionError(
                f"PASQA mora vocabへ明示正規化できません: {text[offset:]}"
            )
        result.append(normalized)
        offset += 1
    if not result or len(result) > 128:
        raise CompletionAutoDecisionError("PASQA mora token数は1..128が必要です。")
    return result


def _convert_to_pasqa_wav(source: Path, destination: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise CompletionAutoDecisionError("ffmpegが見つかりません。")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(source),
        "-map_metadata",
        "-1",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise CompletionAutoDecisionError(
            f"PASQA WAV変換に失敗しました: {source}: {completed.stderr.strip()}"
        )
    with wave.open(str(destination), "rb") as stream:
        if (
            stream.getnchannels() != 1
            or stream.getframerate() != 16_000
            or stream.getsampwidth() != 2
            or not 1_040 <= stream.getnframes() <= 160_000
        ):
            raise CompletionAutoDecisionError(
                f"PASQA WAV契約を満たしません: {destination}"
            )


def _run_pasqa_batch(
    *,
    pasqa_project_dir: Path,
    pasqa_model_dir: Path,
    input_path: Path,
    output_path: Path,
) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise CompletionAutoDecisionError("uvが見つかりません。")
    command = [
        uv,
        "run",
        "--project",
        str(pasqa_project_dir),
        "--locked",
        "--no-sync",
        "gaya-pasqa",
        "rank-batch",
        "--model-dir",
        str(pasqa_model_dir),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
    ]
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        command,
        cwd=pasqa_project_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise CompletionAutoDecisionError(
            "PASQA batch rankingに失敗しました: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )


def _validate_ranking_report(
    document: Any,
    *,
    batch_input_sha256: str,
    candidates_by_group: Mapping[
        tuple[str, str, str, str],
        Sequence[Mapping[str, Any]],
    ],
) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != {
        "format_version",
        "kind",
        "usage",
        "ranking_policy",
        "groups",
        "provenance",
    }:
        raise CompletionAutoDecisionError("ranking report root契約が不正です。")
    if (
        document["format_version"] != 1
        or document["kind"] != BATCH_OUTPUT_KIND
        or document["ranking_policy"] != BATCH_POLICY
        or document["usage"] != "same-group-ranking-only_no_release_rejection"
    ):
        raise CompletionAutoDecisionError("ranking report identityが不正です。")
    provenance = document["provenance"]
    if (
        not isinstance(provenance, dict)
        or provenance.get("input_sha256") != batch_input_sha256
    ):
        raise CompletionAutoDecisionError("ranking report input SHAが不一致です。")
    groups = document["groups"]
    if not isinstance(groups, list) or len(groups) != len(candidates_by_group):
        raise CompletionAutoDecisionError("ranking report group数が不一致です。")
    normalized: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for group in groups:
        identity = _ranking_group_identity(group["group"])
        if identity in normalized or identity not in candidates_by_group:
            raise CompletionAutoDecisionError(f"ranking report groupが不正です: {identity}")
        if group.get("ranking_policy") != BATCH_POLICY:
            raise CompletionAutoDecisionError(f"ranking policyが不一致です: {identity}")
        rankings = group.get("rankings")
        if not isinstance(rankings, list):
            raise CompletionAutoDecisionError(f"ranking listが不正です: {identity}")
        expected = {str(item["take_id"]): item for item in candidates_by_group[identity]}
        actual_ids = {str(item["take_id"]) for item in rankings}
        if actual_ids != set(expected) or [item.get("rank") for item in rankings] != list(
            range(1, len(rankings) + 1)
        ):
            raise CompletionAutoDecisionError(f"ranking candidates/rankが不一致です: {identity}")
        for item in rankings:
            candidate = expected[str(item["take_id"])]
            if (
                item.get("source_audio_sha256") != candidate["sha256"]
                or item.get("gate_content") != candidate["gate"]["content"]
                or float(item.get("duration_seconds")) != float(candidate["duration_sec"])
            ):
                raise CompletionAutoDecisionError(f"ranking candidate bindingが不一致です: {identity}")
            pasqa = item.get("pasqa")
            if pasqa is not None:
                score = pasqa.get("score") if isinstance(pasqa, dict) else None
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(float(score))
                ):
                    raise CompletionAutoDecisionError(f"PASQA scoreが不正です: {identity}")
        normalized[identity] = dict(group)
    return {"groups": normalized}


def _build_decision_groups(
    *,
    plan: CompletionPlan,
    resolution: CompletionSourceResolution,
    scenario_authority: CompletionScenarioAuthority,
    candidate_set: Mapping[str, Any],
    candidates_by_group: Mapping[
        tuple[str, str, str, str],
        Sequence[Mapping[str, Any]],
    ],
    qc_attempts: Mapping[
        tuple[str, str, str, str, int],
        tuple[dict[str, Any], str],
    ],
    ranking_report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    lines = {
        (str(line["scenario"]), str(line["line"])): line
        for line in candidate_set["lines"]
    }
    result: list[dict[str, Any]] = []
    rankings_by_group = ranking_report["groups"]
    for identity, candidates in sorted(candidates_by_group.items()):
        ranking = rankings_by_group[identity]["rankings"]
        candidates_by_take = {str(item["take_id"]): item for item in candidates}
        selected_take_id = str(ranking[0]["take_id"])
        selected = candidates_by_take[selected_take_id]
        selected_attempt, qc_report_sha256 = qc_attempts[
            (*identity, int(selected["take_index"]))
        ]
        context = scenario_authority.contexts[(identity[1], identity[2])]
        screening = _gender_screening(
            expected_gender=str(context["role"]["gender"]),
            attempt=selected_attempt,
            qc_report_sha256=qc_report_sha256,
        )
        minimum = plan.policy_for_model(identity[0]).minimum_eligible_candidates
        source = resolution.group_sources[identity]
        line = lines[(identity[1], identity[2])]
        result.append(
            {
                "model": identity[0],
                "scenario": identity[1],
                "line": identity[2],
                "variant": identity[3],
                "role_epoch_sha256": resolution.expected_role_epochs[identity],
                "group_sha256": completion_group_sha256(
                    identity=identity,
                    line=line,
                    context=context,
                    role_epoch_sha256=resolution.expected_role_epochs[identity],
                    source_run_id=source.run_id,
                    minimum_eligible_candidates=minimum,
                    candidates=candidates,
                ),
                "authority": {
                    "type": "auto-selected",
                    "policy_version": AUTO_SELECTION_POLICY,
                    "minimum_eligible_candidates": minimum,
                    "gate_policy_version": "take-gates-v2",
                },
                "candidates": [
                    {
                        "take_id": item["take_id"],
                        "path": candidates_by_take[str(item["take_id"])]["path"],
                        "audio_sha256": candidates_by_take[str(item["take_id"])]["sha256"],
                        "gate": dict(candidates_by_take[str(item["take_id"])]["gate"]),
                        "rank": int(item["rank"]),
                        "pasqa_score": (
                            float(item["pasqa"]["score"])
                            if item["pasqa"] is not None
                            else None
                        ),
                        "duration_sec": float(item["duration_seconds"]),
                    }
                    for item in ranking
                ],
                "decision": {"type": "selected", "take_id": selected_take_id},
                "screening": screening,
            }
        )
    return result


def _gender_screening(
    *,
    expected_gender: str,
    attempt: Mapping[str, Any],
    qc_report_sha256: str,
) -> dict[str, Any]:
    median = attempt["content"]["prosody"]["f0"]["median_hz"]
    if median is not None:
        median = float(median)
    if expected_gender == "neutral":
        status = "not_applicable"
        signal = None
    elif median is None:
        status = "review_required"
        signal = "gender_f0_unavailable"
    elif expected_gender == "female" and median < 165.0:
        status = "review_required"
        signal = "gender_f0_below_expected"
    elif expected_gender == "male" and median > 180.0:
        status = "review_required"
        signal = "gender_f0_above_expected"
    else:
        status = "pass"
        signal = None
    return {
        "protocol": GENDER_SCREENING_PROTOCOL,
        "expected_gender": expected_gender,
        "median_f0_hz": median,
        "status": status,
        "signal": signal,
        "qc_report_sha256": qc_report_sha256,
    }


def _quality_signals_document(
    *,
    plan: CompletionPlan,
    decision: Mapping[str, Any],
    decision_sha256: str,
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "protocol": QUALITY_SIGNALS_PROTOCOL,
        "plan_sha256": plan.plan_id,
        "decision_sha256": decision_sha256,
        "groups": [
            {
                "model": group["model"],
                "scenario": group["scenario"],
                "line": group["line"],
                "variant": group["variant"],
                **dict(group["screening"]),
            }
            for group in decision["groups"]
        ],
    }


def _candidates_by_group(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        result.setdefault(_group_key(candidate), []).append(dict(candidate))
    return result


def _ranking_group_identity(value: Any) -> tuple[str, str, str, str]:
    if not isinstance(value, dict) or set(value) != {
        "model_id",
        "scenario_id",
        "line_id",
        "variant",
    }:
        raise CompletionAutoDecisionError("ranking group identityが不正です。")
    return (
        str(value["model_id"]),
        str(value["scenario_id"]),
        str(value["line_id"]),
        str(value["variant"]),
    )


def _group_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value["model"]),
        str(value["scenario"]),
        str(value["line"]),
        str(value["variant"]),
    )


def _load_vocab(raw: bytes) -> frozenset[str]:
    tokens = [line for line in raw.decode("utf-8").splitlines() if line]
    if not tokens or len(tokens) != len(set(tokens)):
        raise CompletionAutoDecisionError("PASQA vocabが空または重複しています。")
    return frozenset(tokens)


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise CompletionAutoDecisionError(f"{label}を読めません: {path}") from error


def _require_sha256(value: Any, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CompletionAutoDecisionError(f"{label}はlowercase SHA-256が必要です。")


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as error:
        raise CompletionAutoDecisionError(f"既存fileを上書きしません: {path}") from error
