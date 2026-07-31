from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import gaya_pipeline.completion_listen as completion_listen
from gaya_pipeline.completion_listen import (
    CompletionListeningError,
    _load_target_lines,
)


def test_target_linesは重複modelを除いたscenario_line集合を構成する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (tmp_path / "assets" / "voices").mkdir(parents=True)
    monkeypatch.setattr(
        completion_listen,
        "validate_scenario_ids",
        lambda *_args, **_kwargs: SimpleNamespace(problems=()),
    )
    (scenarios / "scene.yaml").write_text(
        """
format_version: 1
id: scene
title: Scene
locale: ja
scene:
  setting: test
characters:
  - id: actor
    name: Actor
    gender: neutral
    age: adult
    voice: Test voice
lines:
  - id: line-001
    character: actor
    text: 台詞
    delivery: 強く
    emotion: neutral
    intensity: 1
""".lstrip(),
        encoding="utf-8",
    )

    scenario_sha256, lines = _load_target_lines(
        scenarios_dir=scenarios,
        voices_dir=tmp_path / "assets" / "voices",
        targets={("scene", "line-001")},
    )

    assert len(scenario_sha256) == 64
    assert lines == [
        {
            "scenario": "scene",
            "line": "line-001",
            "scenario_title": "Scene",
            "text": "台詞",
            "delivery": "強く",
        },
    ]


def test_target_linesは存在しないlineを拒否する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (tmp_path / "assets" / "voices").mkdir(parents=True)
    monkeypatch.setattr(
        completion_listen,
        "validate_scenario_ids",
        lambda *_args, **_kwargs: SimpleNamespace(problems=()),
    )
    (scenarios / "scene.yaml").write_text(
        """
format_version: 1
id: scene
title: Scene
locale: ja
scene:
  setting: test
characters:
  - id: actor
    name: Actor
    gender: neutral
    age: adult
    voice: Test voice
lines:
  - id: line-001
    character: actor
    text: 台詞
    delivery: 強く
    emotion: neutral
    intensity: 1
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(CompletionListeningError, match="ありません"):
        _load_target_lines(
            scenarios_dir=scenarios,
            voices_dir=tmp_path / "assets" / "voices",
            targets={("scene", "line-999")},
        )
