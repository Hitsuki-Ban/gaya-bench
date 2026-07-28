from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from gaya_pipeline import cli
from gaya_pipeline.qc import (
    QCError,
    QCSummary,
    RuntimeInspection,
    count_japanese_mora,
    run_qc,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"


class FakeRuntime:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.prepared = False
        self.mora_counts: list[int] = []

    def prepare(self) -> None:
        self.prepared = True

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
        assert audio_path.is_file()
        self.mora_counts.append(mora_count)
        return RuntimeInspection(
            transcript=self.transcript,
            average_log_probability=-0.25,
            prosody={
                "duration_sec": 2.9,
                "estimated_mora_count": mora_count,
                "f0": {"median_hz": 155.0},
            },
        )


def _write_manifest(
    path: Path,
    audio_bytes: bytes,
    *,
    scenario: str = "chinatown-street",
    line: str = "shokudo-oyaji-002",
) -> None:
    path.parent.mkdir(parents=True)
    document = {
        "format_version": 3,
        "generated_at": "2026-07-28T00:00:00+00:00",
        "models": [
            {
                "id": "qwen3-tts-12hz-1.7b",
                "name": "Qwen3-TTS",
                "version": "test",
                "license_note": "test",
                "capabilities": {
                    "emotion": False,
                    "voice_prompt": True,
                    "clone": True,
                    "nonverbal": False,
                    "reading": False,
                },
            },
        ],
        "clips": [
            {
                "model": "qwen3-tts-12hz-1.7b",
                "scenario": scenario,
                "line": line,
                "variant": "dry",
                "path": (
                    f"audio/qwen3-tts-12hz-1.7b/{scenario}/"
                    f"{line}-dry.opus"
                ),
                "duration_sec": 2.9,
                "sha256": hashlib.sha256(audio_bytes).hexdigest(),
                "gen_params": {},
                "rtf": 5.5,
                "loudness": {
                    "source": "encoded_opus",
                    "i_lufs": -18.0,
                    "tp_dbtp": -4.0,
                    "shortfall": False,
                },
            },
        ],
        "failures": [],
    }
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_qc_detects_maabo_wrong_reading_without_mutating_dry_artifacts(
    tmp_path: Path,
) -> None:
    audio_bytes = b"ignored test audio"
    artifacts_dir = tmp_path / "artifacts"
    audio_path = (
        artifacts_dir
        / "audio"
        / "qwen3-tts-12hz-1.7b"
        / "chinatown-street"
        / "shokudo-oyaji-002-dry.opus"
    )
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(audio_bytes)
    manifest_path = tmp_path / "data" / "manifest.json"
    _write_manifest(manifest_path, audio_bytes)
    manifest_before = manifest_path.read_bytes()
    runtime = FakeRuntime(
        "ウチノマーボーワツライヨカクゴシナ",
    )
    output_path = artifacts_dir / "qc" / "report.json"

    summary = run_qc(
        manifest_path=manifest_path,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=artifacts_dir,
        output_path=output_path,
        runtime=runtime,
    )

    assert runtime.prepared
    assert runtime.mora_counts == [
        count_japanese_mora("ウチノマーボーワカライヨ、カクゴシナ！"),
    ]
    assert summary.mismatch_count == 1
    assert summary.analysis_error_count == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["format_version"] == 1
    assert report["source"]["clip_set"] == "manifest.clips"
    assert report["source"]["selection"]["coverage"] == "full"
    assert report["summary"]["clip_count"] == 1
    clip = report["clips"][0]
    assert clip["expected_reading"]["source"] == "line.reading"
    assert clip["asr"]["normalized_reading"] == (
        "ウチノマーボーワツライヨカクゴシナ"
    )
    assert clip["status"] == "mismatch"
    assert clip["reading"]["reading_mismatch"] is True
    assert clip["reading"]["reason"] == (
        "ambiguous_reading:辛い:カライ->ツライ"
    )
    assert clip["reading"]["suggested_reading"] == (
        "ウチノマーボーワカライヨ、カクゴシナ！"
    )
    assert clip["prosody"]["f0"]["median_hz"] == 155.0
    assert manifest_path.read_bytes() == manifest_before
    assert audio_path.read_bytes() == audio_bytes


def test_qc_rejects_hash_mismatch_before_runtime_prepare(tmp_path: Path) -> None:
    audio_bytes = b"changed"
    artifacts_dir = tmp_path / "artifacts"
    audio_path = (
        artifacts_dir
        / "audio"
        / "qwen3-tts-12hz-1.7b"
        / "chinatown-street"
        / "shokudo-oyaji-002-dry.opus"
    )
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(audio_bytes)
    manifest_path = tmp_path / "data" / "manifest.json"
    _write_manifest(manifest_path, b"expected")
    runtime = FakeRuntime("unused")

    output_path = artifacts_dir / "qc" / "report.json"
    with pytest.raises(QCError, match="SHA-256"):
        run_qc(
            manifest_path=manifest_path,
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=artifacts_dir,
            output_path=output_path,
            runtime=runtime,
        )

    assert not runtime.prepared
    assert runtime.mora_counts == []
    assert not output_path.exists()


def test_qc_marks_derived_reading_as_review_required(tmp_path: Path) -> None:
    audio_bytes = b"ignored test audio"
    scenario = "castle-gate"
    line = "guard-otoko-001"
    artifacts_dir = tmp_path / "artifacts"
    audio_path = (
        artifacts_dir
        / "audio"
        / "qwen3-tts-12hz-1.7b"
        / scenario
        / f"{line}-dry.opus"
    )
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(audio_bytes)
    manifest_path = tmp_path / "data" / "manifest.json"
    _write_manifest(
        manifest_path,
        audio_bytes,
        scenario=scenario,
        line=line,
    )

    summary = run_qc(
        manifest_path=manifest_path,
        scenarios_dir=SCENARIOS_DIR,
        artifacts_dir=artifacts_dir,
        output_path=artifacts_dir / "qc" / "report.json",
        runtime=FakeRuntime("トマレナニモノダナオナノレ"),
        scenario_id=scenario,
        line_id=line,
    )

    assert summary.mismatch_count == 0
    assert summary.review_required_count == 1
    report = json.loads(summary.output_path.read_text(encoding="utf-8"))
    assert report["clips"][0]["status"] == "review_required"
    assert report["clips"][0]["reading"]["reading_mismatch"] is None
    assert report["source"]["clip_set"] == "manifest.clips.selection"
    assert report["source"]["selection"] == {
        "coverage": "filtered",
        "model": None,
        "scenario": scenario,
        "line": line,
    }


@pytest.mark.parametrize("protected_target", ["manifest", "audio"])
def test_qc_rejects_output_that_overwrites_inputs(
    tmp_path: Path,
    protected_target: str,
) -> None:
    audio_bytes = b"ignored test audio"
    artifacts_dir = tmp_path / "artifacts"
    audio_path = (
        artifacts_dir
        / "audio"
        / "qwen3-tts-12hz-1.7b"
        / "chinatown-street"
        / "shokudo-oyaji-002-dry.opus"
    )
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(audio_bytes)
    manifest_path = tmp_path / "data" / "manifest.json"
    _write_manifest(manifest_path, audio_bytes)
    protected_path = manifest_path if protected_target == "manifest" else audio_path
    before = protected_path.read_bytes()
    runtime = FakeRuntime("unused")

    with pytest.raises(QCError, match="同一にできません"):
        run_qc(
            manifest_path=manifest_path,
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=artifacts_dir,
            output_path=protected_path,
            runtime=runtime,
        )

    assert not runtime.prepared
    assert protected_path.read_bytes() == before


def test_qc_rejects_audio_changed_during_analysis(tmp_path: Path) -> None:
    audio_bytes = b"ignored test audio"
    artifacts_dir = tmp_path / "artifacts"
    audio_path = (
        artifacts_dir
        / "audio"
        / "qwen3-tts-12hz-1.7b"
        / "chinatown-street"
        / "shokudo-oyaji-002-dry.opus"
    )
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(audio_bytes)
    manifest_path = tmp_path / "data" / "manifest.json"
    _write_manifest(manifest_path, audio_bytes)
    output_path = artifacts_dir / "qc" / "report.json"

    class MutatingRuntime(FakeRuntime):
        def inspect(
            self,
            inspected_audio_path: Path,
            *,
            mora_count: int,
        ) -> RuntimeInspection:
            inspection = super().inspect(
                inspected_audio_path,
                mora_count=mora_count,
            )
            inspected_audio_path.write_bytes(b"changed during QC")
            return inspection

    with pytest.raises(QCError, match="SHA-256"):
        run_qc(
            manifest_path=manifest_path,
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=artifacts_dir,
            output_path=output_path,
            runtime=MutatingRuntime("unused"),
        )

    assert not output_path.exists()


def test_filtered_qc_cannot_overwrite_unselected_audio(tmp_path: Path) -> None:
    selected_bytes = b"selected audio"
    unselected_bytes = b"unselected audio"
    artifacts_dir = tmp_path / "artifacts"
    selected_path = (
        artifacts_dir
        / "audio"
        / "qwen3-tts-12hz-1.7b"
        / "chinatown-street"
        / "shokudo-oyaji-002-dry.opus"
    )
    unselected_path = (
        artifacts_dir
        / "audio"
        / "qwen3-tts-12hz-1.7b"
        / "castle-gate"
        / "guard-otoko-001-dry.opus"
    )
    selected_path.parent.mkdir(parents=True)
    unselected_path.parent.mkdir(parents=True)
    selected_path.write_bytes(selected_bytes)
    unselected_path.write_bytes(unselected_bytes)
    manifest_path = tmp_path / "data" / "manifest.json"
    _write_manifest(manifest_path, selected_bytes)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    second_clip = dict(manifest["clips"][0])
    second_clip.update(
        {
            "scenario": "castle-gate",
            "line": "guard-otoko-001",
            "path": (
                "audio/qwen3-tts-12hz-1.7b/castle-gate/"
                "guard-otoko-001-dry.opus"
            ),
            "sha256": hashlib.sha256(unselected_bytes).hexdigest(),
        },
    )
    manifest["clips"].append(second_clip)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime = FakeRuntime("unused")

    with pytest.raises(QCError, match="同一にできません"):
        run_qc(
            manifest_path=manifest_path,
            scenarios_dir=SCENARIOS_DIR,
            artifacts_dir=artifacts_dir,
            output_path=unselected_path,
            runtime=runtime,
            scenario_id="chinatown-street",
            line_id="shokudo-oyaji-002",
        )

    assert not runtime.prepared
    assert unselected_path.read_bytes() == unselected_bytes


def test_qc_cli_routes_selectors_and_prints_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    received: dict[str, Any] = {}

    class FakeNativeRuntime:
        def __init__(self, model_dir: Path) -> None:
            received["model_dir"] = model_dir

    def fake_run_qc(**arguments: Any) -> QCSummary:
        received.update(arguments)
        return QCSummary(
            output_path=arguments["output_path"],
            clip_count=1,
            pass_count=0,
            mismatch_count=1,
            needs_reading_count=0,
            review_required_count=0,
            analysis_error_count=0,
        )

    monkeypatch.setattr(cli, "KanaWhisperQCRuntime", FakeNativeRuntime)
    monkeypatch.setattr(cli, "run_qc", fake_run_qc)
    output_path = tmp_path / "report.json"

    exit_code = cli.main(
        [
            "qc",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--scenarios",
            str(tmp_path / "scenarios"),
            "--artifacts",
            str(tmp_path / "artifacts"),
            "--output",
            str(output_path),
            "--model",
            "qwen3-tts-12hz-1.7b",
            "--scenario",
            "chinatown-street",
            "--line",
            "shokudo-oyaji-002",
        ],
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert received["model_id"] == "qwen3-tts-12hz-1.7b"
    assert received["scenario_id"] == "chinatown-street"
    assert received["line_id"] == "shokudo-oyaji-002"
    assert "読み不一致 1" in output
    assert output_path.as_posix() in output
