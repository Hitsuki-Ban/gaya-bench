from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
import yaml
from gaya_pipeline import voice_assets
from gaya_pipeline.audio import AudioProbe, AudioTools
from gaya_pipeline.voice_assets import (
    validate_local_voice_assets,
    validate_voice_metadata,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VOICES_DIR = REPOSITORY_ROOT / "assets" / "voices"


def _copy_metadata(tmp_path: Path) -> Path:
    voices_dir = tmp_path / "assets" / "voices"
    voices_dir.mkdir(parents=True)
    for filename in ("metadata.schema.json", "metadata.yaml"):
        shutil.copy2(VOICES_DIR / filename, voices_dir / filename)
    return voices_dir


def _read_metadata(voices_dir: Path) -> dict[str, object]:
    return yaml.safe_load(
        (voices_dir / "metadata.yaml").read_text(encoding="utf-8"),
    )


def _write_metadata(
    voices_dir: Path,
    document: dict[str, object],
) -> None:
    (voices_dir / "metadata.yaml").write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _create_local_files(voices_dir: Path) -> dict[str, float]:
    document = _read_metadata(voices_dir)
    durations: dict[str, float] = {}
    for entry in document["voices"]:
        audio_path = voices_dir / entry["file"]
        audio_path.parent.mkdir(parents=True)
        payload = f"fixture:{entry['id']}".encode()
        audio_path.write_bytes(payload)
        entry["sha256"] = hashlib.sha256(payload).hexdigest()
        durations[str(audio_path)] = float(entry["duration_sec"])
    _write_metadata(voices_dir, document)
    return durations


def test_repository_metadata_is_valid() -> None:
    result = validate_voice_metadata(VOICES_DIR)

    assert len(result.voice_ids) == 5
    assert result.problems == ()


def test_current_references_match_registered_voice_profiles() -> None:
    document = _read_metadata(VOICES_DIR)
    profiles = {entry["id"]: entry["voice"] for entry in document["voices"]}
    references: dict[str, list[tuple[str, dict[str, object]]]] = {
        voice_id: [] for voice_id in profiles
    }

    for scenario_path in sorted(
        (REPOSITORY_ROOT / "scenarios").glob("*.yaml"),
    ):
        scenario = yaml.safe_load(
            scenario_path.read_text(encoding="utf-8"),
        )
        for character in scenario["characters"]:
            voice_id = character.get("reference_voice")
            if voice_id is not None:
                references[voice_id].append((scenario["id"], character))

    assert all(len(items) == 1 for items in references.values())
    for voice_id, [(scenario_id, character)] in references.items():
        profile = profiles[voice_id]
        assert character["gender"] == profile["gender"], scenario_id
        assert character["age"] == profile["age"], scenario_id


def test_duplicate_voice_id_is_rejected(tmp_path: Path) -> None:
    voices_dir = _copy_metadata(tmp_path)
    document = _read_metadata(voices_dir)
    document["voices"][1]["id"] = document["voices"][0]["id"]
    document["voices"][1]["file"] = document["voices"][0]["file"]
    _write_metadata(voices_dir, document)

    result = validate_voice_metadata(voices_dir)

    assert any("重複" in problem.reason for problem in result.problems)


def test_extra_metadata_field_is_rejected(tmp_path: Path) -> None:
    voices_dir = _copy_metadata(tmp_path)
    document = _read_metadata(voices_dir)
    document["voices"][0]["unexpected"] = True
    _write_metadata(voices_dir, document)

    result = validate_voice_metadata(voices_dir)

    assert len(result.problems) == 1
    assert result.problems[0].target == "$.voices[0]"


def test_transcript_rights_are_required(tmp_path: Path) -> None:
    voices_dir = _copy_metadata(tmp_path)
    document = _read_metadata(voices_dir)
    del document["voices"][0]["transcript_rights"]
    _write_metadata(voices_dir, document)

    result = validate_voice_metadata(voices_dir)

    assert len(result.problems) == 1
    assert result.problems[0].target == "$.voices[0]"
    assert "transcript_rights" in result.problems[0].reason


def test_file_must_match_voice_id(tmp_path: Path) -> None:
    voices_dir = _copy_metadata(tmp_path)
    document = _read_metadata(voices_dir)
    document["voices"][0]["file"] = "different/reference.wav"
    _write_metadata(voices_dir, document)

    result = validate_voice_metadata(voices_dir)

    assert len(result.problems) == 1
    assert result.problems[0].target == "$.voices[0].file"


def test_missing_local_wav_is_rejected(tmp_path: Path) -> None:
    result = validate_local_voice_assets(
        _copy_metadata(tmp_path),
        tools=AudioTools("ffmpeg", "ffprobe"),
    )

    assert len(result.problems) == 5
    assert all("存在しません" in problem.reason for problem in result.problems)


def test_local_voice_assets_are_fully_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voices_dir = _copy_metadata(tmp_path)
    durations = _create_local_files(voices_dir)

    def fake_probe(_tools: AudioTools, audio_path: Path) -> AudioProbe:
        return AudioProbe(
            codec_name="pcm_s16le",
            sample_rate_hz=48_000,
            channels=1,
            duration_sec=durations[str(audio_path)],
        )

    monkeypatch.setattr(voice_assets, "probe_audio", fake_probe)

    result = validate_local_voice_assets(
        voices_dir,
        tools=AudioTools("ffmpeg", "ffprobe"),
    )

    assert len(result.voice_ids) == 5
    assert result.problems == ()


def test_unregistered_local_wav_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voices_dir = _copy_metadata(tmp_path)
    durations = _create_local_files(voices_dir)
    orphan_path = voices_dir / "orphan" / "reference.wav"
    orphan_path.parent.mkdir()
    orphan_path.write_bytes(b"orphan")
    monkeypatch.setattr(
        voice_assets,
        "probe_audio",
        lambda _tools, audio_path: AudioProbe(
            codec_name="pcm_s16le",
            sample_rate_hz=48_000,
            channels=1,
            duration_sec=durations[str(audio_path)],
        ),
    )

    result = validate_local_voice_assets(
        voices_dir,
        tools=AudioTools("ffmpeg", "ffprobe"),
    )

    assert len(result.problems) == 1
    assert result.problems[0].file == orphan_path
    assert "未登録" in result.problems[0].reason


@pytest.mark.parametrize(
    ("probe", "expected"),
    [
        (AudioProbe("pcm_f32le", 48_000, 1, 10.5), "pcm_s16le"),
        (AudioProbe("pcm_s16le", 44_100, 1, 10.5), "48000"),
        (AudioProbe("pcm_s16le", 48_000, 2, 10.5), "channels"),
        (AudioProbe("pcm_s16le", 48_000, 1, 9.9), "10〜20"),
    ],
)
def test_invalid_audio_properties_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe: AudioProbe,
    expected: str,
) -> None:
    voices_dir = _copy_metadata(tmp_path)
    _create_local_files(voices_dir)
    monkeypatch.setattr(
        voice_assets,
        "probe_audio",
        lambda _tools, _path: probe,
    )

    result = validate_local_voice_assets(
        voices_dir,
        tools=AudioTools("ffmpeg", "ffprobe"),
    )

    assert any(expected in problem.reason for problem in result.problems)
