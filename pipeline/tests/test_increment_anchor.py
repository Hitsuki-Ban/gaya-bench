from __future__ import annotations

import hashlib
import json
import math
import struct
import wave
from pathlib import Path
from typing import Any, Mapping

import pytest

from gaya_pipeline import increment_anchor
from gaya_pipeline.increment_anchor import (
    ANCHOR_ROLE_COUNT,
    CANDIDATES_PER_ROLE,
    MAX_TOPUP_ROUNDS,
    ROLE_SCOPE_EXPLICIT_REFERENCE,
    ROLE_SCOPE_NO_REFERENCE,
    SELECTION_AUTHORITY_TYPE,
    SELECTION_POLICY,
    SOFT_SIGNAL_EXHAUSTED,
    VARIANT_SEED_BASE,
    IncrementAnchorError,
    anchor_round_targets,
    build_anchor_bootstrap_plan_document,
    derive_anchor_seed,
    gender_screening,
    load_anchor_bootstrap_plan,
    rank_anchor_candidates,
    round_attempts,
    run_anchor_bootstrap_generation,
    screening_distance_hz,
    select_role_anchors,
    validate_anchor_bootstrap_plan,
    validate_machine_anchor_selection,
    write_anchor_bootstrap_plan,
)
from gaya_pipeline.take_identity import canonical_json

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = REPOSITORY_ROOT / "scenarios"
VOICES = REPOSITORY_ROOT / "assets" / "voices"
MODEL = "irodori-tts-v4-small"
REVISION = "test-revision-v4"
ANCHOR_TEXT = "そらにはくもがうかび、とおくでかぜのおとがきこえます。"


def _plan_document() -> dict[str, Any]:
    return validate_anchor_bootstrap_plan(
        build_anchor_bootstrap_plan_document(
            model=MODEL,
            model_revision=REVISION,
            scenarios_dir=SCENARIOS,
            voices_dir=VOICES,
            anchor_text=ANCHOR_TEXT,
        ),
    )


def _write_plan(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    path = tmp_path / "role-anchor-bootstrap-plan-v1.json"
    summary = write_anchor_bootstrap_plan(
        model=MODEL,
        model_revision=REVISION,
        scenarios_dir=SCENARIOS,
        voices_dir=VOICES,
        anchor_text=ANCHOR_TEXT,
        output_path=path,
    )
    document = load_anchor_bootstrap_plan(path)
    assert document["plan_sha256"] == summary.plan_sha256
    return path, document


def _write_wav(path: Path, *, amplitude: int = 12_000, seconds: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 16_000
    frames = int(rate * seconds)
    samples = [
        int(amplitude * math.sin(2 * math.pi * 220.0 * index / rate))
        for index in range(frames)
    ]
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(struct.pack(f"<{len(samples)}h", *samples))


class _FakeGenerator:
    """`role_anchor_generation_input` / `generate_role_anchor` 契約だけを満たす。"""

    def __init__(self, *, silent_roles: frozenset[str] = frozenset()) -> None:
        self.closed = False
        self._silent_roles = silent_roles
        self.role_scopes: list[str] = []

    def role_anchor_generation_input(
        self,
        role: Any,
        *,
        role_scope: str = ROLE_SCOPE_NO_REFERENCE,
    ) -> Mapping[str, Any]:
        self.role_scopes.append(role_scope)
        return {
            "model": MODEL,
            "model_revision": REVISION,
            "text": ANCHOR_TEXT,
            "caption": f"{role.role['gender']}/{role.role['age']}",
            "reference_wav": None,
        }

    def generate_role_anchor(
        self,
        role: Any,
        *,
        seed: int,
        output_wav: Path,
        role_scope: str = ROLE_SCOPE_NO_REFERENCE,
    ) -> Mapping[str, Any]:
        del role_scope
        amplitude = 4 if role.character in self._silent_roles else 12_000
        _write_wav(output_wav, amplitude=amplitude)
        return {"seed": seed, "sample_rate_hz": 16_000}

    def close_role_anchor_generation(self) -> None:
        self.closed = True


def test_bootstrap_planは53_anchor_roleと58_role_snapshotを固定する() -> None:
    document = _plan_document()
    assert len(document["targets"]) == ANCHOR_ROLE_COUNT == 53
    assert len(document["roles"]) == 58
    assert document["phase_a"] == {
        "takes": 4,
        "minimum_eligible_candidates": 3,
        "seed_policy": "role-anchor-increment-derived-sha256-v1",
        "seed_base": 194,
        "max_topup_rounds": 2,
    }
    assert {target["model"] for target in document["targets"]} == {MODEL}


def test_role_epochとseedは決定論的でround間で衝突しない() -> None:
    document = _plan_document()
    assert _plan_document() == document

    round0 = anchor_round_targets(plan_document=document, round_index=0)
    assert len(round0) == ANCHOR_ROLE_COUNT
    assert round0[0]["attempts"] == [1, 2, 3, 4]

    identity = (round0[0]["scenario"], round0[0]["character"])
    round1 = anchor_round_targets(
        plan_document=document,
        round_index=1,
        identities=[identity],
    )
    round2 = anchor_round_targets(
        plan_document=document,
        round_index=2,
        identities=[identity],
    )
    assert round1[0]["attempts"] == [5, 6, 7, 8]
    assert round2[0]["attempts"] == [9, 10, 11, 12]
    seeds = [*round0[0]["seeds"], *round1[0]["seeds"], *round2[0]["seeds"]]
    assert len(set(seeds)) == len(seeds) == 12


def test_top_upは2回で上限に達する() -> None:
    assert round_attempts(0) == (1, 2, 3, 4)
    assert round_attempts(MAX_TOPUP_ROUNDS) == (9, 10, 11, 12)
    with pytest.raises(IncrementAnchorError, match="top-upは最大2回"):
        round_attempts(MAX_TOPUP_ROUNDS + 1)


def test_seedはrole_epoch_model_attemptの全てに依存する() -> None:
    base = {
        "role_epoch_sha256": "a" * 64,
        "model": MODEL,
        "scenario": "tavern-night",
        "character": "barmaid",
        "attempt": 1,
    }
    reference = derive_anchor_seed(**base)
    assert 0 <= reference < 2**32
    assert derive_anchor_seed(**base) == reference
    assert derive_anchor_seed(**{**base, "attempt": 2}) != reference
    assert derive_anchor_seed(**{**base, "character": "cook"}) != reference
    assert derive_anchor_seed(**{**base, "role_epoch_sha256": "b" * 64}) != reference


@pytest.mark.parametrize(
    ("gender", "median", "status", "signal"),
    [
        ("female", 164.9, "review_required", "gender_f0_below_expected"),
        ("female", 165.0, "pass", None),
        ("male", 180.0, "pass", None),
        ("male", 180.1, "review_required", "gender_f0_above_expected"),
        ("neutral", None, "not_applicable", None),
        ("neutral", 500.0, "not_applicable", None),
        ("male", None, "review_required", "gender_f0_unavailable"),
        ("female", None, "review_required", "gender_f0_unavailable"),
    ],
)
def test_gender_screeningは公開済みF0_policyと同一である(
    gender: str,
    median: float | None,
    status: str,
    signal: str | None,
) -> None:
    result = gender_screening(expected_gender=gender, median_f0_hz=median)
    assert result["protocol"] == "role-gender-f0-soft-v1"
    assert (result["status"], result["signal"]) == (status, signal)


def test_screening距離はpass候補で常に0になる() -> None:
    assert screening_distance_hz(expected_gender="male", median_f0_hz=120.0) == 0.0
    assert screening_distance_hz(expected_gender="male", median_f0_hz=200.0) == 20.0
    assert screening_distance_hz(expected_gender="female", median_f0_hz=150.0) == 15.0
    assert screening_distance_hz(expected_gender="neutral", median_f0_hz=None) == 0.0
    assert screening_distance_hz(
        expected_gender="male",
        median_f0_hz=None,
    ) == float("inf")


def test_機械順位はscreening_逸脱量_attemptの順で決まる() -> None:
    def candidate(attempt: int, median: float) -> dict[str, Any]:
        return {
            "attempt": attempt,
            "screening": gender_screening(
                expected_gender="male",
                median_f0_hz=median,
            ),
            "screening_distance_hz": screening_distance_hz(
                expected_gender="male",
                median_f0_hz=median,
            ),
        }

    ranked = rank_anchor_candidates(
        [
            candidate(1, 400.0),
            candidate(2, 190.0),
            candidate(3, 120.0),
            candidate(4, 110.0),
        ],
    )
    assert [item["rank"] for item in ranked] == [1, 2, 3, 4]
    # pass候補が先、その中では最小attempt。逸脱候補は逸脱量の小さい順。
    assert [item["attempt"] for item in ranked] == [3, 4, 2, 1]


def test_anchor生成runはledgerとsidecarをwrite_onceで残す(tmp_path: Path) -> None:
    _path, document = _write_plan(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    generator = _FakeGenerator()
    summary = run_anchor_bootstrap_generation(
        plan_document=document,
        plan_sha256=document["plan_sha256"],
        round_index=0,
        identities=None,
        artifacts_dir=artifacts,
        run_id="test-anchor-round0",
        generator=generator,
    )
    assert generator.closed is True
    assert summary.generated_count == ANCHOR_ROLE_COUNT * CANDIDATES_PER_ROLE
    assert summary.eligible_count == summary.generated_count
    assert summary.rejected_count == 0 and summary.failed_count == 0

    ledger_path = summary.run_dir / "ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["protocol"] == "role-anchor-bootstrap-run-v1"
    assert ledger["plan_sha256"] == document["plan_sha256"]
    assert (
        hashlib.sha256(ledger_path.read_bytes()).hexdigest()
        == summary.ledger_sha256
        == (summary.run_dir / "ledger.sha256").read_text(encoding="ascii").strip()
    )
    attempt = ledger["attempts"][0]
    assert attempt["status"] == "eligible"
    assert attempt["qc"]["mechanical"] == "pass"
    assert attempt["generation_input"]["protocol"] == "role-anchor-increment-input-v1"
    wav = artifacts.joinpath(*Path(attempt["audio_path"]).parts)
    assert hashlib.sha256(wav.read_bytes()).hexdigest() == attempt["audio_sha256"]

    with pytest.raises(IncrementAnchorError, match="既存path"):
        run_anchor_bootstrap_generation(
            plan_document=document,
            plan_sha256=document["plan_sha256"],
            round_index=0,
            identities=None,
            artifacts_dir=artifacts,
            run_id="test-anchor-round0",
            generator=_FakeGenerator(),
        )


def test_mechanical_QC不合格はrejectedとして数えられる(tmp_path: Path) -> None:
    _path, document = _write_plan(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    silent = document["targets"][0]["character"]
    summary = run_anchor_bootstrap_generation(
        plan_document=document,
        plan_sha256=document["plan_sha256"],
        round_index=0,
        identities=None,
        artifacts_dir=artifacts,
        run_id="test-anchor-silent",
        generator=_FakeGenerator(silent_roles=frozenset({silent})),
    )
    assert summary.rejected_count >= CANDIDATES_PER_ROLE
    assert summary.eligible_count == summary.generated_count - summary.rejected_count


def _select(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    medians: dict[str, float],
    default_median: float,
    run_rounds: tuple[int, ...] = (0,),
) -> Any:
    _path, document = _write_plan(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    run_ids: list[str] = []
    for round_index in run_rounds:
        run_id = f"test-run-{round_index}"
        run_ids.append(run_id)
        run_anchor_bootstrap_generation(
            plan_document=document,
            plan_sha256=document["plan_sha256"],
            round_index=round_index,
            identities=(
                None
                if round_index == 0
                else [
                    (target["scenario"], target["character"])
                    for target in document["targets"]
                ]
            ),
            artifacts_dir=artifacts,
            run_id=run_id,
            generator=_FakeGenerator(),
        )

    def fake_f0(audio_path: Path, **_kwargs: Any) -> dict[str, Any]:
        character = audio_path.parent.name
        return {"median_hz": medians.get(character, default_median)}

    monkeypatch.setattr(increment_anchor, "measure_median_f0_hz", fake_f0)
    return document, select_role_anchors(
        plan_document=document,
        plan_sha256=document["plan_sha256"],
        run_ids=run_ids,
        artifacts_dir=artifacts,
        output_dir=tmp_path / "selection",
    )


def test_adapterのanchor発話文がplanと違えば生成を拒否する(tmp_path: Path) -> None:
    """plan の anchor_text と adapter の実発話文の乖離を fail fast する。

    ここがずれると合成後の58役 authority が別regimeのanchorを混ぜてしまう。
    """

    _path, document = _write_plan(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    class _DriftingGenerator(_FakeGenerator):
        def role_anchor_generation_input(
            self,
            role: Any,
            *,
            role_scope: str = ROLE_SCOPE_NO_REFERENCE,
        ) -> Mapping[str, Any]:
            document = dict(
                super().role_anchor_generation_input(role, role_scope=role_scope),
            )
            document["text"] = "ちがうぶんしょうです。"
            return document

    with pytest.raises(IncrementAnchorError, match="anchor発話文"):
        run_anchor_bootstrap_generation(
            plan_document=document,
            plan_sha256=document["plan_sha256"],
            round_index=0,
            identities=None,
            artifacts_dir=artifacts,
            run_id="test-drifting-text",
            generator=_DriftingGenerator(),
        )


def test_explicit_reference_scopeのgenerate_selectが5_roleで通る(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--text` バリアント用 anchor 補完の generate → select 一巡 (#201 回帰)。

    明示reference役は既定scopeでは anchor 対象外だが、この scope では対象になる。
    """

    path = tmp_path / "explicit-plan.json"
    write_anchor_bootstrap_plan(
        model=MODEL,
        model_revision=REVISION,
        scenarios_dir=SCENARIOS,
        voices_dir=VOICES,
        anchor_text=ANCHOR_TEXT,
        output_path=path,
        role_scope=ROLE_SCOPE_EXPLICIT_REFERENCE,
    )
    document = load_anchor_bootstrap_plan(path)
    assert document["role_scope"] == ROLE_SCOPE_EXPLICIT_REFERENCE
    assert len(document["targets"]) == 5

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    generator = _FakeGenerator()
    summary = run_anchor_bootstrap_generation(
        plan_document=document,
        plan_sha256=document["plan_sha256"],
        round_index=0,
        identities=None,
        artifacts_dir=artifacts,
        run_id="test-explicit-round0",
        generator=generator,
    )
    assert summary.generated_count == 5 * CANDIDATES_PER_ROLE
    assert summary.eligible_count == summary.generated_count
    assert summary.failed_count == 0
    # scope は adapter の anchor 生成 guard まで届いている必要がある。
    assert set(generator.role_scopes) == {ROLE_SCOPE_EXPLICIT_REFERENCE}

    ledger = json.loads((summary.run_dir / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["seed_base"] == VARIANT_SEED_BASE

    def fake_f0(audio_path: Path, **_kwargs: Any) -> dict[str, Any]:
        del audio_path
        return {"median_hz": 170.0}

    monkeypatch.setattr(increment_anchor, "measure_median_f0_hz", fake_f0)
    selection_summary = select_role_anchors(
        plan_document=document,
        plan_sha256=document["plan_sha256"],
        run_ids=["test-explicit-round0"],
        artifacts_dir=artifacts,
        output_dir=tmp_path / "explicit-selection",
    )
    assert selection_summary.selected_count == 5
    selection = json.loads(
        selection_summary.selection_path.read_text(encoding="utf-8"),
    )
    assert selection["role_scope"] == ROLE_SCOPE_EXPLICIT_REFERENCE
    assert all(
        group["role_identity"]["reference_voice"] is not None
        for group in selection["groups"]
    )
    assert validate_machine_anchor_selection(selection) == selection


def test_機械選抜は53_roleを選びauthorityをauto_selected_v1で記録する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document, summary = _select(
        tmp_path,
        monkeypatch,
        medians={},
        default_median=170.0,
    )
    assert summary.selected_count == ANCHOR_ROLE_COUNT
    assert summary.soft_signal_count == 0

    selection = json.loads(summary.selection_path.read_text(encoding="utf-8"))
    assert selection["protocol"] == "role-anchor-machine-selection-v1"
    assert selection["candidate_set_sha256"] == summary.decision_sha256
    assert (
        summary.selection_path.with_suffix(".sha256")
        .read_text(encoding="ascii")
        .strip()
        == summary.selection_sha256
    )
    group = selection["groups"][0]
    assert group["authority"] == {
        "type": SELECTION_AUTHORITY_TYPE,
        "policy_version": SELECTION_POLICY,
        "minimum_eligible_candidates": 3,
        "gate_policy_version": "take-gates-v2",
    }
    # 人手rubric (role-review-decision-v2) は一切捏造しない。
    assert "decision" not in group
    assert "rubric" not in json.dumps(selection)
    assert group["soft_signals"] == []

    decision = json.loads(summary.decision_path.read_text(encoding="utf-8"))
    assert decision["protocol"] == "role-anchor-machine-decision-v1"
    assert len(decision["groups"]) == ANCHOR_ROLE_COUNT
    assert len(decision["groups"][0]["candidates"]) == CANDIDATES_PER_ROLE
    assert decision["groups"][0]["decision"]["type"] == "selected"
    assert validate_machine_anchor_selection(selection) == selection
    assert len(document["targets"]) == ANCHOR_ROLE_COUNT


def test_screening全滅かつ上限未達はtop_upを要求して停止する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _plan_document()
    male = next(
        target
        for target in document["targets"]
        for role in document["roles"]
        if (role["scenario"], role["character"])
        == (target["scenario"], target["character"])
        and role["role"]["gender"] == "male"
    )
    with pytest.raises(IncrementAnchorError, match="top-up"):
        _select(
            tmp_path,
            monkeypatch,
            medians={male["character"]: 400.0},
            default_median=170.0,
        )


def test_上限到達後はbest_availableを選びsoft_signalを残す(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _plan_document()
    male = next(
        target
        for target in document["targets"]
        for role in document["roles"]
        if (role["scenario"], role["character"])
        == (target["scenario"], target["character"])
        and role["role"]["gender"] == "male"
    )
    _document, summary = _select(
        tmp_path,
        monkeypatch,
        medians={male["character"]: 400.0},
        default_median=170.0,
        run_rounds=(0, 1, 2),
    )
    assert summary.soft_signal_count == 1
    selection = json.loads(summary.selection_path.read_text(encoding="utf-8"))
    group = next(
        item
        for item in selection["groups"]
        if item["character"] == male["character"]
    )
    assert group["soft_signals"] == [SOFT_SIGNAL_EXHAUSTED]
    assert group["screening"]["status"] == "review_required"
    assert group["screening"]["signal"] == "gender_f0_above_expected"


def test_selectionはsoft_signalとscreeningの整合を強制する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _document, summary = _select(
        tmp_path,
        monkeypatch,
        medians={},
        default_median=170.0,
    )
    selection = json.loads(summary.selection_path.read_text(encoding="utf-8"))

    fabricated = json.loads(json.dumps(selection))
    fabricated["groups"][0]["screening"]["status"] = "pass"
    fabricated["groups"][0]["screening"]["median_f0_hz"] = 400.0
    fabricated["groups"][0]["screening"]["expected_gender"] = "male"
    with pytest.raises(IncrementAnchorError, match="F0 policy"):
        validate_machine_anchor_selection(fabricated)

    stray = json.loads(json.dumps(selection))
    stray["groups"][0]["soft_signals"] = [SOFT_SIGNAL_EXHAUSTED]
    with pytest.raises(IncrementAnchorError, match="soft signal"):
        validate_machine_anchor_selection(stray)

    tampered = json.loads(json.dumps(selection))
    tampered["groups"][0]["authority"]["type"] = "human"
    with pytest.raises(IncrementAnchorError, match="authority"):
        validate_machine_anchor_selection(tampered)


def test_選抜outputは書き直しを拒否する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _document, summary = _select(
        tmp_path,
        monkeypatch,
        medians={},
        default_median=170.0,
    )
    assert summary.output_dir.is_dir()
    with pytest.raises(IncrementAnchorError, match="既存path"):
        select_role_anchors(
            plan_document=_plan_document(),
            plan_sha256=_plan_document() and summary.decision_sha256,
            run_ids=["test-run-0"],
            artifacts_dir=tmp_path / "artifacts",
            output_dir=summary.output_dir,
        )


def test_canonical_bytes以外のbootstrap_planは拒否される(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_bytes(
        canonical_json(_plan_document()).encode("utf-8").replace(b"{", b"{ ", 1),
    )
    with pytest.raises(IncrementAnchorError, match="canonical"):
        load_anchor_bootstrap_plan(path)
