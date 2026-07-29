from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline import cli, intonation_report
from gaya_pipeline.intonation_report import (
    IntonationReportError,
    IntonationReportSummary,
    build_intonation_report,
)
from gaya_pipeline.take_identity import canonical_json, make_take_id
from gaya_pipeline.take_ledger import read_ledger, write_ledger_atomic


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPOSITORY_ROOT / "scenarios"
RUN_ID = "test-intonation-r1"
MODEL = "dummy"
SCENARIO = "chinatown-street"
MALE_LINE = "shokudo-oyaji-002"
FEMALE_LINE = "tenshin-okami-001"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _mechanical() -> dict[str, Any]:
    return {
        "status": "pass",
        "duration_sec": 2.0,
        "wav": {
            "codec": "pcm_s16le",
            "sample_rate_hz": 48_000,
            "channels": 1,
        },
        "opus": {
            "codec": "opus",
            "sample_rate_hz": 48_000,
            "channels": 1,
        },
        "loudness": {
            "source": "encoded_opus",
            "i_lufs": -18.0,
            "tp_dbtp": -1.0,
            "shortfall": False,
        },
        "generation_params": {
            "requested": {"temperature": 1.0},
            "realized": {"temperature": 1.0},
        },
        "sidecar_provenance": {
            "generation_seconds": 1.0,
            "postprocess": {},
            "toolchain": {},
            "loudness": {},
        },
    }


def _content(*, authoritative: bool) -> dict[str, Any]:
    return {
        "status": "pass" if authoritative else "review_required",
        "review_reason": (
            None if authoritative else "non_authoritative_expected_reading"
        ),
        "expected_reading": {
            "text": "ア",
            "source": "explicit" if authoritative else "generated",
            "normalized": "ア",
            "authoritative": authoritative,
            "ambiguous_terms": [],
        },
        "asr": {
            "text": "ア",
            "normalized_reading": "ア",
            "average_log_probability": None,
        },
        "reading": {
            "character_error_rate": 0.0,
            "reading_mismatch": False if authoritative else None,
        },
        "prosody": {},
    }


def _write_run(tmp_path: Path) -> tuple[Path, Path]:
    artifacts_dir = tmp_path / "artifacts"
    run_root = artifacts_dir / "takes" / RUN_ID
    scenario_path = SCENARIOS_DIR / f"{SCENARIO}.yaml"
    source_sha = _sha(scenario_path.read_bytes())
    groups = [
        {
            "model": MODEL,
            "scenario": SCENARIO,
            "line": line,
            "variant": "dry",
        }
        for line in (MALE_LINE, FEMALE_LINE)
    ]
    attempts: list[dict[str, Any]] = []
    report_attempts: list[dict[str, Any]] = []
    tamper_target: Path | None = None
    for group in groups:
        for take_index in (1, 2):
            base = (
                f"audio/{MODEL}/{SCENARIO}/{group['line']}/dry/"
                f"take-{take_index:04d}"
            )
            opus_path = run_root / f"{base}.opus"
            opus_path.parent.mkdir(parents=True, exist_ok=True)
            opus_bytes = f"{group['line']}-{take_index}".encode()
            opus_path.write_bytes(opus_bytes)
            if group["line"] == MALE_LINE and take_index == 1:
                tamper_target = opus_path
            opus_sha = _sha(opus_bytes)
            generation_input_sha = _sha(
                f"input-{group['line']}-{take_index}".encode(),
            )
            rejected = group["line"] == FEMALE_LINE and take_index == 2
            status = "hard_rejected" if rejected else "eligible"
            gates = (
                {"mechanical": "reject", "content": "not_run"}
                if rejected
                else {
                    "mechanical": "pass",
                    "content": (
                        "pass"
                        if group["line"] == MALE_LINE
                        else "review_required"
                    ),
                }
            )
            take_id = make_take_id(
                generation_input_sha256=generation_input_sha,
                final_opus_sha256=opus_sha,
            )
            attempts.append(
                {
                    **group,
                    "take_index": take_index,
                    "take_id": take_id,
                    "generation_input_sha256": generation_input_sha,
                    "generation": {
                        "status": "succeeded",
                        "seed": take_index,
                        "sampling": {"temperature": 1.0},
                        "rtf": 0.5,
                    },
                    "audio": {
                        "wav_path": f"{base}.wav",
                        "wav_sha256": "1" * 64,
                        "opus_path": f"{base}.opus",
                        "opus_sha256": opus_sha,
                        "sidecar_sha256": "2" * 64,
                    },
                    "gates": gates,
                    "features": {"status": "unscored"},
                    "status": status,
                },
            )
            report_attempts.append(
                {
                    **group,
                    "take_index": take_index,
                    "take_id": take_id,
                    "status": status,
                    "gates": gates,
                    "mechanical": (
                        {"status": "reject", "reason": "fixture rejection"}
                        if rejected
                        else _mechanical()
                    ),
                    "content": (
                        {"status": "not_run"}
                        if rejected
                        else _content(
                            authoritative=group["line"] == MALE_LINE,
                        )
                    ),
                },
            )

    scenario_sha = _sha(
        canonical_json(
            [{"path": scenario_path.name, "sha256": source_sha}],
        ).encode(),
    )
    ledger = {
        "format_version": 1,
        "run_id": RUN_ID,
        "created_at": "2026-07-30T00:00:00Z",
        "source": {
            "scenario_sha256": scenario_sha,
            "model": MODEL,
            "takes": 2,
            "seed_base": 0,
            "recipe_version": "test-v1",
            "groups": groups,
        },
        "attempts": attempts,
    }
    ledger_path = run_root / "ledger.json"
    write_ledger_atomic(ledger_path, ledger)
    counts = {
        status: sum(attempt["status"] == status for attempt in attempts)
        for status in (
            "eligible",
            "hard_rejected",
            "blocked",
            "generation_failed",
            "planned",
            "generated",
        )
    }
    qc_report = {
        "format_version": 2,
        "generated_at": "2026-07-30T00:00:01Z",
        "gate_policy_version": "take-gates-v2",
        "run_id": RUN_ID,
        "source": {
            "ledger": ledger_path.as_posix(),
            "scenario_sha256": scenario_sha,
            "model": MODEL,
            "recipe_version": "test-v1",
        },
        "runtime": {"status": "fixture"},
        "summary": {
            "attempt_count": len(attempts),
            **counts,
            "pending": 0,
            "content_review_required": 1,
        },
        "attempts": report_attempts,
    }
    (run_root / "qc-report.json").write_text(
        json.dumps(qc_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert tamper_target is not None
    return artifacts_dir, tamper_target


def _prosody(
    *,
    median: float,
    semitone_std: float,
    voiced_ratio: float,
    interval: float | None,
    rise_anchor_met: bool | None,
) -> dict[str, Any]:
    return {
        "f0": {
            "median_hz": median,
            "p10_hz": median * 0.8,
            "p90_hz": median * 1.2,
            "semitone_std": semitone_std,
            "voiced_ratio": voiced_ratio,
            "final_intonation": {
                "raw_interval_semitones": interval,
                "clipped_interval_semitones": interval,
                "rise_anchor_met": rise_anchor_met,
            },
        },
    }


class _FakeAnalysisRuntime:
    def __init__(self) -> None:
        self.analyze_calls = 0

    def identity(self) -> dict[str, Any]:
        return {
            "librosa": {"distribution": "librosa", "version": "0.11.0"},
            "numpy": {"distribution": "numpy", "version": "2.4.6"},
            "ffmpeg": {
                "executable": "C:/fixture/ffmpeg.exe",
                "version": "ffmpeg version fixture",
            },
        }

    def analyze(
        self,
        path: Path,
        *,
        mora_count: int,
        final_intonation: str,
    ) -> dict[str, Any]:
        self.analyze_calls += 1
        assert mora_count == 1
        assert final_intonation == "fall"
        if MALE_LINE in path.as_posix() and path.stem == "take-0001":
            return _prosody(
                median=100.0,
                semitone_std=2.0,
                voiced_ratio=0.8,
                interval=3.0,
                rise_anchor_met=True,
            )
        if MALE_LINE in path.as_posix():
            return _prosody(
                median=200.0,
                semitone_std=2.0,
                voiced_ratio=0.6,
                interval=-1.0,
                rise_anchor_met=False,
            )
        return _prosody(
            median=300.0,
            semitone_std=4.0,
            voiced_ratio=0.7,
            interval=None,
            rise_anchor_met=None,
        )


def _install_fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> _FakeAnalysisRuntime:
    runtime = _FakeAnalysisRuntime()
    monkeypatch.setattr(
        intonation_report,
        "_prepare_analysis_runtime",
        lambda: runtime,
    )
    return runtime


def test_reportはactual_nとwithin_speaker_zとmodel_genderを決定的に出力(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts_dir, _ = _write_run(tmp_path)
    runtime = _install_fake_runtime(monkeypatch)
    first = tmp_path / "report-one"
    second = tmp_path / "report-two"

    summary = build_intonation_report(
        run_ids=[RUN_ID],
        artifacts_dir=artifacts_dir,
        scenarios_dir=SCENARIOS_DIR,
        output_dir=first,
    )
    build_intonation_report(
        run_ids=[RUN_ID],
        artifacts_dir=artifacts_dir,
        scenarios_dir=SCENARIOS_DIR,
        output_dir=second,
    )

    assert summary.eligible_attempt_count == 3
    assert runtime.analyze_calls == 6
    assert (first / "intonation-report.json").read_bytes() == (
        second / "intonation-report.json"
    ).read_bytes()
    assert (first / "intonation-report.md").read_bytes() == (
        second / "intonation-report.md"
    ).read_bytes()
    report = json.loads((first / "intonation-report.json").read_text("utf-8"))
    assert report["summary"] == {
        "run_count": 1,
        "eligible_attempt_count": 3,
        "model_gender_group_count": 2,
    }
    assert report["algorithm"]["runtime"] == runtime.identity()
    assert [
        (item["model"], item["gender"], item["n"])
        for item in report["distributions"]
    ] == [(MODEL, "female", 1), (MODEL, "male", 2)]

    male_attempts = [
        attempt
        for attempt in report["attempts"]
        if attempt["gender"] == "male"
    ]
    assert [
        attempt["metrics"]["median_hz"]["z"] for attempt in male_attempts
    ] == [-1.0, 1.0]
    assert all(
        attempt["metrics"]["semitone_std"]["z"] is None
        for attempt in male_attempts
    )
    female = next(
        attempt
        for attempt in report["attempts"]
        if attempt["gender"] == "female"
    )
    assert female["metrics"]["median_hz"]["z"] is None
    assert female["metrics"]["final_raw_interval_semitones"] == {
        "raw": None,
        "z": None,
    }

    male_distribution = next(
        item for item in report["distributions"] if item["gender"] == "male"
    )
    assert male_distribution["rise_anchor"] == {
        "measured_n": 2,
        "met_count": 1,
        "met_rate": 0.5,
    }
    assert male_distribution["unexpected_rise"] == {
        "measured_fall_n": 2,
        "count": 1,
        "rate": 0.5,
    }
    assert report["inputs"][0]["eligible_attempt_count"] == 3
    assert len(report["inputs"][0]["ledger"]["sha256"]) == 64
    assert len(report["inputs"][0]["qc_report"]["sha256"]) == 64


def test_reportはeligible_opusのhash改変を拒否する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts_dir, opus_path = _write_run(tmp_path)
    runtime = _install_fake_runtime(monkeypatch)
    opus_path.write_bytes(b"tampered")
    output = tmp_path / "report"

    with pytest.raises(IntonationReportError, match="Opus SHA-256"):
        build_intonation_report(
            run_ids=[RUN_ID],
            artifacts_dir=artifacts_dir,
            scenarios_dir=SCENARIOS_DIR,
            output_dir=output,
        )

    assert runtime.analyze_calls == 0
    assert not output.exists()


@pytest.mark.parametrize("status", ["blocked", "planned", "generated"])
def test_reportは非terminal_attemptを分析前に拒否する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    artifacts_dir, _ = _write_run(tmp_path)
    ledger_path = artifacts_dir / "takes" / RUN_ID / "ledger.json"
    ledger = read_ledger(ledger_path)
    attempt = ledger["attempts"][0]
    attempt["status"] = status
    if status == "blocked":
        attempt["gates"] = {
            "mechanical": "blocked",
            "content": "not_run",
        }
    elif status == "generated":
        attempt["gates"] = {}
    else:
        attempt["generation"] = {
            "status": "planned",
            "seed": attempt["generation"]["seed"],
            "sampling": attempt["generation"]["sampling"],
        }
        for key in ("take_id", "audio", "gates", "features"):
            del attempt[key]
    write_ledger_atomic(ledger_path, ledger)

    prepare_calls = 0

    def must_not_prepare() -> _FakeAnalysisRuntime:
        nonlocal prepare_calls
        prepare_calls += 1
        return _FakeAnalysisRuntime()

    monkeypatch.setattr(
        intonation_report,
        "_prepare_analysis_runtime",
        must_not_prepare,
    )
    output = tmp_path / f"report-{status}"
    with pytest.raises(
        IntonationReportError,
        match=f"nonterminal=\\['{status}'\\]",
    ):
        build_intonation_report(
            run_ids=[RUN_ID],
            artifacts_dir=artifacts_dir,
            scenarios_dir=SCENARIOS_DIR,
            output_dir=output,
        )

    assert prepare_calls == 0
    assert not output.exists()


def test_runtimeはlibrosa_numpy_ffmpegの実identityを固定して返す(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    versions = {
        "librosa": "0.11.0",
        "numpy": "2.4.6",
    }
    fake_numpy = object()
    fake_librosa = object()
    fake_ffmpeg = tmp_path / "tools" / "ffmpeg"
    modules = {
        "numpy": fake_numpy,
        "librosa": fake_librosa,
    }
    monkeypatch.setattr(
        intonation_report.importlib.metadata,
        "version",
        lambda name: versions[name],
    )
    monkeypatch.setattr(
        intonation_report.importlib,
        "import_module",
        lambda name: modules[name],
    )
    monkeypatch.setattr(
        intonation_report.shutil,
        "which",
        lambda name: str(fake_ffmpeg) if name == "ffmpeg" else None,
    )
    monkeypatch.setattr(
        intonation_report.subprocess,
        "run",
        lambda *_args, **_kwargs: type(
            "Result",
            (),
            {"stdout": "ffmpeg version 8.1.1 fixture\nconfiguration: fixture\n"},
        )(),
    )

    runtime = intonation_report._prepare_analysis_runtime()

    assert runtime.librosa is fake_librosa
    assert runtime.numpy is fake_numpy
    assert runtime.identity() == {
        "librosa": {"distribution": "librosa", "version": "0.11.0"},
        "numpy": {"distribution": "numpy", "version": "2.4.6"},
        "ffmpeg": {
            "executable": fake_ffmpeg.resolve().as_posix(),
            "version": "ffmpeg version 8.1.1 fixture",
        },
    }


def test_runtimeはnumpy_version不一致を拒否する(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        intonation_report.importlib.metadata,
        "version",
        lambda name: "0.11.0" if name == "librosa" else "2.4.5",
    )

    with pytest.raises(IntonationReportError, match="numpy version"):
        intonation_report._prepare_analysis_runtime()


def test_cliは明示した複数runとpathをreportへ渡す(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}

    def fake_build(**kwargs: Any) -> IntonationReportSummary:
        captured.update(kwargs)
        output = kwargs["output_dir"]
        return IntonationReportSummary(
            output_dir=output,
            json_path=output / "intonation-report.json",
            markdown_path=output / "intonation-report.md",
            run_count=2,
            eligible_attempt_count=7,
        )

    monkeypatch.setattr(cli, "build_intonation_report", fake_build)
    artifacts = tmp_path / "artifacts"
    scenarios = tmp_path / "scenarios"
    output = tmp_path / "report"
    exit_code = cli.main(
        [
            "intonation",
            "report",
            "--run-id",
            "r2",
            "--run-id",
            "r1",
            "--artifacts",
            str(artifacts),
            "--scenarios",
            str(scenarios),
            "--output",
            str(output),
        ],
    )

    assert exit_code == 0
    assert captured == {
        "run_ids": ["r2", "r1"],
        "artifacts_dir": artifacts,
        "scenarios_dir": scenarios,
        "output_dir": output,
    }
    assert "eligible attempt 7" in capsys.readouterr().out
