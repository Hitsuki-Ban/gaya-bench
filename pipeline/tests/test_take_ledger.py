from __future__ import annotations

import errno
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

import gaya_pipeline.take_ledger as take_ledger
from gaya_pipeline.take_ledger import (
    TakeLedgerError,
    transition_attempt,
    validate_ledger,
    write_ledger_atomic,
)
from gaya_pipeline.take_identity import canonical_json


GROUP = {
    "model": "dummy",
    "scenario": "tavern-night",
    "line": "barmaid-001",
    "variant": "dry",
}
INPUT_SHA = "1" * 64
TAKE_ID = "6a0405bd29910757dc58319a4cf0e956cee5402cd80e63f76216c0859c11199b"


def _planned() -> dict[str, object]:
    return {
        **GROUP,
        "take_index": 1,
        "generation_input_sha256": INPUT_SHA,
        "generation": {"status": "planned", "seed": None, "sampling": {}},
        "status": "planned",
    }


def _generated(status: str = "generated") -> dict[str, object]:
    gates = {} if status == "generated" else {
        "mechanical": "pass",
        "content": "review_required",
    }
    return {
        **GROUP,
        "take_index": 1,
        "take_id": TAKE_ID,
        "generation_input_sha256": INPUT_SHA,
        "generation": {
            "status": "succeeded",
            "seed": None,
            "sampling": {},
            "rtf": 0.5,
        },
        "audio": {
            "wav_path": "audio/dummy/tavern-night/barmaid-001/dry/take-0001.wav",
            "wav_sha256": "3" * 64,
            "opus_path": "audio/dummy/tavern-night/barmaid-001/dry/take-0001.opus",
            "opus_sha256": "4" * 64,
            "sidecar_sha256": "5" * 64,
        },
        "gates": gates,
        "features": {"status": "unscored"},
        "status": status,
    }


def _ledger(attempt: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "format_version": 1,
        "run_id": "2026-07-29T120000Z-dummy-n1",
        "created_at": "2026-07-29T12:00:00Z",
        "source": {
            "scenario_sha256": "a" * 64,
            "model": "dummy",
            "takes": 1,
            "seed_base": 42,
            "recipe_version": "fixed-single-v1",
            "groups": [GROUP],
        },
        "attempts": [attempt or _planned()],
    }


def _phase_b_ledger() -> dict[str, object]:
    ledger = _ledger()
    target_group = {**GROUP, "role_epoch_sha256": "b" * 64}
    phase_b = {
        "protocol": "phase-b-generation-v1",
        "plan_sha256": "c" * 64,
        "run_kind": "primary",
        "supersedes_run_id": None,
        "anchor_selection_sha256": None,
        "target_groups": [target_group],
    }
    provenance = {
        "protocol": phase_b["protocol"],
        "plan_sha256": phase_b["plan_sha256"],
        "run_kind": phase_b["run_kind"],
        "supersedes_run_id": phase_b["supersedes_run_id"],
        "anchor_selection_sha256": phase_b["anchor_selection_sha256"],
        "target_group": target_group,
    }
    ledger["source"]["phase_b"] = phase_b  # type: ignore[index]
    ledger["attempts"][0]["phase_b_provenance_sha256"] = hashlib.sha256(  # type: ignore[index]
        canonical_json(provenance).encode("utf-8"),
    ).hexdigest()
    return ledger


def _winerror(code: int, message: str) -> OSError:
    error = OSError(message)
    error.winerror = code
    return error


def test_ledger_v1のexact_contractと合法遷移() -> None:
    planned = _ledger()
    assert validate_ledger(planned) is planned

    generated = transition_attempt(
        planned,
        slot=("dummy", "tavern-night", "barmaid-001", "dry", 1),
        replacement=_generated(),
    )
    eligible = transition_attempt(
        generated,
        slot=("dummy", "tavern-night", "barmaid-001", "dry", 1),
        replacement=_generated("eligible"),
    )

    assert eligible["attempts"][0]["status"] == "eligible"  # type: ignore[index]


def test_phase_b_provenanceはsourceとattemptをexactに拘束する() -> None:
    ledger = _phase_b_ledger()
    assert validate_ledger(ledger) is ledger

    ledger["source"]["phase_b"]["target_groups"][0][  # type: ignore[index]
        "role_epoch_sha256"
    ] = "d" * 64
    with pytest.raises(TakeLedgerError, match="Phase B provenance"):
        validate_ledger(ledger)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda ledger: ledger["source"]["phase_b"].pop("plan_sha256"),
        lambda ledger: ledger["source"]["phase_b"].update(
            anchor_selection_sha256="e" * 64,
        ),
        lambda ledger: ledger["source"]["phase_b"].update(
            run_kind="topup",
        ),
        lambda ledger: ledger["attempts"][0].pop(
            "phase_b_provenance_sha256",
        ),
    ],
)
def test_phase_bの欠落_anchor誤用_topup契約違反を拒否(
    mutation: object,
) -> None:
    ledger = _phase_b_ledger()
    mutation(ledger)  # type: ignore[operator]
    with pytest.raises(TakeLedgerError):
        validate_ledger(ledger)


def test_generation_failedはterminalで書き換えられない() -> None:
    failed_attempt = {
        **_planned(),
        "generation": {
            "status": "failed",
            "seed": None,
            "sampling": {},
            "error": "backend error",
        },
        "status": "generation_failed",
    }
    failed = transition_attempt(
        _ledger(),
        slot=("dummy", "tavern-night", "barmaid-001", "dry", 1),
        replacement=failed_attempt,
    )
    with pytest.raises(TakeLedgerError, match="terminal"):
        transition_attempt(
            failed,
            slot=("dummy", "tavern-night", "barmaid-001", "dry", 1),
            replacement=_generated(),
        )


def test_blocked再評価はprovenanceを固定する() -> None:
    generated = transition_attempt(
        _ledger(),
        slot=("dummy", "tavern-night", "barmaid-001", "dry", 1),
        replacement=_generated(),
    )
    blocked_attempt = _generated("blocked")
    blocked_attempt["gates"] = {"mechanical": "blocked", "content": "not_run"}
    blocked = transition_attempt(
        generated,
        slot=("dummy", "tavern-night", "barmaid-001", "dry", 1),
        replacement=blocked_attempt,
    )
    replacement = _generated("eligible")
    replacement["audio"] = {**replacement["audio"], "opus_sha256": "5" * 64}  # type: ignore[arg-type]

    with pytest.raises(TakeLedgerError, match="provenance"):
        transition_attempt(
            blocked,
            slot=("dummy", "tavern-night", "barmaid-001", "dry", 1),
            replacement=replacement,
        )


def test_gate遷移は生成seedとsamplingを変更できない() -> None:
    generated = transition_attempt(
        _ledger(),
        slot=("dummy", "tavern-night", "barmaid-001", "dry", 1),
        replacement=_generated(),
    )
    replacement = _generated("eligible")
    replacement["generation"] = {
        **replacement["generation"],  # type: ignore[dict-item]
        "seed": 999,
        "sampling": {"temperature": 9.0},
    }

    with pytest.raises(TakeLedgerError, match="generation provenance"):
        transition_attempt(
            generated,
            slot=("dummy", "tavern-night", "barmaid-001", "dry", 1),
            replacement=replacement,
        )


@pytest.mark.parametrize(
    "gates",
    [
        {"mechanical": "reject", "content": "TYPO"},
        {"mechanical": "UNKNOWN", "content": "reject"},
        {"mechanical": "reject", "content": "blocked"},
    ],
)
def test_gateの未知値と未解決blocked_terminalを拒否(
    gates: dict[str, str],
) -> None:
    attempt = _generated("hard_rejected")
    attempt["gates"] = gates
    with pytest.raises(TakeLedgerError):
        validate_ledger(_ledger(attempt))


@pytest.mark.parametrize(
    ("status", "gates"),
    [
        ("eligible", {"mechanical": "pass", "content": "pass"}),
        ("eligible", {"mechanical": "pass", "content": "review_required"}),
        ("hard_rejected", {"mechanical": "reject", "content": "not_run"}),
        ("hard_rejected", {"mechanical": "pass", "content": "reject"}),
        ("blocked", {"mechanical": "blocked", "content": "not_run"}),
        ("blocked", {"mechanical": "pass", "content": "blocked"}),
    ],
)
def test_gateとattempt_statusの合法な組み合わせだけを受理(
    status: str,
    gates: dict[str, str],
) -> None:
    attempt = _generated(status)
    attempt["gates"] = gates
    assert validate_ledger(_ledger(attempt))["attempts"][0]["status"] == status


@pytest.mark.parametrize(
    ("status", "gates"),
    [
        ("eligible", {"mechanical": "pass", "content": "not_run"}),
        ("hard_rejected", {"mechanical": "reject", "content": "pass"}),
        ("blocked", {"mechanical": "blocked", "content": "blocked"}),
    ],
)
def test_gateの未実行状態を別判定へ偽装できない(
    status: str,
    gates: dict[str, str],
) -> None:
    attempt = _generated(status)
    attempt["gates"] = gates
    with pytest.raises(TakeLedgerError, match="一致しません"):
        validate_ledger(_ledger(attempt))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda ledger: ledger.update(extra=True),
        lambda ledger: ledger["attempts"].append(deepcopy(ledger["attempts"][0])),
        lambda ledger: ledger["attempts"][0]["audio"].update(
            opus_path="../outside.opus",
        ),
    ],
)
def test_unknown_key_duplicate_slot_path_escapeを拒否(mutation: object) -> None:
    ledger = _ledger(_generated())
    mutation(ledger)  # type: ignore[operator]
    with pytest.raises(TakeLedgerError):
        validate_ledger(ledger)


def test_run_idのpath_escapeを拒否() -> None:
    ledger = _ledger()
    ledger["run_id"] = "../outside"
    with pytest.raises(TakeLedgerError, match="path segment"):
        validate_ledger(ledger)


@pytest.mark.parametrize(
    "winerrors",
    [
        [5],
        [32, 5, 32],
    ],
)
def test_atomic_replaceは一時的なWindows競合後に成功(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    winerrors: list[int],
) -> None:
    path = tmp_path / "ledger.json"
    path.write_text('{"stable":true}\n', encoding="utf-8")
    original_replace = Path.replace
    errors = [_winerror(code, f"replace failed: {code}") for code in winerrors]
    sleeps: list[float] = []
    calls = 0

    def flaky_replace(source: Path, target: Path) -> Path:
        nonlocal calls
        if source.name == ".ledger.json.pending" and calls < len(errors):
            error = errors[calls]
            calls += 1
            raise error
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(take_ledger.time, "sleep", sleeps.append)

    write_ledger_atomic(path, _ledger())

    assert json.loads(path.read_text(encoding="utf-8"))["format_version"] == 1
    assert calls == len(errors)
    assert sleeps == [0.01, 0.02, 0.04][: len(errors)]
    assert not (tmp_path / ".ledger.json.pending").exists()


def test_atomic_replaceは5回のWindows競合後に最後の原例外を送出(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ledger.json"
    path.write_text('{"stable":true}\n', encoding="utf-8")
    before = path.read_bytes()
    errors = [_winerror(5, f"replace failed: {index}") for index in range(5)]
    sleeps: list[float] = []
    calls = 0

    def fail_replace(source: Path, target: Path) -> Path:
        nonlocal calls
        error = errors[calls]
        calls += 1
        raise error

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(take_ledger.time, "sleep", sleeps.append)

    with pytest.raises(OSError) as caught:
        write_ledger_atomic(path, _ledger())

    assert caught.value is errors[-1]
    assert calls == 5
    assert sleeps == [0.01, 0.02, 0.04, 0.08]
    assert path.read_bytes() == before
    assert not (tmp_path / ".ledger.json.pending").exists()


@pytest.mark.parametrize(
    "error",
    [
        _winerror(33, "lock violation"),
        OSError(errno.ENOSPC, "disk full"),
        OSError(errno.EACCES, "permission denied"),
        OSError("I/O failed"),
    ],
)
def test_atomic_replaceは対象外OSErrorを即時送出(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
) -> None:
    path = tmp_path / "ledger.json"
    path.write_text('{"stable":true}\n', encoding="utf-8")
    before = path.read_bytes()
    calls = 0

    def fail_replace(source: Path, target: Path) -> Path:
        nonlocal calls
        calls += 1
        raise error

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(
        take_ledger.time,
        "sleep",
        lambda delay: pytest.fail(f"sleep must not be called: {delay}"),
    )

    with pytest.raises(OSError) as caught:
        write_ledger_atomic(path, _ledger())

    assert caught.value is error
    assert calls == 1
    assert path.read_bytes() == before
    assert not (tmp_path / ".ledger.json.pending").exists()


def test_invalid_ledgerはI_O前に失敗(tmp_path: Path) -> None:
    path = tmp_path / "not-created" / "ledger.json"
    ledger = _ledger()
    ledger["format_version"] = 2

    with pytest.raises(TakeLedgerError, match="format_version"):
        write_ledger_atomic(path, ledger)

    assert not path.parent.exists()


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows の open reader と replace の競合を実ファイルで検証するため",
)
def test_atomic_replaceは開いているWindows_readerの解放後に成功(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ledger.json"
    path.write_text('{"stable":true}\n', encoding="utf-8")
    reader = path.open("rb")
    sleeps: list[float] = []

    def release_reader(delay: float) -> None:
        sleeps.append(delay)
        reader.close()

    monkeypatch.setattr(take_ledger.time, "sleep", release_reader)
    try:
        write_ledger_atomic(path, _ledger())
    finally:
        reader.close()

    assert sleeps == [0.01]
    assert json.loads(path.read_text(encoding="utf-8"))["format_version"] == 1
    assert not (tmp_path / ".ledger.json.pending").exists()


def test_json_fixtureとしてround_trip可能() -> None:
    document = json.loads(json.dumps(_ledger(), ensure_ascii=False))
    assert validate_ledger(document)["format_version"] == 1
