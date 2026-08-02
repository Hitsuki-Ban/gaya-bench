from __future__ import annotations

import json
import struct
import subprocess
import sys
import wave
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, ClassVar

import pytest

from gaya_pipeline.adapters.base import (
    Capabilities,
    LineJob,
    ModelProfile,
    TakeContext,
    TakeRecipe,
)
from gaya_pipeline.role_contamination_canary import (
    MATRIX_REPORT_KIND,
    SESSION_REPORT_KIND,
    RoleContaminationCanaryError,
    _execute_session,
    _launch_session_process,
    aggregate_role_contamination_reports,
    run_role_contamination_canary,
)

TARGET_A = "guild-hall/receptionist-001"
TARGET_B = "guild-hall/veteran-001"


class _FakeAdapter:
    profile = ModelProfile(
        id="fake-role-canary",
        name="Fake role canary",
        version="1",
        license_note="test",
        capabilities=Capabilities(
            emotion=False,
            voice_prompt=False,
            clone=False,
            nonverbal=False,
            reading=False,
        ),
    )
    prepare_events: ClassVar[list[tuple[int, tuple[str, ...]]]] = []

    def __init__(self, *, instance: int, mode: str) -> None:
        self.instance = instance
        self.mode = mode
        self.prepared_targets: tuple[str, ...] = ()
        self.a_contaminated = False

    def take_recipe(self) -> TakeRecipe:
        return TakeRecipe(
            version="test-v1",
            seed_policy="derived-sha256-v1",
            single_take_seed=0,
            seed_range=(0, 999),
            sampling=(("temperature", 0.5),),
            supports_multiple=True,
        )

    def generation_params(self) -> dict[str, Any]:
        return {"adapter": "fake", "mode": self.mode}

    def prepare(
        self,
        jobs: list[LineJob],
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        assert voices_dir.is_dir()
        artifacts_dir.mkdir(parents=True)
        self.prepared_targets = tuple(
            f"{job.scenario_id}/{job.line_id}" for job in jobs
        )
        self.a_contaminated = (
            self.mode in {"prepare-contamination", "input-contamination"}
            and TARGET_B in self.prepared_targets
        )
        self.prepare_events.append((self.instance, self.prepared_targets))

    def generation_input(
        self,
        job: LineJob,
        take_context: TakeContext,
    ) -> dict[str, Any]:
        target = f"{job.scenario_id}/{job.line_id}"
        value: dict[str, Any] = {
            "target": target,
            "seed": take_context.seed,
        }
        if self.mode == "input-contamination" and target == TARGET_A:
            value["prepare_contaminated"] = self.a_contaminated
        return value

    def generate(
        self,
        job: LineJob,
        take_context: TakeContext,
        output_wav: Path,
    ) -> dict[str, Any]:
        target = f"{job.scenario_id}/{job.line_id}"
        marker = 100 if target == TARGET_A else 200
        if (
            self.mode == "prepare-contamination"
            and target == TARGET_A
            and self.a_contaminated
        ):
            marker += 50
        if self.mode == "nondeterministic":
            marker += self.instance
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_wav), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16_000)
            wav_file.writeframes(struct.pack("<h", marker))
        return {
            "seed": take_context.seed,
            "prepared_targets": list(self.prepared_targets),
        }


def _local_clean_process_launcher(
    mode: str,
) -> Callable[[Path], dict[str, Any]]:
    next_instance = 0

    def launch(spec_path: Path) -> dict[str, Any]:
        nonlocal next_instance
        next_instance += 1
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        instance = next_instance
        return _execute_session(
            spec,
            adapter_factory=lambda _model_id: _FakeAdapter(
                instance=instance,
                mode=mode,
            ),
            process_id=900_000 + instance,
            parent_process_id=800_000 + instance,
            process_instance_id=f"test-process-instance-{instance}",
        )

    return launch


def _run_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    mode: str,
) -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[2]
    _FakeAdapter.prepare_events = []
    monkeypatch.setattr(
        "gaya_pipeline.role_contamination_canary._launch_session_process",
        _local_clean_process_launcher(mode),
    )
    return run_role_contamination_canary(
        model_id="fake-role-canary",
        scenarios_dir=repository_root / "scenarios",
        voices_dir=repository_root / "assets" / "voices",
        run_root=tmp_path / "run",
        targets=(TARGET_A, TARGET_B),
        seed=177,
    )


def test_clean_process_matrix_passes_bit_identical_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _run_matrix(monkeypatch, tmp_path, mode="clean")

    assert report["format_version"] == 2
    assert report["report_kind"] == MATRIX_REPORT_KIND
    assert report["verdict"] == "pass"
    assert report["targets"] == [TARGET_A, TARGET_B]
    assert [session["label"] for session in report["sessions"]] == [
        "isolated-a-1",
        "isolated-a-2",
        "isolated-b-1",
        "isolated-b-2",
        "forward",
        "reverse",
        "aba",
        "bab",
    ]
    assert len({session["session_id"] for session in report["sessions"]}) == 8
    assert len(
        {session["process_instance_id"] for session in report["sessions"]},
    ) == 8
    assert all(
        session["report_kind"] == SESSION_REPORT_KIND
        and session["prepare_invocation_count"] == 1
        for session in report["sessions"]
    )
    assert len(_FakeAdapter.prepare_events) == 8
    assert len({instance for instance, _ in _FakeAdapter.prepare_events}) == 8
    assert dict(_FakeAdapter.prepare_events[4:]) == {
        5: (TARGET_A, TARGET_B),
        6: (TARGET_B, TARGET_A),
        7: (TARGET_A, TARGET_B),
        8: (TARGET_B, TARGET_A),
    }
    for target_result in report["target_results"]:
        assert target_result["verdict"] == "pass"
        assert target_result["bit_identical"] is True
        assert target_result["isolated_stable"] is True
        assert len(target_result["comparison_groups"]) == 1
        assert target_result["comparison_groups"][0]["attempt_count"] == 7
        assert len(
            target_result["comparison_groups"][0]["wav_sha256_values"],
        ) == 1
    assert (tmp_path / "run" / "report.json").is_file()
    assert len(list((tmp_path / "run" / "sessions").glob("*/report.json"))) == 8


def test_prepare_time_cross_target_contamination_is_a_machine_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _run_matrix(
        monkeypatch,
        tmp_path,
        mode="prepare-contamination",
    )

    assert report["verdict"] == "fail"
    results = {result["target"]: result for result in report["target_results"]}
    affected = results[TARGET_A]
    assert affected["verdict"] == "fail"
    assert affected["isolated_stable"] is True
    assert affected["bit_identical"] is False
    mismatch_labels = {
        attempt["session_label"]
        for attempt in affected["difference_evidence"]["mismatched_attempts"]
    }
    assert mismatch_labels == {"forward", "reverse", "aba", "bab"}
    assert results[TARGET_B]["verdict"] == "pass"


def test_generation_input_changes_are_a_machine_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _run_matrix(
        monkeypatch,
        tmp_path,
        mode="input-contamination",
    )

    assert report["verdict"] == "fail"
    affected = next(
        result
        for result in report["target_results"]
        if result["target"] == TARGET_A
    )
    assert affected["verdict"] == "fail"
    assert len(affected["comparison_groups"]) == 2
    assert len(
        affected["difference_evidence"]["generation_input_sha256_values"],
    ) == 2


def test_isolated_nondeterminism_requires_review_not_false_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _run_matrix(
        monkeypatch,
        tmp_path,
        mode="nondeterministic",
    )

    assert report["verdict"] == "review"
    for target_result in report["target_results"]:
        assert target_result["verdict"] == "review"
        assert target_result["isolated_stable"] is False
        assert len(
            target_result["difference_evidence"][
                "isolated_wav_sha256_values"
            ],
        ) == 2


def test_equivalent_clean_topology_cold_run_difference_requires_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _run_matrix(monkeypatch, tmp_path, mode="clean")
    sessions = deepcopy(report["sessions"])
    aba = next(session for session in sessions if session["label"] == "aba")
    aba_a_first = next(
        attempt
        for attempt in aba["attempts"]
        if attempt["target"] == TARGET_A and attempt["position"] == 1
    )
    aba_a_first["wav_sha256"] = "f" * 64

    reaggregated = aggregate_role_contamination_reports(sessions)

    assert reaggregated["verdict"] == "review"
    results = {
        result["target"]: result
        for result in reaggregated["target_results"]
    }
    affected = results[TARGET_A]
    assert affected["verdict"] == "review"
    assert affected["isolated_stable"] is True
    assert results[TARGET_B]["verdict"] == "pass"
    unstable = affected["difference_evidence"]["unstable_topologies"]
    assert [group["topology"] for group in unstable] == [
        "multi-target-clean",
    ]
    assert {
        (attempt["session_label"], attempt["position"])
        for attempt in unstable[0]["attempts"]
    } == {("forward", 1), ("aba", 1)}
    assert len(unstable[0]["wav_sha256_values"]) == 2


def test_duplicate_session_identity_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _run_matrix(monkeypatch, tmp_path, mode="clean")
    sessions = deepcopy(report["sessions"])
    sessions[1]["process_instance_id"] = sessions[0]["process_instance_id"]

    with pytest.raises(
        RoleContaminationCanaryError,
        match="process_instance_id.*重複",
    ):
        aggregate_role_contamination_reports(sessions)


def test_duplicate_actual_python_process_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _run_matrix(monkeypatch, tmp_path, mode="clean")
    sessions = deepcopy(report["sessions"])
    sessions[1]["process_id"] = sessions[0]["process_id"]

    with pytest.raises(
        RoleContaminationCanaryError,
        match="process_id.*重複",
    ):
        aggregate_role_contamination_reports(sessions)


def test_legacy_single_process_report_has_explicit_rerun_error() -> None:
    with pytest.raises(
        RoleContaminationCanaryError,
        match="format_version=1.*再実行",
    ):
        aggregate_role_contamination_reports(
            [{"format_version": 1, "sequence": [TARGET_A, TARGET_B, TARGET_A]}],
        )


def test_launch_session_uses_python_child_process_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    session_root = tmp_path / "session"
    spec = {
        "format_version": 2,
        "session_id": "isolated-a-1-test",
        "session_token": "test-token",
        "label": "isolated-a-1",
        "model_id": "fake-role-canary",
        "scenarios_dir": str(repository_root / "scenarios"),
        "voices_dir": str(repository_root / "assets" / "voices"),
        "session_root": str(session_root),
        "sequence": [TARGET_A],
        "seed": 177,
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    received: list[str] = []

    class FakePopen:
        pid = 41216
        returncode = 0

        def __init__(
            self,
            command: list[str],
            *,
            stdout: int,
            stderr: int,
            text: bool,
        ) -> None:
            received.extend(command)
            assert stdout == subprocess.PIPE
            assert stderr == subprocess.PIPE
            assert text is True

        def communicate(self) -> tuple[str, str]:
            _execute_session(
                spec,
                adapter_factory=lambda _model_id: _FakeAdapter(
                    instance=1,
                    mode="clean",
                ),
                process_id=24596,
                parent_process_id=self.pid,
                process_instance_id="child-process-instance",
            )
            return "", ""

    monkeypatch.setattr(
        "gaya_pipeline.role_contamination_canary.subprocess.Popen",
        FakePopen,
    )
    report = _launch_session_process(spec_path)

    assert received[:3] == [
        sys.executable,
        "-m",
        "gaya_pipeline.role_contamination_canary",
    ]
    assert received[3:] == ["_session", "--spec", str(spec_path)]
    assert report["process_id"] == 24596
    assert report["parent_process_id"] == 41216


def test_launch_rejects_report_from_a_different_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    session_root = tmp_path / "session"
    spec = {
        "format_version": 2,
        "session_id": "isolated-a-1-test",
        "session_token": "test-token",
        "label": "isolated-a-1",
        "model_id": "fake-role-canary",
        "scenarios_dir": str(repository_root / "scenarios"),
        "voices_dir": str(repository_root / "assets" / "voices"),
        "session_root": str(session_root),
        "sequence": [TARGET_A],
        "seed": 177,
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    class FakePopen:
        pid = 54321
        returncode = 0

        def __init__(
            self,
            _command: list[str],
            *,
            stdout: int,
            stderr: int,
            text: bool,
        ) -> None:
            assert stdout == subprocess.PIPE
            assert stderr == subprocess.PIPE
            assert text is True

        def communicate(self) -> tuple[str, str]:
            _execute_session(
                spec,
                adapter_factory=lambda _model_id: _FakeAdapter(
                    instance=1,
                    mode="clean",
                ),
                process_id=99999,
                parent_process_id=12345,
                process_instance_id="other-process-instance",
            )
            return "", ""

    monkeypatch.setattr(
        "gaya_pipeline.role_contamination_canary.subprocess.Popen",
        FakePopen,
    )
    with pytest.raises(
        RoleContaminationCanaryError,
        match=r"parent_process_id.*expected=54321.*actual=12345",
    ):
        _launch_session_process(spec_path)


def test_launch_rejects_redirector_pid_as_actual_python_pid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    session_root = tmp_path / "session"
    spec = {
        "format_version": 2,
        "session_id": "isolated-a-1-test",
        "session_token": "test-token",
        "label": "isolated-a-1",
        "model_id": "fake-role-canary",
        "scenarios_dir": str(repository_root / "scenarios"),
        "voices_dir": str(repository_root / "assets" / "voices"),
        "session_root": str(session_root),
        "sequence": [TARGET_A],
        "seed": 177,
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    class FakePopen:
        pid = 54321
        returncode = 0

        def __init__(
            self,
            _command: list[str],
            *,
            stdout: int,
            stderr: int,
            text: bool,
        ) -> None:
            assert stdout == subprocess.PIPE
            assert stderr == subprocess.PIPE
            assert text is True

        def communicate(self) -> tuple[str, str]:
            _execute_session(
                spec,
                adapter_factory=lambda _model_id: _FakeAdapter(
                    instance=1,
                    mode="clean",
                ),
                process_id=self.pid,
                parent_process_id=self.pid,
                process_instance_id="redirector-process-instance",
            )
            return "", ""

    monkeypatch.setattr(
        "gaya_pipeline.role_contamination_canary.subprocess.Popen",
        FakePopen,
    )
    with pytest.raises(
        RoleContaminationCanaryError,
        match="base Python process.*redirector=54321",
    ):
        _launch_session_process(spec_path)


def test_existing_run_root_and_invalid_target_matrix_are_rejected(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    run_root = tmp_path / "run"
    run_root.mkdir()
    with pytest.raises(RoleContaminationCanaryError, match="新規 path"):
        run_role_contamination_canary(
            model_id="fake-role-canary",
            scenarios_dir=repository_root / "scenarios",
            voices_dir=repository_root / "assets" / "voices",
            run_root=run_root,
            targets=(TARGET_A, TARGET_B),
        )

    with pytest.raises(RoleContaminationCanaryError, match="2件"):
        run_role_contamination_canary(
            model_id="fake-role-canary",
            scenarios_dir=repository_root / "scenarios",
            voices_dir=repository_root / "assets" / "voices",
            run_root=tmp_path / "other-run",
            targets=(TARGET_A,),
        )
