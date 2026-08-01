from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import gaya_pipeline.completion_listen as completion_listen
from gaya_pipeline.completion_listen import (
    CompletionSourceResolution,
    CompletionSourceRun,
    build_completion_listening_bundle,
)
from gaya_pipeline.take_identity import make_take_id


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


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main(root: Path) -> None:
    root.mkdir()
    output_dir = root / "bundle"
    lines: list[dict[str, str]] = []
    all_groups: list[tuple[str, str, str, str]] = []
    role_epochs: dict[tuple[str, str, str, str], str] = {}
    group_sources: dict[
        tuple[str, str, str, str],
        CompletionSourceRun,
    ] = {}
    runs: list[CompletionSourceRun] = []

    global_index = 0
    for model, group_count in MODEL_GROUP_COUNTS.items():
        run_id = f"cross-contract-{model}"
        run_root = root / run_id
        run_root.mkdir()
        candidate_count = 1 if model == "aivisspeech-kohaku" else 3
        model_groups: list[tuple[str, str, str, str]] = []
        candidates: list[dict[str, object]] = []
        attempt_seeds: dict[
            tuple[str, str, str, str],
            frozenset[int],
        ] = {}
        for model_index in range(group_count):
            scenario = f"scene-{global_index:03d}"
            line = f"line-{global_index:03d}"
            identity = (model, scenario, line, "dry")
            model_groups.append(identity)
            all_groups.append(identity)
            role_epochs[identity] = sha(f"role:{global_index}".encode())
            attempt_seeds[identity] = (
                frozenset()
                if candidate_count == 1
                else frozenset(
                    global_index * 10 + take_index
                    for take_index in range(1, candidate_count + 1)
                )
            )
            lines.append(
                {
                    "scenario": scenario,
                    "line": line,
                    "scenario_title": f"Scene {global_index:03d}",
                    "text": f"台詞 {global_index:03d}",
                    "delivery": "役柄と自然さを維持して読む",
                },
            )
            for take_index in range(1, candidate_count + 1):
                audio = f"opus:{model}:{global_index}:{take_index}".encode()
                audio_sha = sha(audio)
                input_sha = sha(
                    f"input:{model}:{global_index}:{take_index}".encode(),
                )
                take_id = make_take_id(
                    generation_input_sha256=input_sha,
                    final_opus_sha256=audio_sha,
                )
                artifact_path = (
                    f"audio/takes/{model}/{scenario}/{line}/dry/"
                    f"take-{take_index:04d}-{audio_sha}.opus"
                )
                local_path = (
                    run_root
                    / "audio"
                    / model
                    / scenario
                    / line
                    / "dry"
                    / f"take-{take_index:04d}.opus"
                )
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(audio)
                seed = (
                    None
                    if candidate_count == 1
                    else global_index * 10 + take_index
                )
                candidates.append(
                    {
                        "model": model,
                        "scenario": scenario,
                        "line": line,
                        "variant": "dry",
                        "take_index": take_index,
                        "take_id": take_id,
                        "path": artifact_path,
                        "duration_sec": 1.0,
                        "sha256": audio_sha,
                        "generation_input_sha256": input_sha,
                        "gen_params": {
                            "seed": seed,
                            "recipe_version": "cross-contract-v2",
                            "sampling": {},
                            "requested": {},
                            "realized": {},
                        },
                        "rtf": 0.5,
                        "loudness": {
                            "source": "encoded_opus",
                            "i_lufs": -18.0,
                            "tp_dbtp": -1.0,
                            "shortfall": False,
                        },
                        "gate": {
                            "mechanical": "pass",
                            "content": (
                                "review_required"
                                if take_index == 1
                                else "pass"
                            ),
                            "policy_version": "take-gates-v2",
                        },
                    },
                )
            global_index += 1

        model_document = {
            "id": model,
            "name": model,
            "version": "cross-contract-v2",
            "license_note": "",
            "capabilities": {
                "emotion": False,
                "voice_prompt": False,
                "clone": False,
                "nonverbal": False,
                "reading": False,
            },
        }
        manifest = {
            "format_version": 4,
            "generated_at": "2026-07-31T00:00:00Z",
            "candidate_set_sha256": "0" * 64,
            "models": [model_document],
            "candidates": candidates,
            "curations": [],
            "failures": [],
        }
        source_run = CompletionSourceRun(
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
            groups=frozenset(model_groups),
            role_epochs={identity: role_epochs[identity] for identity in model_groups},
            seed_base=None if candidate_count == 1 else 104,
            attempt_seeds=attempt_seeds,
        )
        runs.append(source_run)
        group_sources.update(
            {identity: source_run for identity in model_groups},
        )

    plan_sha = sha(b"frozen-plan-v2")
    anchor_sha = sha(b"anchor-selection")
    plan = SimpleNamespace(
        plan_id=plan_sha,
        targets=[
            SimpleNamespace(scenario=identity[1], line=identity[2])
            for identity in all_groups
        ],
        policy_for_model=lambda model: SimpleNamespace(
            minimum_eligible_candidates=(
                1 if model == "aivisspeech-kohaku" else 3
            ),
        ),
    )
    resolution = CompletionSourceResolution(
        runs=tuple(runs),
        group_sources=group_sources,
        anchor_selection_sha256=anchor_sha,
        expected_role_epochs=role_epochs,
    )
    with (
        patch.object(
            completion_listen,
            "resolve_completion_sources",
            return_value=resolution,
        ),
        patch.object(
            completion_listen,
            "_load_target_lines",
            return_value=(sha(b"scenarios"), lines),
        ),
        patch.object(
            completion_listen,
            "require_production_completion_plan",
            return_value=None,
        ),
    ):
        build_completion_listening_bundle(
            plan=plan,
            primary_run_ids=[run.run_id for run in runs],
            topup_run_ids=[],
            anchor_selection_path=root / "unused-anchor.json",
            artifacts_dir=root / "unused-artifacts",
            scenarios_dir=root / "unused-scenarios",
            voices_dir=root / "unused-voices",
            output_dir=output_dir,
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: generate-phase-b-bundle.py OUTPUT_ROOT")
    main(Path(sys.argv[1]).resolve())
