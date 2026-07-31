from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import gaya_pipeline.completion_listen as completion_listen
from gaya_pipeline.completion_listen import (
    CompletionSourceResolution,
    CompletionSourceRun,
    build_completion_listening_bundle,
)
from gaya_pipeline.take_identity import make_take_id


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main(root: Path) -> None:
    root.mkdir()
    run_root = root / "source-run"
    run_root.mkdir()
    output_dir = root / "bundle"
    model = {
        "id": "dummy",
        "name": "Cross-contract dummy",
        "version": "1",
        "license_note": "",
        "capabilities": {
            "emotion": False,
            "voice_prompt": False,
            "clone": False,
            "nonverbal": False,
            "reading": False,
        },
    }
    groups: list[tuple[str, str, str, str]] = []
    lines: list[dict[str, str]] = []
    candidates: list[dict[str, object]] = []
    role_epochs: dict[tuple[str, str, str, str], str] = {}
    attempt_seeds: dict[
        tuple[str, str, str, str],
        frozenset[int],
    ] = {}
    for group_index in range(363):
        scenario = f"scene-{group_index:03d}"
        line = f"line-{group_index:03d}"
        identity = ("dummy", scenario, line, "dry")
        groups.append(identity)
        attempt_seeds[identity] = frozenset(
            group_index * 10 + take_index for take_index in range(1, 4)
        )
        lines.append(
            {
                "scenario": scenario,
                "line": line,
                "scenario_title": f"Scene {group_index:03d}",
                "text": f"台詞 {group_index:03d}",
                "delivery": "役柄と自然さを維持して読む",
            },
        )
        role_epochs[identity] = sha(f"role:{group_index}".encode())
        for take_index in range(1, 4):
            audio = f"opus:{group_index}:{take_index}".encode()
            audio_sha = sha(audio)
            generation_input_sha = sha(
                f"input:{group_index}:{take_index}".encode(),
            )
            take_id = make_take_id(
                generation_input_sha256=generation_input_sha,
                final_opus_sha256=audio_sha,
            )
            artifact_path = (
                f"audio/takes/dummy/{scenario}/{line}/dry/"
                f"take-{take_index:04d}-{audio_sha}.opus"
            )
            local_path = (
                run_root
                / "audio"
                / "dummy"
                / scenario
                / line
                / "dry"
                / f"take-{take_index:04d}.opus"
            )
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(audio)
            candidates.append(
                {
                    "model": "dummy",
                    "scenario": scenario,
                    "line": line,
                    "variant": "dry",
                    "take_index": take_index,
                    "take_id": take_id,
                    "path": artifact_path,
                    "duration_sec": 1.0,
                    "sha256": audio_sha,
                    "generation_input_sha256": generation_input_sha,
                    "gen_params": {
                        "seed": group_index * 10 + take_index,
                        "recipe_version": "cross-contract-v1",
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
                            "review_required" if take_index == 1 else "pass"
                        ),
                        "policy_version": "take-gates-v2",
                    },
                },
            )
    run_manifest = {
        "format_version": 4,
        "generated_at": "2026-07-31T00:00:00Z",
        "candidate_set_sha256": "0" * 64,
        "models": [model],
        "candidates": candidates,
        "curations": [],
        "failures": [],
    }
    source_run = CompletionSourceRun(
        run_id="cross-contract-source",
        model="dummy",
        kind="primary",
        supersedes_run_id=None,
        root=run_root.resolve(),
        ledger_sha256="1" * 64,
        qc_report_sha256="2" * 64,
        manifest_sha256="3" * 64,
        candidate_set_sha256="4" * 64,
        manifest=run_manifest,
        groups=frozenset(groups),
        role_epochs=role_epochs,
        seed_base=104,
        attempt_seeds=attempt_seeds,
    )
    plan_sha = sha(b"frozen-plan")
    anchor_sha = sha(b"anchor-selection")
    plan = SimpleNamespace(
        plan_id=plan_sha,
        seed_base=104,
        minimum_eligible_candidates=3,
        targets=[
            SimpleNamespace(scenario=identity[1], line=identity[2])
            for identity in groups
        ],
    )
    resolution = CompletionSourceResolution(
        runs=(source_run,),
        group_sources={identity: source_run for identity in groups},
        anchor_selection_sha256=anchor_sha,
        expected_role_epochs=role_epochs,
    )
    original_resolve = completion_listen.resolve_completion_sources
    original_lines = completion_listen._load_target_lines
    completion_listen.resolve_completion_sources = lambda **_kwargs: resolution
    completion_listen._load_target_lines = lambda **_kwargs: (sha(b"scenarios"), lines)
    try:
        build_completion_listening_bundle(
            plan=plan,
            primary_run_ids=["cross-contract-source"],
            topup_run_ids=[],
            vox_run_id="unused",
            anchor_selection_path=root / "unused-anchor.json",
            artifacts_dir=root / "unused-artifacts",
            scenarios_dir=root / "unused-scenarios",
            voices_dir=root / "unused-voices",
            output_dir=output_dir,
        )
    finally:
        completion_listen.resolve_completion_sources = original_resolve
        completion_listen._load_target_lines = original_lines


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: generate-phase-b-bundle.py OUTPUT_ROOT")
    main(Path(sys.argv[1]).resolve())
