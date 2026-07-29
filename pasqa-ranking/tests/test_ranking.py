from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path
from typing import Any

import pytest
from gaya_pasqa_ranking import ranking
from gaya_pasqa_ranking.ranking import RankingError


class FakePredictor:
    def __init__(self, vocab: dict[str, int], scores: dict[str, float]) -> None:
        self.mora_vocab = vocab
        self._scores = scores

    def predict(self, *, mora: list[str], wav_path: Path) -> dict[str, Any]:
        assert mora == ["ト", "マ", "レ"]
        return {
            "mos": self._scores[wav_path.name],
            "frame_lengths": 7,
        }


def _write_wav(
    path: Path,
    *,
    sample_rate: int = 16_000,
    channels: int = 1,
    frames: int = 1_600,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\0\0" * frames * channels)


def _write_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, int]]:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    contents = {
        ranking.PASQA_CHECKPOINT_NAME: b"checkpoint",
        ranking.PASQA_CONFIG_NAME: b"config",
        ranking.PASQA_VOCAB_NAME: "\nト\nマ\nレ\n".encode(),
    }
    hashes: dict[str, str] = {}
    for filename, content in contents.items():
        (model_dir / filename).write_bytes(content)
        hashes[filename] = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(ranking, "MODEL_FILE_SHA256", hashes)
    return model_dir, {"ト": 0, "マ": 1, "レ": 2}


def _write_input(tmp_path: Path) -> Path:
    _write_wav(tmp_path / "audio" / "take-0001.wav")
    _write_wav(tmp_path / "audio" / "take-0002.wav")
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "group": {
                    "scenario_id": "castle-gate",
                    "line_id": "guard-onna-002",
                    "model_id": "qwen3-tts-12hz-1.7b",
                    "variant": "dry",
                },
                "mora_tokens": ["ト", "マ", "レ"],
                "takes": [
                    {
                        "take_id": "a" * 64,
                        "audio_path": "audio/take-0001.wav",
                    },
                    {
                        "take_id": "b" * 64,
                        "audio_path": "audio/take-0002.wav",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return input_path


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ranking, "_require_python_310", lambda: None)
    monkeypatch.setattr(ranking, "_require_runtime_versions", lambda: None)
    real_version = ranking.importlib.metadata.version

    def fake_version(name: str) -> str:
        if name in {"torch", "torchaudio"}:
            return "2.8.0+cpu"
        return real_version(name)

    monkeypatch.setattr(ranking.importlib.metadata, "version", fake_version)


def test_same_line_takeをscore降順で順位付けしprovenanceを固定する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(monkeypatch)
    model_dir, vocab = _write_contract(tmp_path, monkeypatch)
    input_path = _write_input(tmp_path)
    output_path = tmp_path / "report.json"

    result = ranking.run_ranking(
        model_dir=model_dir,
        input_path=input_path,
        output_path=output_path,
        predictor_factory=lambda _checkpoint, _config: FakePredictor(
            vocab,
            {"take-0001.wav": 2.5, "take-0002.wav": 4.0},
        ),
    )

    assert [item["take_id"] for item in result["rankings"]] == [
        "b" * 64,
        "a" * 64,
    ]
    assert [item["rank"] for item in result["rankings"]] == [1, 2]
    assert result["usage"] == "ranking_only_no_absolute_threshold"
    assert result["provenance"]["pasqa"]["code_commit"] == ranking.PASQA_CODE_COMMIT
    assert result["provenance"]["runtime"]["device"] == "cpu"
    assert json.loads(output_path.read_text(encoding="utf-8")) == result


def test_unknown_moraはpredictorを呼ぶ前に拒否する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(monkeypatch)
    model_dir, _vocab = _write_contract(tmp_path, monkeypatch)
    input_path = _write_input(tmp_path)
    document = json.loads(input_path.read_text(encoding="utf-8"))
    document["mora_tokens"] = ["未登録"]
    input_path.write_text(
        json.dumps(document, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(RankingError, match="固定 PASQA vocab にない"):
        ranking.run_ranking(
            model_dir=model_dir,
            input_path=input_path,
            output_path=tmp_path / "report.json",
            predictor_factory=lambda _checkpoint, _config: pytest.fail(
                "predictor must not be created"
            ),
        )


@pytest.mark.parametrize(
    ("sample_rate", "channels", "frames", "message"),
    [
        (24_000, 1, 1_600, "16 kHz"),
        (16_000, 2, 1_600, "mono"),
        (16_000, 1, 1_039, "sample数"),
        (16_000, 1, 160_001, "sample数"),
    ],
)
def test_implicit_audio変換が必要な入力を拒否する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_rate: int,
    channels: int,
    frames: int,
    message: str,
) -> None:
    _patch_runtime(monkeypatch)
    model_dir, vocab = _write_contract(tmp_path, monkeypatch)
    input_path = _write_input(tmp_path)
    _write_wav(
        tmp_path / "audio" / "take-0001.wav",
        sample_rate=sample_rate,
        channels=channels,
        frames=frames,
    )

    with pytest.raises(RankingError, match=message):
        ranking.run_ranking(
            model_dir=model_dir,
            input_path=input_path,
            output_path=tmp_path / "report.json",
            predictor_factory=lambda _checkpoint, _config: FakePredictor(
                vocab,
                {},
            ),
        )


def test_output既存時は上書きしない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(monkeypatch)
    model_dir, vocab = _write_contract(tmp_path, monkeypatch)
    input_path = _write_input(tmp_path)
    output_path = tmp_path / "report.json"
    output_path.write_text("keep", encoding="utf-8")

    with pytest.raises(RankingError, match="既に存在"):
        ranking.run_ranking(
            model_dir=model_dir,
            input_path=input_path,
            output_path=output_path,
            predictor_factory=lambda _checkpoint, _config: FakePredictor(
                vocab,
                {},
            ),
        )
    assert output_path.read_text(encoding="utf-8") == "keep"


def test_atomic_commitも既存fileを上書きしない(tmp_path: Path) -> None:
    temporary_path = tmp_path / "temporary"
    target = tmp_path / "target"
    temporary_path.write_text("new", encoding="utf-8")
    target.write_text("keep", encoding="utf-8")

    with pytest.raises(RankingError, match="上書きしません"):
        ranking._commit_new_file(temporary_path, target)
    assert target.read_text(encoding="utf-8") == "keep"


def test_inference中にaudioが変更されたらprovenanceを確定しない(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(monkeypatch)
    model_dir, vocab = _write_contract(tmp_path, monkeypatch)
    input_path = _write_input(tmp_path)

    class MutatingPredictor(FakePredictor):
        def predict(self, *, mora: list[str], wav_path: Path) -> dict[str, Any]:
            result = super().predict(mora=mora, wav_path=wav_path)
            wav_path.write_bytes(wav_path.read_bytes() + b"changed")
            return result

    with pytest.raises(RankingError, match="音声が検査後に変更"):
        ranking.run_ranking(
            model_dir=model_dir,
            input_path=input_path,
            output_path=tmp_path / "report.json",
            predictor_factory=lambda _checkpoint, _config: MutatingPredictor(
                vocab,
                {"take-0001.wav": 2.5, "take-0002.wav": 4.0},
            ),
        )


def test_vocab_fallbackを検出して停止する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runtime(monkeypatch)
    model_dir, _vocab = _write_contract(tmp_path, monkeypatch)
    input_path = _write_input(tmp_path)

    with pytest.raises(RankingError, match="tokenizer fallback"):
        ranking.run_ranking(
            model_dir=model_dir,
            input_path=input_path,
            output_path=tmp_path / "report.json",
            predictor_factory=lambda _checkpoint, _config: FakePredictor(
                {"別": 0},
                {},
            ),
        )
