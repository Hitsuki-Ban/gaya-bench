from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline import cli, qc
from gaya_pipeline.audio import (
    AudioProbe,
    AudioProcessingError,
    AudioTools,
    EncodedLoudnessReport,
    PostprocessProfile,
)
from gaya_pipeline.curation import apply_curation, canonical_candidate_set_bytes
from gaya_pipeline.qc import QCError, RuntimeInspection, count_japanese_mora, run_qc
from gaya_pipeline.qc_report import QCReportError, validate_qc_report
from gaya_pipeline.take_identity import canonical_json, make_take_id
from gaya_pipeline.take_ledger import read_ledger, write_ledger_atomic
from gaya_pipeline.take_manifest_v4 import validate_manifest_v4
from gaya_pipeline.take_sidecar import validate_take_sidecar

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"
ARTIFACT_BYTES = b"stable generated audio"
TOOLS = AudioTools(
    ffmpeg="ffmpeg",
    ffprobe="ffprobe",
    ffmpeg_version="ffmpeg version test",
    ffprobe_version="ffprobe version test",
    libopus_encoder=True,
)


class FakeRuntime:
    def __init__(
        self,
        transcript: str,
        *,
        active_speech_sec: float = 1.5,
        prepare_error: Exception | None = None,
        inspect_error: Exception | None = None,
    ) -> None:
        self.transcript = transcript
        self.active_speech_sec = active_speech_sec
        self.prepare_error = prepare_error
        self.inspect_error = inspect_error
        self.prepare_calls = 0
        self.inspect_calls = 0
        self.mora_counts: list[int] = []

    def prepare(self) -> None:
        self.prepare_calls += 1
        if self.prepare_error is not None:
            raise self.prepare_error

    def describe(self) -> dict[str, Any]:
        return {
            "asr_model": "fake-kana-asr",
            "asr_revision": "test",
            "prosody_thresholds": "report_only",
        }

    def inspect(
        self,
        audio_path: Path,
        *,
        mora_count: int,
    ) -> RuntimeInspection:
        self.inspect_calls += 1
        self.mora_counts.append(mora_count)
        assert audio_path.is_file()
        if self.inspect_error is not None:
            raise self.inspect_error
        return RuntimeInspection(
            transcript=self.transcript,
            average_log_probability=-0.2,
            prosody={
                "duration_sec": 2.0,
                "active_speech_sec": self.active_speech_sec,
                "estimated_mora_count": mora_count,
                "pause": {"internal_count": 1},
                "f0": {"median_hz": 150.0},
            },
        )


@pytest.fixture(autouse=True)
def fake_audio_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qc, "find_audio_tools", lambda: TOOLS)

    def fake_probe(_tools: AudioTools, path: Path) -> AudioProbe:
        if path.suffix == ".wav":
            return AudioProbe("pcm_s16le", 48_000, 1, 2.0)
        return AudioProbe("opus", 48_000, 1, 2.0)

    monkeypatch.setattr(qc, "probe_audio", fake_probe)
    monkeypatch.setattr(
        qc,
        "measure_encoded_opus",
        lambda _tools, _path, _profile: EncodedLoudnessReport(
            integrated_lufs=-18.0,
            true_peak_dbtp=-1.0,
            loudness_range_lu=4.0,
        ),
    )


def test_explicit_reading_pass_creates_eligible_snapshot(tmp_path: Path) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    runtime = FakeRuntime("ウチノマーボーワカライヨカクゴシナ")

    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=runtime,
    )

    assert runtime.prepare_calls == 1
    assert runtime.inspect_calls == 1
    assert runtime.mora_counts == [
        count_japanese_mora("ウチノマーボーワカライヨ、カクゴシナ！"),
    ]
    assert summary.eligible_count == 1
    assert summary.blocked_count == 0
    assert summary.pending_count == 0
    assert summary.snapshot_path is not None
    ledger = read_ledger(ledger_path)
    assert ledger["attempts"][0]["status"] == "eligible"
    assert ledger["attempts"][0]["gates"] == {
        "mechanical": "pass",
        "content": "pass",
    }
    manifest = validate_manifest_v4(
        json.loads(summary.snapshot_path.read_text(encoding="utf-8")),
    )
    assert len(manifest["candidates"]) == 1
    candidate = manifest["candidates"][0]
    assert candidate["gate"]["policy_version"] == "take-gates-v2"
    assert candidate["gen_params"]["requested"] == {"temperature": 1.0}
    assert candidate["gen_params"]["realized"] == {"temperature": 1.0}
    assert candidate["loudness"] == {
        "source": "encoded_opus",
        "i_lufs": -18.0,
        "tp_dbtp": -1.0,
        "shortfall": False,
    }
    report = json.loads(summary.report_path.read_text(encoding="utf-8"))
    assert report["format_version"] == 2
    assert report["gate_policy_version"] == "take-gates-v2"
    assert report["summary"]["content_review_required"] == 0
    assert report["attempts"][0]["content"]["review_reason"] is None
    assert report["runtime"]["status"] == "ready"
    assert report["attempts"][0]["mechanical"]["sidecar_provenance"] == {
        "generation_seconds": 1.0,
        "postprocess": PostprocessProfile().as_dict(),
        "toolchain": TOOLS.as_identity(),
        "loudness": {
            "normalized_wav": {"integrated_lufs": -18.0},
            "encoded_opus": {"integrated_lufs": -18.0},
        },
    }
    assert report["attempts"][0]["content"]["prosody"]["pause"] == {
        "internal_count": 1,
    }


def test_explicit_reading_mismatch_is_review_required_and_in_snapshot(
    tmp_path: Path,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)

    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("ウチノマーボーワツライヨカクゴシナ"),
    )

    attempt = read_ledger(ledger_path)["attempts"][0]
    assert attempt["status"] == "eligible"
    assert attempt["gates"] == {
        "mechanical": "pass",
        "content": "review_required",
    }
    assert summary.eligible_count == 1
    assert summary.content_review_required_count == 1
    assert summary.snapshot_path is not None
    report = json.loads(summary.report_path.read_text(encoding="utf-8"))
    content = report["attempts"][0]["content"]
    assert content["status"] == "review_required"
    assert content["review_reason"] == "explicit_reading_mismatch"
    assert content["reading"]["reading_mismatch"] is True
    assert report["summary"]["content_review_required"] == 1
    manifest = json.loads(summary.snapshot_path.read_text(encoding="utf-8"))
    assert len(manifest["candidates"]) == 1
    assert manifest["candidates"][0]["gate"]["content"] == "review_required"
    assert manifest["failures"] == []


@pytest.mark.parametrize("failure", ["format", "loudness"])
def test_mechanical_failure_short_circuits_content_and_uses_not_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    runtime = FakeRuntime("unused")
    if failure == "format":
        monkeypatch.setattr(
            qc,
            "probe_audio",
            lambda _tools, path: (
                AudioProbe("mp3", 44_100, 2, 2.0)
                if path.suffix == ".opus"
                else AudioProbe("pcm_s16le", 48_000, 1, 2.0)
            ),
        )
    else:
        monkeypatch.setattr(
            qc,
            "measure_encoded_opus",
            lambda *_args: (_ for _ in ()).throw(
                AudioProcessingError(
                    "エンコード後 Opus が loudness/true-peak profile を満たしません。",
                ),
            ),
        )

    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=runtime,
    )

    assert runtime.prepare_calls == 0
    assert runtime.inspect_calls == 0
    attempt = read_ledger(ledger_path)["attempts"][0]
    assert attempt["status"] == "hard_rejected"
    assert attempt["gates"] == {
        "mechanical": "reject",
        "content": "not_run",
    }
    report = json.loads(summary.report_path.read_text(encoding="utf-8"))
    assert report["attempts"][0]["content"]["status"] == "not_run"
    assert summary.snapshot_path is not None


def test_active_speech_zero_is_mechanical_reject(tmp_path: Path) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    runtime = FakeRuntime("カナ", active_speech_sec=0.0)

    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=runtime,
    )

    assert runtime.inspect_calls == 1
    attempt = read_ledger(ledger_path)["attempts"][0]
    assert attempt["status"] == "hard_rejected"
    assert attempt["gates"] == {
        "mechanical": "reject",
        "content": "not_run",
    }
    report = json.loads(summary.report_path.read_text(encoding="utf-8"))
    assert "active_speech_sec" in report["attempts"][0]["mechanical"]["reason"]


def test_derived_reading_is_review_required_not_pass_or_reject(
    tmp_path: Path,
) -> None:
    run_id, ledger_path = _write_generated_run(
        tmp_path,
        line_id="tenshin-okami-001",
    )

    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("ムシタテノシューマイダヨアツアツダヨー"),
    )

    attempt = read_ledger(ledger_path)["attempts"][0]
    assert attempt["status"] == "eligible"
    assert attempt["gates"] == {
        "mechanical": "pass",
        "content": "review_required",
    }
    assert summary.eligible_count == 1
    assert summary.content_review_required_count == 1
    assert summary.snapshot_path is not None
    candidate = json.loads(summary.snapshot_path.read_text(encoding="utf-8"))[
        "candidates"
    ][0]
    assert candidate["gate"]["content"] == "review_required"
    report = json.loads(summary.report_path.read_text(encoding="utf-8"))
    content = report["attempts"][0]["content"]
    assert content["review_reason"] == "non_authoritative_expected_reading"
    assert content["reading"]["reading_mismatch"] is None


@pytest.mark.parametrize("stage", ["prepare", "inspect", "empty_transcript"])
def test_runtime_failure_is_blocked_and_never_writes_snapshot(
    tmp_path: Path,
    stage: str,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    runtime = FakeRuntime(
        "" if stage == "empty_transcript" else "unused",
        prepare_error=RuntimeError("load failed") if stage == "prepare" else None,
        inspect_error=RuntimeError("asr failed") if stage == "inspect" else None,
    )
    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=runtime,
    )

    attempt = read_ledger(ledger_path)["attempts"][0]
    assert attempt["status"] == "blocked"
    assert "blocked" in attempt["gates"].values()
    assert summary.blocked_count == 1
    assert summary.snapshot_path is None


def test_provenance_hash_mismatch_is_blocked_before_runtime(tmp_path: Path) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    ledger = read_ledger(ledger_path)
    opus_path = ledger_path.parent / ledger["attempts"][0]["audio"]["opus_path"]
    opus_path.write_bytes(b"tampered")
    runtime = FakeRuntime("unused")

    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=runtime,
    )

    assert runtime.prepare_calls == 0
    assert runtime.inspect_calls == 0
    assert read_ledger(ledger_path)["attempts"][0]["status"] == "blocked"
    assert summary.snapshot_path is None


def test_audio_changed_during_runtime_is_blocked_before_gate_transition(
    tmp_path: Path,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)

    class MutatingRuntime(FakeRuntime):
        def inspect(
            self,
            audio_path: Path,
            *,
            mora_count: int,
        ) -> RuntimeInspection:
            inspection = super().inspect(audio_path, mora_count=mora_count)
            audio_path.write_bytes(b"changed during runtime")
            return inspection

    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=MutatingRuntime("ウチノマーボーワカライヨカクゴシナ"),
    )

    attempt = read_ledger(ledger_path)["attempts"][0]
    assert attempt["status"] == "blocked"
    assert attempt["gates"] == {
        "mechanical": "blocked",
        "content": "not_run",
    }
    assert summary.snapshot_path is None


def test_report書込後にaudioが変化したらsnapshotを確定しない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    ledger = read_ledger(ledger_path)
    opus_path = ledger_path.parent / ledger["attempts"][0]["audio"]["opus_path"]
    real_write = qc._atomic_write_json

    def mutate_after_report(path: Path, payload: dict[str, Any]) -> None:
        real_write(path, payload)
        if path.name == "qc-report.json":
            opus_path.write_bytes(b"changed after report")

    monkeypatch.setattr(qc, "_atomic_write_json", mutate_after_report)

    with pytest.raises(QCError, match="snapshot 再検証"):
        run_qc(
            run_id=run_id,
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=tmp_path / "artifacts",
            runtime=FakeRuntime("ウチノマーボーワカライヨカクゴシナ"),
        )

    assert not (ledger_path.parent / "manifest-v4.json").exists()


def test_sidecarの生成parameter改変はledger_hash_joinでblocked(
    tmp_path: Path,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    ledger = read_ledger(ledger_path)
    opus_path = ledger_path.parent / ledger["attempts"][0]["audio"]["opus_path"]
    sidecar_path = opus_path.with_suffix(".json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["gen_params"]["requested"]["temperature"] = 9.0
    sidecar_path.write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("must not inspect"),
    )

    assert summary.blocked_count == 1
    assert summary.snapshot_path is None
    assert read_ledger(ledger_path)["attempts"][0]["gates"] == {
        "mechanical": "blocked",
        "content": "not_run",
    }


def test_planned_attempt_keeps_run_nonterminal_without_runtime_or_snapshot(
    tmp_path: Path,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    ledger = read_ledger(ledger_path)
    attempt = ledger["attempts"][0]
    ledger["attempts"][0] = {
        "model": attempt["model"],
        "scenario": attempt["scenario"],
        "line": attempt["line"],
        "variant": attempt["variant"],
        "take_index": attempt["take_index"],
        "generation_input_sha256": attempt["generation_input_sha256"],
        "generation": {
            "status": "planned",
            "seed": attempt["generation"]["seed"],
            "sampling": attempt["generation"]["sampling"],
        },
        "status": "planned",
    }
    write_ledger_atomic(ledger_path, ledger)
    runtime = FakeRuntime("unused")

    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=runtime,
    )

    assert runtime.prepare_calls == 0
    assert runtime.inspect_calls == 0
    assert summary.pending_count == 1
    assert summary.snapshot_path is None


def test_generation_failedだけのterminal_runは音声tool不要でsnapshot化(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    ledger = read_ledger(ledger_path)
    attempt = ledger["attempts"][0]
    ledger["attempts"][0] = {
        "model": attempt["model"],
        "scenario": attempt["scenario"],
        "line": attempt["line"],
        "variant": attempt["variant"],
        "take_index": attempt["take_index"],
        "generation_input_sha256": attempt["generation_input_sha256"],
        "generation": {
            "status": "failed",
            "seed": attempt["generation"]["seed"],
            "sampling": attempt["generation"]["sampling"],
            "error": "generation failed",
        },
        "status": "generation_failed",
    }
    write_ledger_atomic(ledger_path, ledger)
    monkeypatch.setattr(
        qc,
        "find_audio_tools",
        lambda: (_ for _ in ()).throw(AssertionError("must not load tools")),
    )
    runtime = FakeRuntime("must not inspect")

    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=runtime,
    )

    assert summary.generation_failed_count == 1
    assert summary.snapshot_path is not None
    manifest = json.loads(summary.snapshot_path.read_text(encoding="utf-8"))
    assert manifest["candidates"] == []
    assert manifest["failures"][0]["reason"] == "no_eligible_take"
    assert runtime.prepare_calls == 0
    assert runtime.inspect_calls == 0


def test_generation_failedだけのterminal_snapshot再実行もv2_reportを先に要求(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    ledger = read_ledger(ledger_path)
    attempt = ledger["attempts"][0]
    ledger["attempts"][0] = {
        "model": attempt["model"],
        "scenario": attempt["scenario"],
        "line": attempt["line"],
        "variant": attempt["variant"],
        "take_index": attempt["take_index"],
        "generation_input_sha256": attempt["generation_input_sha256"],
        "generation": {
            "status": "failed",
            "seed": attempt["generation"]["seed"],
            "sampling": attempt["generation"]["sampling"],
            "error": "generation failed",
        },
        "status": "generation_failed",
    }
    write_ledger_atomic(ledger_path, ledger)
    monkeypatch.setattr(
        qc,
        "find_audio_tools",
        lambda: (_ for _ in ()).throw(AssertionError("must not load tools")),
    )

    first = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("must not inspect"),
    )
    assert first.snapshot_path is not None
    assert first.candidate_set_path is not None
    assert first.candidate_set_marker_path is not None
    watched = (
        first.snapshot_path,
        first.candidate_set_path,
        first.candidate_set_marker_path,
    )
    before = {path: path.read_bytes() for path in watched}
    first.report_path.unlink()

    with pytest.raises(QCError, match="完全な v2 QC report"):
        run_qc(
            run_id=run_id,
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=tmp_path / "artifacts",
            runtime=FakeRuntime("must not inspect"),
        )

    assert {path: path.read_bytes() for path in watched} == before


def test_generatedとgeneration_failedの混在はtool不足をstrict_reportへ記録する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path, takes=2)
    ledger = read_ledger(ledger_path)
    failed = ledger["attempts"][1]
    ledger["attempts"][1] = {
        "model": failed["model"],
        "scenario": failed["scenario"],
        "line": failed["line"],
        "variant": failed["variant"],
        "take_index": failed["take_index"],
        "generation_input_sha256": failed["generation_input_sha256"],
        "generation": {
            "status": "failed",
            "seed": failed["generation"]["seed"],
            "sampling": failed["generation"]["sampling"],
            "error": "generation failed",
        },
        "status": "generation_failed",
    }
    write_ledger_atomic(ledger_path, ledger)
    monkeypatch.setattr(
        qc,
        "find_audio_tools",
        lambda: (_ for _ in ()).throw(AudioProcessingError("tools unavailable")),
    )

    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("must not inspect"),
    )

    assert summary.blocked_count == 1
    assert summary.generation_failed_count == 1
    assert summary.snapshot_path is None
    report = json.loads(summary.report_path.read_text(encoding="utf-8"))
    failed_report = next(
        attempt
        for attempt in report["attempts"]
        if attempt["status"] == "generation_failed"
    )
    assert failed_report["mechanical"] == {
        "status": "not_run",
        "reason": "tools unavailable",
    }
    assert failed_report["content"] == {"status": "not_run"}


def test_terminal_rerun_does_not_prepare_or_inspect_runtime(tmp_path: Path) -> None:
    run_id, _ledger_path = _write_generated_run(tmp_path)
    first = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("ウチノマーボーワカライヨカクゴシナ"),
    )
    assert first.snapshot_path is not None
    first_report = json.loads(first.report_path.read_text(encoding="utf-8"))
    first_content = first_report["attempts"][0]["content"]
    runtime = FakeRuntime(
        "must not inspect",
        prepare_error=AssertionError("must not prepare"),
        inspect_error=AssertionError("must not inspect"),
    )

    second = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=runtime,
    )

    assert runtime.prepare_calls == 0
    assert runtime.inspect_calls == 0
    assert second.eligible_count == 1
    assert second.snapshot_path is not None
    second_report = json.loads(second.report_path.read_text(encoding="utf-8"))
    assert second_report["attempts"][0]["content"] == first_content
    assert set(second_report["attempts"][0]["mechanical"]) == {
        "status",
        "duration_sec",
        "wav",
        "opus",
        "loudness",
        "generation_params",
        "sidecar_provenance",
    }
    validate_manifest_v4(
        json.loads(second.snapshot_path.read_text(encoding="utf-8")),
    )


def test_v2_report_rejects_terminal_content_stub(tmp_path: Path) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("ウチノマーボーワカライヨカクゴシナ"),
    )
    ledger = read_ledger(ledger_path)
    report = json.loads(summary.report_path.read_text(encoding="utf-8"))
    report["attempts"][0]["content"] = {
        "status": "pass",
        "inspection": "terminal_not_repeated",
    }

    with pytest.raises(QCReportError, match="exact contract"):
        validate_qc_report(report, ledger_path=ledger_path, ledger=ledger)


def test_v2_report_rejects_content_hard_reject(tmp_path: Path) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("ウチノマーボーワカライヨカクゴシナ"),
    )
    ledger = read_ledger(ledger_path)
    ledger["attempts"][0]["status"] = "hard_rejected"
    ledger["attempts"][0]["gates"] = {
        "mechanical": "pass",
        "content": "reject",
    }
    report = json.loads(summary.report_path.read_text(encoding="utf-8"))
    report["summary"]["eligible"] = 0
    report["summary"]["hard_rejected"] = 1
    report["attempts"][0]["status"] = "hard_rejected"
    report["attempts"][0]["gates"] = {
        "mechanical": "pass",
        "content": "reject",
    }
    report["attempts"][0]["content"]["status"] = "reject"

    with pytest.raises(QCReportError, match="mechanical reject"):
        validate_qc_report(report, ledger_path=ledger_path, ledger=ledger)


@pytest.mark.parametrize(
    "mutation",
    [
        "review_reason",
        "authoritative",
        "reading_mismatch",
        "normalized_reading",
    ],
)
def test_v2_report_rejects_inconsistent_pass_evidence(
    tmp_path: Path,
    mutation: str,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("ウチノマーボーワカライヨカクゴシナ"),
    )
    ledger = read_ledger(ledger_path)
    report = json.loads(summary.report_path.read_text(encoding="utf-8"))
    content = report["attempts"][0]["content"]
    if mutation == "review_reason":
        content["review_reason"] = "explicit_reading_mismatch"
    elif mutation == "authoritative":
        content["expected_reading"]["authoritative"] = False
    elif mutation == "reading_mismatch":
        content["reading"]["reading_mismatch"] = True
    else:
        content["asr"]["normalized_reading"] = "フイッチ"

    with pytest.raises(QCReportError, match="pass 判定根拠"):
        validate_qc_report(report, ledger_path=ledger_path, ledger=ledger)


@pytest.mark.parametrize(
    "mutation",
    [
        "review_reason",
        "authoritative",
        "reading_mismatch",
        "normalized_reading",
    ],
)
def test_v2_report_rejects_inconsistent_explicit_mismatch_evidence(
    tmp_path: Path,
    mutation: str,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("ウチノマーボーワツライヨカクゴシナ"),
    )
    ledger = read_ledger(ledger_path)
    report = json.loads(summary.report_path.read_text(encoding="utf-8"))
    content = report["attempts"][0]["content"]
    if mutation == "review_reason":
        content["review_reason"] = None
    elif mutation == "authoritative":
        content["expected_reading"]["authoritative"] = False
    elif mutation == "reading_mismatch":
        content["reading"]["reading_mismatch"] = False
    else:
        content["asr"]["normalized_reading"] = content["expected_reading"]["normalized"]

    with pytest.raises(QCReportError, match="判定根拠"):
        validate_qc_report(report, ledger_path=ledger_path, ledger=ledger)


def test_terminal_eligibleのmechanical再検証失敗はsnapshotを無効化(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    first = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("ウチノマーボーワカライヨカクゴシナ"),
    )
    assert first.snapshot_path is not None
    monkeypatch.setattr(
        qc,
        "measure_encoded_opus",
        lambda *_args: (_ for _ in ()).throw(
            AudioProcessingError("terminal audio is invalid"),
        ),
    )

    with pytest.raises(QCError, match="mechanical 再検証"):
        run_qc(
            run_id=run_id,
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=tmp_path / "artifacts",
            runtime=FakeRuntime("must not inspect"),
        )

    assert not (ledger_path.parent / "manifest-v4.json").exists()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing", "完全な v2 QC report"),
        ("format_v1", "format_version"),
        ("policy_v1", "gate_policy_version"),
    ],
)
def test_terminal_rerun_requires_existing_v2_report_before_snapshot_invalidation(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    first = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("ウチノマーボーワカライヨカクゴシナ"),
    )
    assert first.snapshot_path is not None
    assert first.candidate_set_path is not None
    assert first.candidate_set_marker_path is not None
    watched = (
        first.snapshot_path,
        first.candidate_set_path,
        first.candidate_set_marker_path,
    )
    before = {path: path.read_bytes() for path in watched}
    report_path = ledger_path.parent / "qc-report.json"
    if mutation == "missing":
        report_path.unlink()
    else:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if mutation == "format_v1":
            report["format_version"] = 1
        else:
            report["gate_policy_version"] = "take-gates-v1"
        report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(QCError, match=message):
        run_qc(
            run_id=run_id,
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=tmp_path / "artifacts",
            runtime=FakeRuntime("must not inspect"),
        )

    assert {path: path.read_bytes() for path in watched} == before


def test_qc_cliはrun_idだけを入力にする() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["qc", "--run-id", "run-1"]).run_id == "run-1"
    with pytest.raises(SystemExit):
        parser.parse_args(["qc"])
    with pytest.raises(SystemExit):
        parser.parse_args(["qc", "--run-id", "run-1", "--manifest", "v3.json"])


def test_qcはrun_idのpath_escapeを入力読込前に拒否(tmp_path: Path) -> None:
    runtime = FakeRuntime("must not inspect")
    with pytest.raises(QCError, match="path segment"):
        run_qc(
            run_id="../outside",
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=tmp_path / "artifacts",
            runtime=runtime,
        )
    assert runtime.prepare_calls == 0
    assert runtime.inspect_calls == 0


def test_each_transition_is_checkpointed_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path, takes=2)
    real_write = qc.write_ledger_atomic
    checkpoints: list[list[str]] = []

    def recording_write(path: Path, ledger: dict[str, Any]) -> None:
        real_write(path, ledger)
        checkpoints.append(
            [str(attempt["status"]) for attempt in ledger["attempts"]],
        )

    monkeypatch.setattr(qc, "write_ledger_atomic", recording_write)

    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("ウチノマーボーワカライヨカクゴシナ"),
    )

    assert checkpoints == [
        ["eligible", "generated"],
        ["eligible", "eligible"],
    ]
    assert summary.eligible_count == 2
    assert read_ledger(ledger_path)["attempts"][1]["status"] == "eligible"


def _write_generated_run(
    tmp_path: Path,
    *,
    line_id: str = "shokudo-oyaji-002",
    takes: int = 1,
) -> tuple[str, Path]:
    run_id = "20260729T000000000000Z-dummy-n1"
    artifacts_dir = tmp_path / "artifacts"
    run_root = artifacts_dir / "takes" / run_id
    scenario_id = "chinatown-street"
    source_path = SCENARIOS_DIR / f"{scenario_id}.yaml"
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    group = {
        "model": "dummy",
        "scenario": scenario_id,
        "line": line_id,
        "variant": "dry",
    }
    attempts: list[dict[str, Any]] = []
    for take_index in range(1, takes + 1):
        generation_input_sha = hashlib.sha256(
            f"input-{take_index}".encode(),
        ).hexdigest()
        root = (
            f"audio/dummy/{scenario_id}/{line_id}/dry/"
            f"take-{take_index:04d}"
        )
        wav_path = run_root / f"{root}.wav"
        opus_path = run_root / f"{root}.opus"
        sidecar_path = run_root / f"{root}.json"
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        wav_bytes = ARTIFACT_BYTES + f"-wav-{take_index}".encode()
        opus_bytes = ARTIFACT_BYTES + f"-opus-{take_index}".encode()
        wav_path.write_bytes(wav_bytes)
        opus_path.write_bytes(opus_bytes)
        wav_sha = hashlib.sha256(wav_bytes).hexdigest()
        opus_sha = hashlib.sha256(opus_bytes).hexdigest()
        take_id = make_take_id(
            generation_input_sha256=generation_input_sha,
            final_opus_sha256=opus_sha,
        )
        generation = {
            "status": "succeeded",
            "seed": take_index,
            "sampling": {"temperature": 1.0},
            "rtf": 0.5,
        }
        attempt = {
            **group,
            "take_index": take_index,
            "take_id": take_id,
            "generation_input_sha256": generation_input_sha,
            "generation": generation,
            "audio": {
                "wav_path": f"{root}.wav",
                "wav_sha256": wav_sha,
                "opus_path": f"{root}.opus",
                "opus_sha256": opus_sha,
                "sidecar_sha256": "0" * 64,
            },
            "gates": {},
            "features": {"status": "unscored"},
            "status": "generated",
        }
        sidecar = {
            "format_version": 1,
            "run_id": run_id,
            **group,
            "take_index": take_index,
            "take_id": take_id,
            "generation_input_sha256": generation_input_sha,
            "wav_sha256": wav_sha,
            "opus_sha256": opus_sha,
            "duration_sec": 2.0,
            "generation_seconds": 1.0,
            "rtf": 0.5,
            "take": {
                "seed": take_index,
                "recipe_version": "dummy-takes-v1",
                "sampling": {"temperature": 1.0},
            },
            "gen_params": {
                "requested": {"temperature": 1.0},
                "realized": {"temperature": 1.0},
            },
            "postprocess": PostprocessProfile().as_dict(),
            "toolchain": TOOLS.as_identity(),
            "loudness": {
                "normalized_wav": {"integrated_lufs": -18.0},
                "encoded_opus": {"integrated_lufs": -18.0},
            },
        }
        validate_take_sidecar(sidecar)
        sidecar_path.write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        attempt["audio"]["sidecar_sha256"] = hashlib.sha256(
            sidecar_path.read_bytes(),
        ).hexdigest()
        attempts.append(attempt)

    ledger = {
        "format_version": 1,
        "run_id": run_id,
        "created_at": "2026-07-29T00:00:00Z",
        "source": {
            "scenario_sha256": hashlib.sha256(
                canonical_json(
                    [{"path": source_path.name, "sha256": source_sha}],
                ).encode(),
            ).hexdigest(),
            "model": "dummy",
            "takes": takes,
            "seed_base": 0,
            "recipe_version": "dummy-takes-v1",
            "groups": [group],
        },
        "attempts": attempts,
    }
    ledger_path = run_root / "ledger.json"
    write_ledger_atomic(ledger_path, ledger)
    return run_id, ledger_path


def test_terminal_snapshotはcanonical_candidate_setを先に書く(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, _ledger_path = _write_generated_run(tmp_path)
    writes: list[str] = []
    real_write = qc._atomic_write_bytes

    def record_write(path: Path, payload: bytes) -> None:
        writes.append(path.name)
        real_write(path, payload)

    monkeypatch.setattr(qc, "_atomic_write_bytes", record_write)
    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("ウチノマーボーワカライヨカクゴシナ"),
    )

    assert summary.snapshot_path is not None
    assert summary.candidate_set_path is not None
    assert summary.candidate_set_marker_path is not None
    manifest = json.loads(summary.snapshot_path.read_text(encoding="utf-8"))
    candidate_set = json.loads(summary.candidate_set_path.read_text(encoding="utf-8"))
    candidate_bytes = canonical_candidate_set_bytes(candidate_set)
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
    assert summary.candidate_set_path.read_bytes() == candidate_bytes
    assert summary.candidate_set_marker_path.read_bytes() == candidate_sha.encode("ascii")
    assert manifest["candidate_set_sha256"] == candidate_sha
    assert set(candidate_set) == {
        "format_version",
        "scenario_sha256",
        "lines",
        "models",
        "candidates",
        "failures",
    }
    assert candidate_set["lines"] == [
        {
            "scenario": "chinatown-street",
            "line": "shokudo-oyaji-002",
            "scenario_title": "中華街・大通りの夕暮れ",
            "text": "うちの麻婆は辛いよ、覚悟しな！",
            "delivery": "豪快な笑いを含んだ脅し文句。だみ声が楽しげに揺れる。",
        },
    ]
    assert writes.index("candidate-set.json") < writes.index("manifest-v4.json")
    assert writes.index("candidate-set.sha256") < writes.index("manifest-v4.json")
    assert writes.index("candidate-set.json") < writes.index("candidate-set.sha256")


def test_snapshot再検証のloudnessをreportとcandidateの同一authorityにする(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, _ledger_path = _write_generated_run(tmp_path)
    measurements = iter(
        [
            EncodedLoudnessReport(-18.0, -1.0, 4.0),
            EncodedLoudnessReport(-17.7, -1.0, 4.0),
        ],
    )
    monkeypatch.setattr(
        qc,
        "measure_encoded_opus",
        lambda _tools, _path, _profile: next(measurements),
    )

    summary = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("ウチノマーボーワカライヨカクゴシナ"),
    )

    assert summary.snapshot_path is not None
    manifest = json.loads(summary.snapshot_path.read_text(encoding="utf-8"))
    report = json.loads(summary.report_path.read_text(encoding="utf-8"))
    candidate = manifest["candidates"][0]
    assert report["attempts"][0]["mechanical"]["loudness"]["i_lufs"] == -17.7
    assert candidate["loudness"]["i_lufs"] == -17.7
    curation = {
        "format_version": 1,
        "rubric_version": "take-curation-v1",
        "candidate_set_sha256": manifest["candidate_set_sha256"],
        "groups": [
            {
                "model": candidate["model"],
                "scenario": candidate["scenario"],
                "line": candidate["line"],
                "variant": candidate["variant"],
                "candidates": [
                    {
                        "take_id": candidate["take_id"],
                        "path": candidate["path"],
                        "audio_sha256": candidate["sha256"],
                        "rubric": {
                            "content_correct": True,
                            "intent_match": 4,
                            "character_naturalness": 4,
                            "adoptable": True,
                        },
                    },
                ],
                "decision": {
                    "type": "selected",
                    "take_id": candidate["take_id"],
                },
            },
        ],
    }
    input_path = tmp_path / "curation-drift.json"
    input_path.write_text(json.dumps(curation), encoding="utf-8")

    applied = apply_curation(
        run_id=run_id,
        input_path=input_path,
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )

    assert applied.added_projection_count == 1


def test_QC_apply_QCは新runを要求してbundleとartifactのbytesを保持(
    tmp_path: Path,
) -> None:
    run_id, _ledger_path = _write_generated_run(tmp_path)
    first_qc = run_qc(
        run_id=run_id,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=tmp_path / "artifacts",
        runtime=FakeRuntime("ウチノマーボーワカライヨカクゴシナ"),
    )
    assert first_qc.snapshot_path is not None
    assert first_qc.candidate_set_path is not None
    assert first_qc.candidate_set_marker_path is not None
    manifest = json.loads(first_qc.snapshot_path.read_text(encoding="utf-8"))
    candidate = manifest["candidates"][0]
    curation = {
        "format_version": 1,
        "rubric_version": "take-curation-v1",
        "candidate_set_sha256": manifest["candidate_set_sha256"],
        "groups": [
            {
                "model": candidate["model"],
                "scenario": candidate["scenario"],
                "line": candidate["line"],
                "variant": candidate["variant"],
                "candidates": [
                    {
                        "take_id": candidate["take_id"],
                        "path": candidate["path"],
                        "audio_sha256": candidate["sha256"],
                        "rubric": {
                            "content_correct": True,
                            "intent_match": 4,
                            "character_naturalness": 4,
                            "adoptable": True,
                        },
                    },
                ],
                "decision": {
                    "type": "selected",
                    "take_id": candidate["take_id"],
                },
            },
        ],
    }
    input_path = tmp_path / "curation.json"
    input_path.write_text(json.dumps(curation), encoding="utf-8")
    applied = apply_curation(
        run_id=run_id,
        input_path=input_path,
        artifacts_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        scenarios_dir=SCENARIOS_DIR,
    )
    watched = (
        first_qc.snapshot_path,
        first_qc.candidate_set_marker_path,
        first_qc.candidate_set_path,
        applied.artifact_path,
    )
    before = {path: path.read_bytes() for path in watched}

    with pytest.raises(QCError, match="新しい generation run"):
        run_qc(
            run_id=run_id,
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=tmp_path / "artifacts",
            runtime=FakeRuntime("unused"),
        )

    assert {path: path.read_bytes() for path in watched} == before


def test_qc開始時は壊れたsnapshot_bundleを無変更で拒否(
    tmp_path: Path,
) -> None:
    run_id, ledger_path = _write_generated_run(tmp_path)
    snapshot_path = ledger_path.parent / "manifest-v4.json"
    candidate_set_path = ledger_path.parent / "candidate-set.json"
    marker_path = ledger_path.parent / "candidate-set.sha256"
    snapshot_path.write_bytes(b"stale manifest")
    candidate_set_path.write_bytes(b"stale candidate set")
    marker_path.write_bytes(b"0" * 64)
    before = {
        path: path.read_bytes()
        for path in (snapshot_path, marker_path, candidate_set_path)
    }

    with pytest.raises(QCError, match="不正"):
        run_qc(
            run_id=run_id,
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=tmp_path / "artifacts",
            runtime=FakeRuntime("unused", prepare_error=RuntimeError("load failed")),
        )

    assert {path: path.read_bytes() for path in before} == before


def test_三文件状态不完整时ledger読込失敗でも無変更(tmp_path: Path) -> None:
    run_root = tmp_path / "artifacts" / "takes" / "broken-run"
    run_root.mkdir(parents=True)
    snapshot_path = run_root / "manifest-v4.json"
    candidate_set_path = run_root / "candidate-set.json"
    snapshot_path.write_bytes(b"stale manifest")
    candidate_set_path.write_bytes(b"stale candidate set")
    before = (snapshot_path.read_bytes(), candidate_set_path.read_bytes())

    with pytest.raises(QCError, match="run ledger"):
        run_qc(
            run_id="broken-run",
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=tmp_path / "artifacts",
            runtime=FakeRuntime("unused"),
        )

    assert (snapshot_path.read_bytes(), candidate_set_path.read_bytes()) == before
