from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import gaya_pipeline.completion_listen as completion_listen
from gaya_pipeline.completion_listen import (
    CompletionScenarioAuthority,
    CompletionSourceResolution,
    CompletionSourceRun,
    build_completion_listening_bundle,
)
from gaya_pipeline.completion_release import _validate_decision_against_sources
from gaya_pipeline.completion_selection import (
    canonical_completion_decision_bytes,
    validate_completion_decision,
)
from gaya_pipeline.take_identity import canonical_json, make_take_id


MODEL_GROUP_COUNTS = {
    "aivisspeech-kohaku": 25,
    "chatterbox-multilingual-v3": 13,
    "cosyvoice3-0.5b-2512": 14,
    "gpt-sovits-v2-pro-plus": 37,
    "irodori-tts-600m-v3-voicedesign": 161,
    "qwen3-tts-12hz-1.7b": 161,
    "supertonic-3": 25,
    "voxcpm2": 161,
}
ANCHOR_MODELS = frozenset(
    {"irodori-tts-600m-v3-voicedesign", "qwen3-tts-12hz-1.7b"},
)


def sha(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def main(root: Path) -> None:
    root.mkdir()
    output_dir = root / "bundle"
    revisions = {
        model: f"cross-contract-v3:{model}" for model in MODEL_GROUP_COUNTS
    }
    roles = _build_roles()
    anchor_plan_sha = sha("synthetic-anchor-plan-v1")
    anchor_candidate_sha = sha("synthetic-anchor-candidate-set-v1")
    anchor_selection = _build_anchor_selection(
        roles=roles,
        revisions=revisions,
        anchor_plan_sha=anchor_plan_sha,
        anchor_candidate_sha=anchor_candidate_sha,
    )
    anchor_bytes = canonical_bytes(anchor_selection)
    anchor_sha = sha(anchor_bytes)
    targets = _build_targets(roles)
    plan_document = _build_plan(
        revisions=revisions,
        roles=roles,
        targets=targets,
        anchor_plan_sha=anchor_plan_sha,
        anchor_candidate_sha=anchor_candidate_sha,
        anchor_selection_sha=anchor_sha,
    )
    plan_bytes = canonical_bytes(plan_document)
    plan_sha = sha(plan_bytes)
    plan_path = (root / "fixture-plan.json").resolve()
    plan_path.write_bytes(plan_bytes)
    anchor_path = (root / "fixture-anchor.json").resolve()
    anchor_path.write_bytes(anchor_bytes)
    anchor_path.with_suffix(".sha256").write_text(anchor_sha, encoding="ascii")

    anchor_epochs = {
        (group["model"], group["scenario"], group["character"]): group[
            "role_epoch_sha256"
        ]
        for group in anchor_selection["groups"]
    }
    role_by_scenario = {role["scenario"]: role for role in roles}
    lines: dict[tuple[str, str], dict[str, str]] = {}
    contexts: dict[tuple[str, str], dict[str, object]] = {}
    role_epochs: dict[tuple[str, str, str, str], str] = {}
    group_sources: dict[
        tuple[str, str, str, str], CompletionSourceRun
    ] = {}
    runs: list[CompletionSourceRun] = []

    for model in MODEL_GROUP_COUNTS:
        run_id = f"cross-contract-{model}"
        run_root = root / run_id
        run_root.mkdir()
        model_targets = [target for target in targets if target["model"] == model]
        candidate_count = 1 if model == "aivisspeech-kohaku" else 3
        candidates: list[dict[str, object]] = []
        groups: list[tuple[str, str, str, str]] = []
        attempt_seeds: dict[
            tuple[str, str, str, str], frozenset[int]
        ] = {}
        for target_index, target in enumerate(model_targets):
            scenario = target["scenario"]
            line = target["line"]
            variant = target["variant"]
            identity = (model, scenario, line, variant)
            role = role_by_scenario[scenario]
            role_epoch = _role_epoch(
                model=model,
                scenario=scenario,
                role=role,
                revisions=revisions,
                plan_sha=plan_sha,
                anchor_sha=anchor_sha,
                anchor_epochs=anchor_epochs,
            )
            role_epochs[identity] = role_epoch
            groups.append(identity)
            line_identity = (scenario, line)
            lines.setdefault(
                line_identity,
                {
                    "scenario": scenario,
                    "line": line,
                    "scenario_title": f"Scene {scenario}",
                    "text": f"台詞 {line}",
                    "delivery": "役柄を保って自然に読む",
                },
            )
            contexts.setdefault(
                line_identity,
                {
                    "character": role["character"],
                    "role_identity_sha256": role["role_identity_sha256"],
                    "reference_voice": role["reference_voice"],
                    "role": role["role"],
                    "scene_setting": role["scene_setting"],
                    "reading": (
                        f"ダイシ {line}" if target_index % 7 == 0 else None
                    ),
                    "situation": "正在向附近的人说话。",
                    "emotion": "neutral",
                    "intensity": 2,
                },
            )
            provenance = _phase_b_provenance(
                identity=identity,
                role_epoch=role_epoch,
                plan_sha=plan_sha,
                anchor_sha=anchor_sha,
                anchor_plan_sha=anchor_plan_sha,
            )
            seeds = set[int]()
            for take_index in range(1, candidate_count + 1):
                audio = f"opus:{model}:{scenario}:{line}:{take_index}".encode()
                audio_sha = sha(audio)
                input_sha = sha(
                    f"input:{model}:{scenario}:{line}:{take_index}",
                )
                take_id = make_take_id(
                    generation_input_sha256=input_sha,
                    final_opus_sha256=audio_sha,
                )
                local_path = (
                    run_root
                    / "audio"
                    / model
                    / scenario
                    / line
                    / variant
                    / f"take-{take_index:04d}.opus"
                )
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(audio)
                seed = None if candidate_count == 1 else target_index * 10 + take_index
                if seed is not None:
                    seeds.add(seed)
                candidates.append(
                    _candidate(
                        identity=identity,
                        take_index=take_index,
                        take_id=take_id,
                        audio_sha=audio_sha,
                        input_sha=input_sha,
                        seed=seed,
                        provenance=provenance,
                    ),
                )
            attempt_seeds[identity] = frozenset(seeds)

        model_document = _model_document(model, revisions[model])
        manifest = {
            "format_version": 4,
            "generated_at": "2026-08-02T00:00:00Z",
            "candidate_set_sha256": "0" * 64,
            "models": [model_document],
            "candidates": candidates,
            "curations": [],
            "failures": [],
        }
        run = CompletionSourceRun(
            run_id=run_id,
            model=model,
            kind="primary",
            supersedes_run_id=None,
            root=run_root.resolve(),
            ledger_sha256="1" * 64,
            qc_report_sha256="2" * 64,
            manifest_sha256="3" * 64,
            candidate_set_sha256="4" * 64,
            manifest=manifest,
            groups=frozenset(groups),
            role_epochs={identity: role_epochs[identity] for identity in groups},
            seed_base=None if candidate_count == 1 else 104,
            attempt_seeds=attempt_seeds,
        )
        runs.append(run)
        group_sources.update({identity: run for identity in groups})

    policies = {
        item["model"]: SimpleNamespace(
            minimum_eligible_candidates=item["minimum_eligible_candidates"],
        )
        for item in plan_document["phase_b"]["model_policies"]
    }
    plan = SimpleNamespace(
        plan_id=plan_sha,
        raw_sha256=plan_sha,
        targets=[SimpleNamespace(**target) for target in targets],
        policy_for_model=lambda model: policies[model],
    )
    resolution = CompletionSourceResolution(
        runs=tuple(runs),
        group_sources=group_sources,
        anchor_selection_sha256=anchor_sha,
        expected_role_epochs=role_epochs,
    )
    scenario_authority = CompletionScenarioAuthority(
        scenario_sha256=sha("synthetic-scenario-authority-v1"),
        lines=tuple(
            sorted(lines.values(), key=lambda item: (item["scenario"], item["line"])),
        ),
        contexts=contexts,
        line_characters={
            identity: str(context["character"])
            for identity, context in contexts.items()
        },
    )
    with (
        patch.object(
            completion_listen,
            "resolve_completion_sources",
            return_value=resolution,
        ),
        patch.object(
            completion_listen,
            "_load_completion_scenario_authority",
            return_value=scenario_authority,
        ),
        patch.object(
            completion_listen,
            "require_production_completion_plan",
            return_value=None,
        ),
    ):
        build_completion_listening_bundle(
            plan=plan,
            plan_path=plan_path,
            primary_run_ids=[run.run_id for run in runs],
            topup_run_ids=[],
            anchor_selection_path=anchor_path,
            artifacts_dir=root / "unused-artifacts",
            scenarios_dir=root / "unused-scenarios",
            voices_dir=root / "unused-voices",
            output_dir=output_dir,
        )


def _build_roles() -> list[dict[str, object]]:
    roles: list[dict[str, object]] = []
    for index in range(58):
        identity = {
            "scenario": f"scene-role-{index:03d}",
            "character": f"character-{index:03d}",
            "role": {
                "name": f"角色 {index:03d}",
                "kind": "human",
                "gender": "male" if index % 2 == 0 else "female",
                "age": "adult",
                "archetype": "测试角色",
                "voice": "清晰自然的声音",
                "personality": "沉着",
            },
            "reference_voice": None if index < 53 else f"reference-{index:03d}",
            "scene_setting": f"测试场景 {index:03d}",
        }
        roles.append(
            {**identity, "role_identity_sha256": sha(canonical_bytes(identity))},
        )
    return roles


def _build_anchor_selection(
    *,
    roles: list[dict[str, object]],
    revisions: dict[str, str],
    anchor_plan_sha: str,
    anchor_candidate_sha: str,
) -> dict[str, object]:
    groups: list[dict[str, object]] = []
    for model in sorted(ANCHOR_MODELS):
        for role in roles:
            if role["reference_voice"] is not None:
                continue
            scenario = str(role["scenario"])
            character = str(role["character"])
            anchor_id = sha(f"anchor:{model}:{scenario}:{character}")
            review_epoch = sha(f"review:{model}:{scenario}:{character}")
            audio_sha = sha(f"anchor-audio:{model}:{scenario}:{character}")
            decision = {
                "id": sha(f"decision:{model}:{scenario}:{character}"),
                "model": model,
                "scenario": scenario,
                "character": character,
                "line": None,
                "role_epoch_sha256": review_epoch,
                "group_sha256": sha(f"anchor-group:{model}:{scenario}:{character}"),
                "heard_candidate_ids": [anchor_id],
                "selected_candidate_id": anchor_id,
                "no_usable_candidate": False,
                "rubric": _anchor_rubric(),
                "confirmed": True,
            }
            decision_sha = sha(canonical_bytes(decision))
            role_epoch = sha(
                canonical_bytes(
                    {
                        "protocol": "selected-role-epoch-v1",
                        "model": model,
                        "model_revision": revisions[model],
                        "scenario": scenario,
                        "character": character,
                        "role_identity_sha256": role["role_identity_sha256"],
                        "review_role_epoch_sha256": review_epoch,
                        "anchor_id": anchor_id,
                        "audio_sha256": audio_sha,
                        "decision_sha256": decision_sha,
                    },
                ),
            )
            role_identity = {
                key: role[key]
                for key in (
                    "scenario",
                    "character",
                    "role",
                    "reference_voice",
                    "scene_setting",
                )
            }
            anchor_text = f"锚点 {model} {character}"
            groups.append(
                {
                    "model": model,
                    "model_revision": revisions[model],
                    "scenario": scenario,
                    "character": character,
                    "role_identity": role_identity,
                    "role_identity_sha256": role["role_identity_sha256"],
                    "review_role_epoch_sha256": review_epoch,
                    "role_epoch_sha256": role_epoch,
                    "anchor_id": anchor_id,
                    "attempt": 1,
                    "seed": 1,
                    "audio_path": f"audio/{anchor_id}.wav",
                    "audio_sha256": audio_sha,
                    "anchor_text": anchor_text,
                    "anchor_text_sha256": sha(anchor_text),
                    "decision": decision,
                    "decision_sha256": decision_sha,
                },
            )
    groups.sort(key=lambda item: (item["model"], item["scenario"], item["character"]))
    assert len(groups) == 106
    return {
        "format_version": 1,
        "protocol": "role-anchor-selection-v1",
        "plan_sha256": anchor_plan_sha,
        "candidate_set_sha256": anchor_candidate_sha,
        "groups": groups,
    }


def _build_targets(roles: list[dict[str, object]]) -> list[dict[str, str]]:
    targets = [
        {
            "model": model,
            "scenario": str(roles[index % len(roles)]["scenario"]),
            "line": f"line-{index:03d}",
            "variant": "dry",
        }
        for model, count in MODEL_GROUP_COUNTS.items()
        for index in range(count)
    ]
    targets.sort(
        key=lambda item: (
            item["model"],
            item["scenario"],
            item["line"],
            item["variant"],
        ),
    )
    assert len(targets) == 597
    return targets


def _build_plan(
    *,
    revisions: dict[str, str],
    roles: list[dict[str, object]],
    targets: list[dict[str, str]],
    anchor_plan_sha: str,
    anchor_candidate_sha: str,
    anchor_selection_sha: str,
) -> dict[str, object]:
    scenario_files = [
        {
            "scenario": str(role["scenario"]),
            "path": f"scenarios/{role['scenario']}.yaml",
            "sha256": sha(f"scenario:{role['scenario']}"),
        }
        for role in roles
    ]
    policies = [
        {
            "model": model,
            "takes": 1 if model == "aivisspeech-kohaku" else 4,
            "minimum_eligible_candidates": (
                1 if model == "aivisspeech-kohaku" else 3
            ),
            "seed_policy": (
                "none" if model == "aivisspeech-kohaku" else "derived-sha256-v1"
            ),
            "primary_seed_base": None if model == "aivisspeech-kohaku" else 104,
        }
        for model in MODEL_GROUP_COUNTS
    ]
    return {
        "format_version": 2,
        "protocol": "role-baseline-plan-v2",
        "base": {
            "manifest_sha256": sha("base-manifest"),
            "git_blob": "a" * 40,
            "candidate_set_sha256": sha("base-candidate-set"),
            "selection_sha256": sha("base-selection"),
            "inherited_groups": 691,
            "final_groups": 1288,
        },
        "sources": {
            "scenario_registry_sha256": sha(canonical_bytes(scenario_files)),
            "scenario_files": scenario_files,
            "voice_registry_path": "assets/voices/metadata.yaml",
            "voice_registry_sha256": sha("voice-registry"),
        },
        "models": [
            {"id": model, "revision": revisions[model]}
            for model in MODEL_GROUP_COUNTS
        ],
        "roles": roles,
        "anchor_authority": {
            "source_plan_sha256": anchor_plan_sha,
            "candidate_set_sha256": anchor_candidate_sha,
            "selection_sha256": anchor_selection_sha,
        },
        "phase_b": {"model_policies": policies, "targets": targets},
    }


def _role_epoch(
    *,
    model: str,
    scenario: str,
    role: dict[str, object],
    revisions: dict[str, str],
    plan_sha: str,
    anchor_sha: str,
    anchor_epochs: dict[tuple[str, str, str], str],
) -> str:
    anchor = anchor_epochs.get((model, scenario, str(role["character"])))
    if anchor is not None:
        return anchor
    return sha(
        canonical_bytes(
            {
                "protocol": "phase-b-role-epoch-v1",
                "plan_sha256": plan_sha,
                "model": model,
                "model_revision": revisions[model],
                "scenario": scenario,
                "character": role["character"],
                "role_identity_sha256": role["role_identity_sha256"],
                "reference_voice": role["reference_voice"],
                "anchor_selection_sha256": anchor_sha if model in ANCHOR_MODELS else None,
            },
        ),
    )


def _phase_b_provenance(
    *,
    identity: tuple[str, str, str, str],
    role_epoch: str,
    plan_sha: str,
    anchor_sha: str,
    anchor_plan_sha: str,
) -> dict[str, object]:
    model, scenario, line, variant = identity
    anchor_bound = model in ANCHOR_MODELS
    return {
        "protocol": "phase-b-generation-v2",
        "plan_sha256": plan_sha,
        "run_kind": "primary",
        "supersedes_run_id": None,
        "anchor_selection_sha256": anchor_sha if anchor_bound else None,
        "anchor_plan_sha256": anchor_plan_sha if anchor_bound else None,
        "target_group": {
            "model": model,
            "scenario": scenario,
            "line": line,
            "variant": variant,
            "role_epoch_sha256": role_epoch,
        },
    }


def _candidate(
    *,
    identity: tuple[str, str, str, str],
    take_index: int,
    take_id: str,
    audio_sha: str,
    input_sha: str,
    seed: int | None,
    provenance: dict[str, object],
) -> dict[str, object]:
    model, scenario, line, variant = identity
    return {
        "model": model,
        "scenario": scenario,
        "line": line,
        "variant": variant,
        "take_index": take_index,
        "take_id": take_id,
        "path": (
            f"audio/takes/{model}/{scenario}/{line}/{variant}/"
            f"take-{take_index:04d}-{audio_sha}.opus"
        ),
        "duration_sec": 1,
        "sha256": audio_sha,
        "generation_input_sha256": input_sha,
        "gen_params": {
            "seed": seed,
            "recipe_version": "cross-contract-v3",
            "sampling": {},
            "requested": {"phase_b_provenance": provenance},
            "realized": {"phase_b_provenance": provenance},
        },
        "rtf": 0.5,
        "loudness": {
            "source": "encoded_opus",
            "i_lufs": -18,
            "tp_dbtp": -1,
            "shortfall": False,
        },
        "gate": {
            "mechanical": "pass",
            "content": "pass" if take_index > 1 else "review_required",
            "policy_version": "take-gates-v2",
        },
    }


def _model_document(model: str, revision: str) -> dict[str, object]:
    return {
        "id": model,
        "name": model,
        "version": revision,
        "license_note": "",
        "capabilities": {
            "emotion": False,
            "voice_prompt": False,
            "clone": False,
            "nonverbal": False,
            "reading": False,
        },
    }


def _anchor_rubric() -> dict[str, object]:
    return {
        "content": "pass",
        "prompt_leakage": "pass",
        "reading": "pass",
        "pitch_accent": "pass",
        "gender": "pass",
        "age": "pass",
        "archetype": "pass",
        "voice_identity": "not_applicable",
        "delivery": "not_applicable",
        "naturalness_quality": 4,
        "notes": "",
    }


def validate_site_decision(bundle: Path, raw: bytes) -> bytes:
    decision = validate_completion_decision(json.loads(raw))
    canonical = canonical_completion_decision_bytes(decision)
    if raw != canonical:
        raise ValueError("site decision must be canonical bytes")
    plan_raw = (bundle / "completion-plan.json").read_bytes()
    anchor_raw = (bundle / "role-anchor-selection-v1.json").read_bytes()
    candidate_raw = (bundle / "candidate-set.json").read_bytes()
    candidate_set = json.loads(candidate_raw)
    plan_document = json.loads(plan_raw)
    if (
        decision["plan_sha256"] != sha(plan_raw)
        or decision["anchor_selection_sha256"] != sha(anchor_raw)
        or decision["candidate_set_sha256"] != sha(candidate_raw)
    ):
        raise ValueError("release decision authority binding mismatch")
    source_map = json.loads(
        (bundle / "phase-b-source-map-v1.json").read_bytes(),
    )
    source_groups = {
        (group["model"], group["scenario"], group["line"], group["variant"]): group
        for group in source_map["groups"]
    }
    candidates_by_group: dict[
        tuple[str, str, str, str], list[dict[str, Any]]
    ] = {}
    for candidate in candidate_set["candidates"]:
        identity = (
            candidate["model"],
            candidate["scenario"],
            candidate["line"],
            candidate["variant"],
        )
        candidates_by_group.setdefault(identity, []).append(candidate)
    decision_groups = {
        (
            group["model"],
            group["scenario"],
            group["line"],
            group["variant"],
        ): group
        for group in decision["groups"]
    }
    if set(decision_groups) != set(source_groups):
        raise ValueError("release decision/source group set mismatch")
    contexts = {
        (identity[1], identity[2]): {
            key: source[key]
            for key in (
                "character",
                "role_identity_sha256",
                "reference_voice",
                "role",
                "scene_setting",
                "reading",
                "situation",
                "emotion",
                "intensity",
            )
        }
        for identity, source in source_groups.items()
    }
    policies = {
        item["model"]: SimpleNamespace(
            minimum_eligible_candidates=item["minimum_eligible_candidates"],
        )
        for item in plan_document["phase_b"]["model_policies"]
    }
    scenario_authority = CompletionScenarioAuthority(
        scenario_sha256=candidate_set["scenario_sha256"],
        lines=tuple(candidate_set["lines"]),
        contexts=contexts,
        line_characters={
            identity: str(context["character"])
            for identity, context in contexts.items()
        },
    )
    resolution = SimpleNamespace(
        expected_role_epochs={
            identity: source["role_epoch_sha256"]
            for identity, source in source_groups.items()
        },
        group_sources={
            identity: SimpleNamespace(
                run_id=source["source_run_id"],
                manifest={"candidates": candidates_by_group[identity]},
            )
            for identity, source in source_groups.items()
        },
    )
    _validate_decision_against_sources(
        decision_groups=decision_groups,
        plan=SimpleNamespace(policy_for_model=lambda model: policies[model]),
        resolution=resolution,
        candidate_set=candidate_set,
        scenario_authority=scenario_authority,
    )
    return canonical


if __name__ == "__main__":
    if len(sys.argv) == 2:
        main(Path(sys.argv[1]).resolve())
    elif len(sys.argv) == 3 and sys.argv[1] == "validate-decision":
        sys.stdout.buffer.write(
            validate_site_decision(
                Path(sys.argv[2]).resolve(),
                sys.stdin.buffer.read(),
            ),
        )
    else:
        raise SystemExit(
            "usage: generate-phase-b-bundle.py OUTPUT_ROOT | "
            "validate-decision BUNDLE_ROOT",
        )
