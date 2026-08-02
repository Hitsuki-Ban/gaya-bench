import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_TOOL_PATH = Path(__file__).parents[1] / "tools" / "issue190_listening_results.py"
_SPEC = importlib.util.spec_from_file_location("issue190_listening_results", _TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
ResultError = _MODULE.ResultError
canonical_bytes = _MODULE.canonical_bytes
summarize = _MODULE.summarize


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, list[str]]:
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "audio").mkdir(parents=True)
    candidate_ids = [_sha(b"candidate-a"), _sha(b"candidate-b")]
    study_id = _sha(b"study")
    group_id = _sha(b"group")
    bundle = {
        "format_version": 1,
        "protocol": "baseline-quality-ab-bundle-v1",
        "study_id": study_id,
        "title": "test",
        "instructions": "listen",
        "groups": [
            {
                "id": group_id,
                "track": "supertonic-speed",
                "model": "supertonic-3",
                "scenario": "castle-gate",
                "line": "guard-001",
                "text": "止まれ。",
                "focus": "速度を比較",
                "candidates": [
                    {
                        "id": candidate_ids[0],
                        "variant": "speed-1.05",
                        "audio_path": f"audio/{candidate_ids[0]}.wav",
                        "audio_sha256": _sha(b"audio-a"),
                    },
                    {
                        "id": candidate_ids[1],
                        "variant": "speed-1.00",
                        "audio_path": f"audio/{candidate_ids[1]}.wav",
                        "audio_sha256": _sha(b"audio-b"),
                    },
                ],
            }
        ],
    }
    bundle_raw = canonical_bytes(bundle)
    (bundle_dir / "baseline-quality-ab-bundle-v1.json").write_bytes(bundle_raw)
    (bundle_dir / "baseline-quality-ab-bundle-v1.sha256").write_text(
        _sha(bundle_raw), encoding="ascii"
    )
    (bundle_dir / "audio" / f"{candidate_ids[0]}.wav").write_bytes(b"audio-a")
    (bundle_dir / "audio" / f"{candidate_ids[1]}.wav").write_bytes(b"audio-b")
    result = {
        "format_version": 1,
        "protocol": "baseline-quality-ab-result-v1",
        "study_id": study_id,
        "groups": [
            {
                "id": group_id,
                "heard_candidate_ids": candidate_ids,
                "choice": candidate_ids[1],
                "notes": "",
            }
        ],
    }
    result_path = tmp_path / "result.json"
    result_path.write_bytes(canonical_bytes(result))
    return bundle_dir, result_path, candidate_ids


def test_completed_blind_result_is_bound_and_summarized(tmp_path: Path) -> None:
    bundle_dir, result_path, _candidate_ids = _fixture(tmp_path)

    report = summarize(bundle_dir, result_path)

    assert report["group_count"] == 1
    assert report["by_track"] == [
        {
            "track": "supertonic-speed",
            "group_count": 1,
            "choice_counts": {"speed-1.00": 1},
        }
    ]


def test_incomplete_playback_is_rejected(tmp_path: Path) -> None:
    bundle_dir, result_path, candidate_ids = _fixture(tmp_path)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["groups"][0]["heard_candidate_ids"] = candidate_ids[:1]
    result_path.write_bytes(canonical_bytes(result))

    with pytest.raises(ResultError, match="complete playback"):
        summarize(bundle_dir, result_path)
