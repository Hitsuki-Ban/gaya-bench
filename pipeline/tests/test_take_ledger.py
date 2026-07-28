from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from gaya_pipeline.take_ledger import (
    TakeLedgerError,
    transition_attempt,
    validate_ledger,
    write_ledger_atomic,
)


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
    blocked_attempt["gates"] = {"mechanical": "blocked", "content": "blocked"}
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


def test_atomic_replace失敗時に既存bytesを保持(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ledger.json"
    path.write_text('{"stable":true}\n', encoding="utf-8")
    before = path.read_bytes()
    original_replace = Path.replace

    def fail_replace(source: Path, target: Path) -> Path:
        if source.name == ".ledger.json.pending":
            raise OSError("replace failed")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_ledger_atomic(path, _ledger())

    assert path.read_bytes() == before
    assert not (tmp_path / ".ledger.json.pending").exists()


def test_json_fixtureとしてround_trip可能() -> None:
    document = json.loads(json.dumps(_ledger(), ensure_ascii=False))
    assert validate_ledger(document)["format_version"] == 1
