"""公開済みbaselineへ1 model分を足し込むrelease finalizer。

#174 の `completion_release` は 597 replacement + 691 inherited = 1,288 に
SHA凍結されている。増分では **公開済みrelease bundleをそのまま inherited とし**、
新規modelの161 groupだけを追加して 1,288 + 161 = 1,449 の manifest v4 を作る。
音声objectはcontent-addressedなので既存1,288分は一切再upload対象にならない。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from gaya_pipeline.completion_auto import (
    CompletionAutoDecisionError,
    canonical_completion_quality_signals_bytes,
    validate_completion_quality_signals,
)
from gaya_pipeline.completion_listen import (
    CompletionListeningError,
    CompletionSourceResolution,
    _load_completion_scenario_authority,
    _local_audio_path,
    resolve_completion_sources,
)
from gaya_pipeline.completion_release import (
    CompletionReleaseBundle,
    _build_candidate_set,
    _read_marker_bound,
    _write_release,
)
from gaya_pipeline.completion_selection import (
    FORMAT_VERSION as SELECTION_FORMAT_VERSION,
    PROTOCOL as SELECTION_PROTOCOL,
    canonical_completion_decision_bytes,
    canonical_completion_selection_bytes,
    validate_completion_decision,
    validate_completion_selection,
)
from gaya_pipeline.curation import CurationError, canonical_candidate_set_bytes
from gaya_pipeline.increment_anchor import (
    IncrementAnchorError,
    load_machine_anchor_selection,
)
from gaya_pipeline.increment_plan import (
    BASE_GROUPS,
    FINAL_GROUPS,
    INCREMENT_GROUPS,
    IncrementPlan,
    IncrementPlanError,
)
from gaya_pipeline.take_identity import canonical_json
from gaya_pipeline.take_manifest_v4 import TakeManifestError, validate_manifest_v4


class IncrementReleaseError(RuntimeError):
    pass


RELEASE_PROTOCOL = "role-increment-release-v1"
RELEASE_FORMAT_VERSION = 1
BASE_QUALITY_SIGNAL_GROUPS = 597
FINAL_QUALITY_SIGNAL_GROUPS = BASE_QUALITY_SIGNAL_GROUPS + INCREMENT_GROUPS

@dataclass(frozen=True)
class IncrementReleaseSummary:
    output_dir: Path
    manifest_sha256: str
    candidate_set_sha256: str
    selection_sha256: str
    quality_signals_sha256: str
    candidate_count: int
    selected_count: int
    increment_candidate_count: int
    model_count: int


def finalize_increment_release(
    *,
    plan: IncrementPlan,
    base_release_dir: Path,
    decision_path: Path,
    quality_signals_path: Path,
    primary_run_ids: Sequence[str],
    topup_run_ids: Sequence[str],
    anchor_selection_path: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    output_dir: Path,
) -> IncrementReleaseSummary:
    try:
        return _finalize_increment_release(
            plan=plan,
            base_release_dir=base_release_dir,
            decision_path=decision_path,
            quality_signals_path=quality_signals_path,
            primary_run_ids=primary_run_ids,
            topup_run_ids=topup_run_ids,
            anchor_selection_path=anchor_selection_path,
            artifacts_dir=artifacts_dir,
            scenarios_dir=scenarios_dir,
            voices_dir=voices_dir,
            output_dir=output_dir,
        )
    except IncrementReleaseError:
        raise
    except (
        CompletionAutoDecisionError,
        CompletionListeningError,
        CurationError,
        IncrementAnchorError,
        IncrementPlanError,
        TakeManifestError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise IncrementReleaseError(
            f"increment release入力契約が不正です: {error}",
        ) from error


def _finalize_increment_release(
    *,
    plan: IncrementPlan,
    base_release_dir: Path,
    decision_path: Path,
    quality_signals_path: Path,
    primary_run_ids: Sequence[str],
    topup_run_ids: Sequence[str],
    anchor_selection_path: Path,
    artifacts_dir: Path,
    scenarios_dir: Path,
    voices_dir: Path,
    output_dir: Path,
) -> IncrementReleaseSummary:
    for path, label in (
        (base_release_dir, "base release"),
        (decision_path, "increment decision"),
        (quality_signals_path, "increment quality signals"),
        (anchor_selection_path, "anchor selection"),
        (artifacts_dir, "artifacts"),
        (scenarios_dir, "scenarios"),
        (voices_dir, "voices"),
        (output_dir, "increment release output"),
    ):
        if not path.is_absolute():
            raise IncrementReleaseError(f"{label}は絶対pathが必要です: {path}")
    if output_dir.exists():
        raise IncrementReleaseError(
            f"increment release outputは既存pathを拒否します: {output_dir}",
        )
    if not output_dir.resolve().parent.is_dir():
        raise IncrementReleaseError("increment release outputの親directoryが存在しません。")

    base = load_base_release(base_release_dir, plan=plan)

    scenario_authority = _load_completion_scenario_authority(
        scenarios_dir=scenarios_dir,
        voices_dir=voices_dir,
        plan=plan,
    )
    resolution = resolve_completion_sources(
        plan=plan,
        primary_run_ids=primary_run_ids,
        topup_run_ids=topup_run_ids,
        anchor_selection_path=anchor_selection_path,
        artifacts_dir=artifacts_dir,
        scenario_authority=scenario_authority,
        primary_models=frozenset({plan.model}),
        anchor_loader=lambda path, current: load_machine_anchor_selection(
            path,
            plan=current,
        ),
    )

    increment_candidates = _increment_candidates(plan, resolution)
    increment_candidate_set = _build_candidate_set(
        candidates=increment_candidates,
        models=[run.manifest["models"][0] for run in resolution.runs],
        scenario_authority=scenario_authority,
    )
    increment_candidate_sha256 = hashlib.sha256(
        canonical_candidate_set_bytes(increment_candidate_set),
    ).hexdigest()

    decision_raw = _read_bytes(decision_path, "increment decision")
    decision_sha256 = hashlib.sha256(decision_raw).hexdigest()
    decision = validate_completion_decision(_decode_json(decision_raw, decision_path))
    if decision_raw != canonical_completion_decision_bytes(decision):
        raise IncrementReleaseError("increment decisionはcanonical bytesが必要です。")
    if (
        decision["plan_sha256"] != plan.plan_id
        or decision["anchor_selection_sha256"] != resolution.anchor_selection_sha256
        or decision["candidate_set_sha256"] != increment_candidate_sha256
    ):
        raise IncrementReleaseError(
            "increment decisionのplan/anchor/candidate-set bindingが不一致です。",
        )
    increment_identities = {target.identity for target in plan.targets}
    if len(increment_identities) != INCREMENT_GROUPS:
        raise IncrementReleaseError(
            f"increment planはexact {INCREMENT_GROUPS} groupが必要です。",
        )
    decision_groups = {_group_key(group): group for group in decision["groups"]}
    if set(decision_groups) != increment_identities:
        raise IncrementReleaseError(
            f"increment decisionはexact {INCREMENT_GROUPS} groupが必要です。",
        )
    base_identities = {
        _group_key(group) for group in base.selection["groups"]
    }
    if increment_identities & base_identities:
        raise IncrementReleaseError(
            "increment groupが公開済みbaseと衝突しています。",
        )

    quality_signals_raw = _read_bytes(
        quality_signals_path,
        "increment quality signals",
    )
    increment_quality_signals = validate_completion_quality_signals(
        _decode_json(quality_signals_raw, quality_signals_path),
        expected_group_count=INCREMENT_GROUPS,
    )
    if quality_signals_raw != canonical_completion_quality_signals_bytes(
        increment_quality_signals,
        expected_group_count=INCREMENT_GROUPS,
    ):
        raise IncrementReleaseError(
            "increment quality signalsはcanonical bytesが必要です。",
        )
    if (
        increment_quality_signals["plan_sha256"] != plan.plan_id
        or increment_quality_signals["decision_sha256"] != decision_sha256
    ):
        raise IncrementReleaseError(
            "increment quality signalsのplan/decision bindingが不一致です。",
        )
    signal_identities = {
        _group_key(group) for group in increment_quality_signals["groups"]
    }
    if signal_identities != increment_identities:
        raise IncrementReleaseError(
            "increment quality signalsはdecisionと同じgroup集合が必要です。",
        )

    base_models = [dict(model) for model in base.manifest["models"]]
    increment_model = dict(resolution.runs[0].manifest["models"][0])
    if increment_model["id"] != plan.model:
        raise IncrementReleaseError("increment run manifestのmodelがplanと不一致です。")
    if any(model["id"] == plan.model for model in base_models):
        raise IncrementReleaseError(
            f"増分modelは公開済みbaseに存在してはいけません: {plan.model}",
        )
    final_candidates = sorted(
        [
            *(dict(candidate) for candidate in base.manifest["candidates"]),
            *increment_candidates,
        ],
        key=lambda item: (_group_key(item), int(item["take_index"])),
    )
    final_candidate_set = _build_candidate_set(
        candidates=final_candidates,
        models=[*base_models, increment_model],
        scenario_authority=scenario_authority,
    )
    if (
        final_candidate_set["scenario_sha256"]
        != base.candidate_set["scenario_sha256"]
        or final_candidate_set["lines"] != base.candidate_set["lines"]
    ):
        raise IncrementReleaseError(
            "increment releaseのscenario snapshotが公開済みbaseと一致しません。",
        )
    candidate_set_bytes = canonical_candidate_set_bytes(final_candidate_set)
    candidate_set_sha256 = hashlib.sha256(candidate_set_bytes).hexdigest()

    selection_groups = merge_selection_groups(
        base_groups=base.selection["groups"],
        decision_groups=decision_groups,
    )
    selection = validate_completion_selection(
        {
            "format_version": SELECTION_FORMAT_VERSION,
            "protocol": SELECTION_PROTOCOL,
            "plan_sha256": plan.plan_id,
            "anchor_selection_sha256": resolution.anchor_selection_sha256,
            "candidate_set_sha256": candidate_set_sha256,
            "ranking_report_sha256": decision["ranking_report_sha256"],
            "groups": selection_groups,
        },
    )
    if len(selection["groups"]) != FINAL_GROUPS:
        raise IncrementReleaseError(
            f"final selectionはexact {FINAL_GROUPS} groupが必要です。",
        )
    selection_bytes = canonical_completion_selection_bytes(selection)
    selection_sha256 = hashlib.sha256(selection_bytes).hexdigest()
    curations = build_curations(
        selection_groups=selection["groups"],
        selection_sha256=selection_sha256,
    )
    manifest = validate_manifest_v4(
        {
            "format_version": 4,
            "generated_at": max(
                [str(base.manifest["generated_at"])]
                + [str(run.manifest["generated_at"]) for run in resolution.runs],
            ),
            "candidate_set_sha256": candidate_set_sha256,
            "models": final_candidate_set["models"],
            "candidates": final_candidates,
            "curations": curations,
            "failures": [],
        },
    )
    manifest_bytes = canonical_json(manifest).encode("utf-8")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

    quality_signals = validate_completion_quality_signals(
        {
            "format_version": 1,
            "protocol": increment_quality_signals["protocol"],
            "plan_sha256": plan.plan_id,
            "decision_sha256": decision_sha256,
            "groups": [
                *(dict(group) for group in base.quality_signals["groups"]),
                *(dict(group) for group in increment_quality_signals["groups"]),
            ],
        },
        expected_group_count=FINAL_QUALITY_SIGNAL_GROUPS,
    )
    quality_signals_bytes = canonical_completion_quality_signals_bytes(
        quality_signals,
        expected_group_count=FINAL_QUALITY_SIGNAL_GROUPS,
    )
    quality_signals_sha256 = hashlib.sha256(quality_signals_bytes).hexdigest()

    provenance = _build_provenance(
        plan=plan,
        base=base,
        resolution=resolution,
        manifest_sha256=manifest_sha256,
        candidate_set_sha256=candidate_set_sha256,
        selection_sha256=selection_sha256,
        decision_sha256=decision_sha256,
        increment_candidate_set_sha256=increment_candidate_sha256,
        quality_signals_sha256=quality_signals_sha256,
    )
    _write_release(
        output_dir=output_dir,
        manifest_bytes=manifest_bytes,
        candidate_set_bytes=candidate_set_bytes,
        selection_bytes=selection_bytes,
        quality_signals_bytes=quality_signals_bytes,
        provenance_bytes=canonical_json(provenance).encode("utf-8"),
    )
    validate_increment_release(
        release_dir=output_dir,
        artifacts_dir=artifacts_dir,
    )
    return IncrementReleaseSummary(
        output_dir=output_dir,
        manifest_sha256=manifest_sha256,
        candidate_set_sha256=candidate_set_sha256,
        selection_sha256=selection_sha256,
        quality_signals_sha256=quality_signals_sha256,
        candidate_count=len(final_candidates),
        selected_count=len(selection["groups"]),
        increment_candidate_count=len(increment_candidates),
        model_count=len(manifest["models"]),
    )


def merge_selection_groups(
    *,
    base_groups: Sequence[Mapping[str, Any]],
    decision_groups: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """公開済みselectionをそのまま継承し、増分decisionを追加する。

    `group_sha256` / `screening` は decision 固有の field なので release
    selection からは落とす (#174 finalizer と同じ投影)。
    """

    base = [dict(group) for group in base_groups]
    base_identities = {_group_key(group) for group in base}
    increment_identities = set(decision_groups)
    if base_identities & increment_identities:
        raise IncrementReleaseError(
            "increment groupが公開済みbaseと衝突しています。",
        )
    return [
        *base,
        *(
            {
                key: value
                for key, value in decision_groups[identity].items()
                if key not in {"group_sha256", "screening"}
            }
            for identity in sorted(increment_identities)
        ),
    ]


def build_curations(
    *,
    selection_groups: Sequence[Mapping[str, Any]],
    selection_sha256: str,
) -> list[dict[str, Any]]:
    return [
        {
            "model": group["model"],
            "scenario": group["scenario"],
            "line": group["line"],
            "variant": group["variant"],
            "decision": "selected",
            "take_id": group["decision"]["take_id"],
            "curation_sha256": selection_sha256,
        }
        for group in selection_groups
    ]


@dataclass(frozen=True)
class BaseReleaseBundle:
    root: Path
    manifest: dict[str, Any]
    candidate_set: dict[str, Any]
    selection: dict[str, Any]
    quality_signals: dict[str, Any]
    provenance: dict[str, Any]
    manifest_sha256: str
    candidate_set_sha256: str
    selection_sha256: str
    quality_signals_sha256: str
    provenance_sha256: str


def load_base_release(
    base_release_dir: Path,
    *,
    plan: IncrementPlan,
) -> BaseReleaseBundle:
    """公開済み #174 release bundleをplanのbase bindingどおりに読む。"""

    root = base_release_dir.resolve(strict=True)
    if not root.is_dir():
        raise IncrementReleaseError("base releaseはdirectoryが必要です。")
    manifest, manifest_sha = _read_marker_bound(
        root / "manifest-v4.json",
        root / "manifest-v4.sha256",
        "base manifest",
    )
    candidate_set, candidate_sha = _read_marker_bound(
        root / "candidate-set.json",
        root / "candidate-set.sha256",
        "base candidate set",
    )
    selection, selection_sha = _read_marker_bound(
        root / "selection.json",
        root / "selection.sha256",
        "base selection",
    )
    quality_signals, quality_signals_sha = _read_marker_bound(
        root / "quality-signals.json",
        root / "quality-signals.sha256",
        "base quality signals",
    )
    provenance, provenance_sha = _read_marker_bound(
        root / "release-provenance.json",
        root / "release-provenance.sha256",
        "base release provenance",
    )
    if (
        manifest_sha != plan.base_manifest_sha256
        or candidate_sha != plan.base_candidate_set_sha256
        or selection_sha != plan.base_selection_sha256
        or quality_signals_sha != plan.base_quality_signals_sha256
        or provenance_sha != plan.base_release_provenance_sha256
    ):
        raise IncrementReleaseError(
            "base release bundleのSHAがincrement planのbase bindingと一致しません。",
        )
    normalized_manifest = validate_manifest_v4(manifest)
    normalized_selection = validate_completion_selection(selection)
    normalized_quality_signals = validate_completion_quality_signals(
        quality_signals,
        expected_group_count=BASE_QUALITY_SIGNAL_GROUPS,
    )
    if len(normalized_selection["groups"]) != BASE_GROUPS:
        raise IncrementReleaseError(
            f"base selectionはexact {BASE_GROUPS} groupが必要です。",
        )
    if normalized_manifest["candidate_set_sha256"] != candidate_sha:
        raise IncrementReleaseError(
            "base manifestのcandidate_set_sha256がbundleと一致しません。",
        )
    return BaseReleaseBundle(
        root=root,
        manifest=normalized_manifest,
        candidate_set=candidate_set,
        selection=normalized_selection,
        quality_signals=normalized_quality_signals,
        provenance=provenance,
        manifest_sha256=manifest_sha,
        candidate_set_sha256=candidate_sha,
        selection_sha256=selection_sha,
        quality_signals_sha256=quality_signals_sha,
        provenance_sha256=provenance_sha,
    )


def validate_increment_release(
    *,
    release_dir: Path,
    artifacts_dir: Path | None = None,
    source_audit_path: Path | None = None,
) -> CompletionReleaseBundle:
    """publishが受理する増分releaseの完全性契約。

    `source_audit_path` は `completion publish` と同じ引数列で呼べるように
    受け取るだけで、増分固有の判定には使わない (base側の公開監査は
    base release provenance の SHA 束縛で継承される)。
    """

    try:
        root = release_dir.resolve(strict=True)
        if not root.is_dir():
            raise IncrementReleaseError("release_dirはdirectoryが必要です。")
        manifest, manifest_sha = _read_marker_bound(
            root / "manifest-v4.json",
            root / "manifest-v4.sha256",
            "manifest",
        )
        candidate_set, candidate_sha = _read_marker_bound(
            root / "candidate-set.json",
            root / "candidate-set.sha256",
            "candidate set",
        )
        selection_doc, selection_sha = _read_marker_bound(
            root / "selection.json",
            root / "selection.sha256",
            "selection",
        )
        quality_signals_doc, quality_signals_sha = _read_marker_bound(
            root / "quality-signals.json",
            root / "quality-signals.sha256",
            "quality signals",
        )
        provenance, provenance_sha = _read_marker_bound(
            root / "release-provenance.json",
            root / "release-provenance.sha256",
            "release provenance",
        )
        manifest = validate_manifest_v4(manifest)
        selection = validate_completion_selection(selection_doc)
        quality_signals = validate_completion_quality_signals(
            quality_signals_doc,
            expected_group_count=FINAL_QUALITY_SIGNAL_GROUPS,
        )
        if canonical_json(manifest).encode("utf-8") != (
            root / "manifest-v4.json"
        ).read_bytes():
            raise IncrementReleaseError("manifestはcanonical bytesが必要です。")
        if canonical_candidate_set_bytes(candidate_set) != (
            root / "candidate-set.json"
        ).read_bytes():
            raise IncrementReleaseError("candidate setはcanonical bytesが必要です。")
        if canonical_completion_selection_bytes(selection) != (
            root / "selection.json"
        ).read_bytes():
            raise IncrementReleaseError("selectionはcanonical bytesが必要です。")
        if canonical_completion_quality_signals_bytes(
            quality_signals,
            expected_group_count=FINAL_QUALITY_SIGNAL_GROUPS,
        ) != (root / "quality-signals.json").read_bytes():
            raise IncrementReleaseError("quality signalsはcanonical bytesが必要です。")
        _validate_provenance(
            provenance,
            manifest_sha=manifest_sha,
            candidate_sha=candidate_sha,
            selection_sha=selection_sha,
            quality_signals_sha=quality_signals_sha,
        )
        if (
            manifest["candidate_set_sha256"] != candidate_sha
            or selection["candidate_set_sha256"] != candidate_sha
        ):
            raise IncrementReleaseError("candidate set SHA bindingが不一致です。")
        if len(selection["groups"]) != FINAL_GROUPS:
            raise IncrementReleaseError(
                f"increment releaseはexact {FINAL_GROUPS} selected groupが必要です。",
            )
        if manifest["failures"]:
            raise IncrementReleaseError("increment releaseにfailureは許可されません。")
        _validate_manifest_joins(
            manifest=manifest,
            candidate_set=candidate_set,
            selection=selection,
            quality_signals=quality_signals,
            provenance=provenance,
        )
        if artifacts_dir is not None:
            _verify_increment_audio(
                manifest=manifest,
                provenance=provenance,
                artifacts_dir=artifacts_dir,
            )
        _ = provenance_sha
        return CompletionReleaseBundle(
            root=root,
            manifest=manifest,
            candidate_set=candidate_set,
            selection=selection,
            quality_signals=quality_signals,
            provenance=provenance,
        )
    except IncrementReleaseError:
        raise
    except (
        CompletionAutoDecisionError,
        CurationError,
        TakeManifestError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise IncrementReleaseError(
            f"increment release契約が不正です: {error}",
        ) from error


_PROVENANCE_ROOT_FIELDS = {
    "format_version",
    "protocol",
    "plan_sha256",
    "model",
    "anchor_selection_sha256",
    "increment_candidate_set_sha256",
    "manifest_sha256",
    "candidate_set_sha256",
    "selection_sha256",
    "decision_sha256",
    "quality_signals_sha256",
    "counts",
    "base",
    "source_runs",
}
_PROVENANCE_COUNTS_FIELDS = {
    "base_groups",
    "increment_groups",
    "selected_groups",
    "failures",
}
_PROVENANCE_BASE_FIELDS = {
    "manifest_sha256",
    "candidate_set_sha256",
    "selection_sha256",
    "quality_signals_sha256",
    "release_provenance_sha256",
}


def _build_provenance(
    *,
    plan: IncrementPlan,
    base: BaseReleaseBundle,
    resolution: CompletionSourceResolution,
    manifest_sha256: str,
    candidate_set_sha256: str,
    selection_sha256: str,
    decision_sha256: str,
    increment_candidate_set_sha256: str,
    quality_signals_sha256: str,
) -> dict[str, Any]:
    source_runs: list[dict[str, Any]] = []
    for run in resolution.runs:
        effective_groups = {
            identity
            for identity, source in resolution.group_sources.items()
            if source is run
        }
        candidates: list[dict[str, Any]] = []
        for candidate in run.manifest["candidates"]:
            if _group_key(candidate) not in effective_groups:
                continue
            local_path = _local_audio_path(run.root, candidate)
            candidates.append(
                {
                    "take_id": candidate["take_id"],
                    "path": candidate["path"],
                    "audio_sha256": candidate["sha256"],
                    "run_relative_path": local_path.relative_to(run.root).as_posix(),
                    "size_bytes": local_path.stat().st_size,
                },
            )
        source_runs.append(
            {
                "model": run.model,
                "run_id": run.run_id,
                "kind": run.kind,
                "supersedes_run_id": run.supersedes_run_id,
                "ledger_sha256": run.ledger_sha256,
                "qc_report_sha256": run.qc_report_sha256,
                "manifest_sha256": run.manifest_sha256,
                "candidate_set_sha256": run.candidate_set_sha256,
                "effective_groups": [
                    {
                        "model": identity[0],
                        "scenario": identity[1],
                        "line": identity[2],
                        "variant": identity[3],
                        "role_epoch_sha256": resolution.expected_role_epochs[identity],
                    }
                    for identity in sorted(effective_groups)
                ],
                "candidates": sorted(candidates, key=lambda item: item["take_id"]),
            },
        )
    return {
        "format_version": RELEASE_FORMAT_VERSION,
        "protocol": RELEASE_PROTOCOL,
        "plan_sha256": plan.plan_id,
        "model": plan.model,
        "anchor_selection_sha256": resolution.anchor_selection_sha256,
        "increment_candidate_set_sha256": increment_candidate_set_sha256,
        "manifest_sha256": manifest_sha256,
        "candidate_set_sha256": candidate_set_sha256,
        "selection_sha256": selection_sha256,
        "decision_sha256": decision_sha256,
        "quality_signals_sha256": quality_signals_sha256,
        "counts": {
            "base_groups": BASE_GROUPS,
            "increment_groups": INCREMENT_GROUPS,
            "selected_groups": FINAL_GROUPS,
            "failures": 0,
        },
        "base": {
            "manifest_sha256": base.manifest_sha256,
            "candidate_set_sha256": base.candidate_set_sha256,
            "selection_sha256": base.selection_sha256,
            "quality_signals_sha256": base.quality_signals_sha256,
            "release_provenance_sha256": base.provenance_sha256,
        },
        "source_runs": sorted(source_runs, key=lambda item: item["run_id"]),
    }


def _validate_provenance(
    value: Any,
    *,
    manifest_sha: str,
    candidate_sha: str,
    selection_sha: str,
    quality_signals_sha: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _PROVENANCE_ROOT_FIELDS:
        raise IncrementReleaseError("increment release provenance rootが不正です。")
    if (
        value["format_version"] != RELEASE_FORMAT_VERSION
        or value["protocol"] != RELEASE_PROTOCOL
    ):
        raise IncrementReleaseError("increment release provenance identityが不正です。")
    if (
        value["manifest_sha256"] != manifest_sha
        or value["candidate_set_sha256"] != candidate_sha
        or value["selection_sha256"] != selection_sha
        or value["quality_signals_sha256"] != quality_signals_sha
    ):
        raise IncrementReleaseError(
            "increment release provenanceのdocument SHAが不一致です。",
        )
    counts = value["counts"]
    if not isinstance(counts, dict) or set(counts) != _PROVENANCE_COUNTS_FIELDS:
        raise IncrementReleaseError("increment release provenance countsが不正です。")
    if (
        counts["base_groups"] != BASE_GROUPS
        or counts["increment_groups"] != INCREMENT_GROUPS
        or counts["selected_groups"] != FINAL_GROUPS
        or counts["failures"] != 0
    ):
        raise IncrementReleaseError(
            f"increment release countsは{BASE_GROUPS}+{INCREMENT_GROUPS}"
            f"={FINAL_GROUPS}が必要です。",
        )
    base = value["base"]
    if not isinstance(base, dict) or set(base) != _PROVENANCE_BASE_FIELDS:
        raise IncrementReleaseError("increment release provenance baseが不正です。")
    for field in sorted(_PROVENANCE_BASE_FIELDS):
        _require_sha256(base[field], f"release provenance.base.{field}")
    source_runs = value["source_runs"]
    if not isinstance(source_runs, list) or not source_runs:
        raise IncrementReleaseError("increment release provenance source_runsが不正です。")
    models = {str(run["model"]) for run in source_runs}
    if models != {str(value["model"])}:
        raise IncrementReleaseError(
            "increment release provenanceは単一modelのsource runだけが必要です。",
        )
    effective = [
        (
            str(group["model"]),
            str(group["scenario"]),
            str(group["line"]),
            str(group["variant"]),
        )
        for run in source_runs
        for group in run["effective_groups"]
    ]
    if len(effective) != INCREMENT_GROUPS or len(set(effective)) != INCREMENT_GROUPS:
        raise IncrementReleaseError(
            f"increment release provenanceはexact {INCREMENT_GROUPS} effective "
            "groupが必要です。",
        )
    return dict(value)


def _validate_manifest_joins(
    *,
    manifest: Mapping[str, Any],
    candidate_set: Mapping[str, Any],
    selection: Mapping[str, Any],
    quality_signals: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> None:
    if (
        candidate_set["models"] != manifest["models"]
        or candidate_set["candidates"] != manifest["candidates"]
        or candidate_set["failures"] != manifest["failures"]
    ):
        raise IncrementReleaseError("candidate setとmanifestが一致しません。")
    candidates_by_take = {
        candidate["take_id"]: candidate for candidate in manifest["candidates"]
    }
    curations = {
        _group_key(curation): curation for curation in manifest["curations"]
    }
    if (
        len(curations) != len(manifest["curations"])
        or len(curations) != FINAL_GROUPS
    ):
        raise IncrementReleaseError(
            f"manifest curationsはexact {FINAL_GROUPS}件が必要です。",
        )
    for group in selection["groups"]:
        identity = _group_key(group)
        curation = curations.get(identity)
        if curation is None or curation["decision"] != "selected":
            raise IncrementReleaseError(f"selected curationがありません: {identity}")
        take_id = group["decision"]["take_id"]
        if curation["take_id"] != take_id:
            raise IncrementReleaseError(f"curation take_idが不一致です: {identity}")
        candidate = candidates_by_take.get(take_id)
        if candidate is None or _group_key(candidate) != identity:
            raise IncrementReleaseError(
                f"selected take がmanifest candidateにありません: {identity}",
            )
    selected_identities = {_group_key(group) for group in selection["groups"]}
    signal_identities = {
        _group_key(group) for group in quality_signals["groups"]
    }
    if not signal_identities <= selected_identities:
        raise IncrementReleaseError(
            "quality signal groupがselected groupに存在しません。",
        )
    provenance_identities = {
        (
            str(group["model"]),
            str(group["scenario"]),
            str(group["line"]),
            str(group["variant"]),
        )
        for run in provenance["source_runs"]
        for group in run["effective_groups"]
    }
    if not provenance_identities <= selected_identities:
        raise IncrementReleaseError(
            "source run effective groupがselected groupに存在しません。",
        )


def _verify_increment_audio(
    *,
    manifest: Mapping[str, Any],
    provenance: Mapping[str, Any],
    artifacts_dir: Path,
) -> None:
    takes_root = artifacts_dir / "takes"
    by_take = {
        candidate["take_id"]: candidate for candidate in manifest["candidates"]
    }
    for run in provenance["source_runs"]:
        run_root = takes_root / str(run["run_id"])
        for item in run["candidates"]:
            candidate = by_take.get(item["take_id"])
            if candidate is None:
                raise IncrementReleaseError(
                    f"source run candidateがmanifestにありません: {item['take_id']}",
                )
            path = run_root / Path(str(item["run_relative_path"]))
            try:
                payload = path.read_bytes()
            except OSError as error:
                raise IncrementReleaseError(
                    f"increment audioを読めません: {path}",
                ) from error
            if (
                hashlib.sha256(payload).hexdigest() != item["audio_sha256"]
                or item["audio_sha256"] != candidate["sha256"]
                or len(payload) != int(item["size_bytes"])
            ):
                raise IncrementReleaseError(
                    f"increment audioのSHA/sizeが不一致です: {path}",
                )


def _increment_candidates(
    plan: IncrementPlan,
    resolution: CompletionSourceResolution,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    minimum = plan.policy_for_model(plan.model).minimum_eligible_candidates
    for identity, run in sorted(resolution.group_sources.items()):
        group_candidates = [
            dict(candidate)
            for candidate in run.manifest["candidates"]
            if _group_key(candidate) == identity
        ]
        if len(group_candidates) < minimum:
            raise IncrementReleaseError(
                "increment groupはeligible candidateがmodel policyの"
                f"{minimum}件以上必要です: {identity}",
            )
        result.extend(group_candidates)
    return result


def _group_key(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value["model"]),
        str(value["scenario"]),
        str(value["line"]),
        str(value["variant"]),
    )


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise IncrementReleaseError(f"{field}はlowercase SHA-256が必要です。")
    return value


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise IncrementReleaseError(f"{label}を読めません: {path}") from error


def _decode_json(raw: bytes, path: Path) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IncrementReleaseError(f"JSONが不正です: {path}: {error}") from error


__all__ = [
    "BASE_QUALITY_SIGNAL_GROUPS",
    "FINAL_QUALITY_SIGNAL_GROUPS",
    "RELEASE_PROTOCOL",
    "BaseReleaseBundle",
    "IncrementReleaseError",
    "IncrementReleaseSummary",
    "build_curations",
    "merge_selection_groups",
    "finalize_increment_release",
    "load_base_release",
    "validate_increment_release",
]
