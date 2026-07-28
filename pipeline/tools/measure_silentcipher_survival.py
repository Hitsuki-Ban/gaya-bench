from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gaya_pipeline.adapters import create_adapter
from gaya_pipeline.adapters.irodori_tts import (
    MODEL_ID,
    PROFILE_VERSION,
    WATERMARK_PAYLOAD,
)
from gaya_pipeline.audio import (
    PostprocessProfile,
    encode_opus,
    find_audio_tools,
    normalize_wav,
    probe_audio,
)
from gaya_pipeline.generation import _load_jobs

SCENARIO_IDS = ("tavern-night", "market-day")
EXPECTED_PAYLOAD = [ord(character) for character in WATERMARK_PAYLOAD]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Irodori-TTSの固定12行について、SilentCipher payloadの"
            "後処理段階別残存状態を測定する。"
        ),
    )
    parser.add_argument("--scenarios-dir", type=Path, required=True)
    parser.add_argument("--voices-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def _require_directory(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir():
        raise RuntimeError(f"{label}がディレクトリではありません: {resolved}")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decoder_model(adapter: Any) -> Any:
    native_runtime = getattr(adapter, "_runtime", None)
    inference_runtime = getattr(native_runtime, "_runtime", None)
    watermarker = getattr(inference_runtime, "watermarker", None)
    model = getattr(watermarker, "model", None)
    if model is None:
        raise RuntimeError("固定SilentCipher decoderを取得できません。")
    return model


def _decode(model: Any, path: Path, *, phase_shift: bool) -> dict[str, Any]:
    decoded = model.decode(str(path), phase_shift_decoding=phase_shift)
    if not isinstance(decoded, dict) or not isinstance(decoded.get("status"), bool):
        raise RuntimeError(f"SilentCipher decode結果が不正です: {path}")

    if not decoded["status"]:
        return {
            "state": "undetected",
            "message_bytes": [],
            "confidence": None,
        }

    messages = decoded.get("messages")
    confidences = decoded.get("confidences")
    if (
        not isinstance(messages, list)
        or len(messages) != 1
        or not isinstance(messages[0], list)
        or not all(isinstance(value, int) for value in messages[0])
        or not isinstance(confidences, list)
        or len(confidences) != 1
        or not isinstance(confidences[0], (int, float))
        or isinstance(confidences[0], bool)
    ):
        raise RuntimeError(f"SilentCipher検出結果の項目が不正です: {path}")

    message = list(messages[0])
    return {
        "state": "exact" if message == EXPECTED_PAYLOAD else "mismatch",
        "message_bytes": message,
        "confidence": round(float(confidences[0]), 6),
    }


def _stage_result(
    model: Any,
    path: Path,
    *,
    tools: Any,
    relative_to: Path,
) -> dict[str, Any]:
    probe = probe_audio(tools, path)
    return {
        "path": path.relative_to(relative_to).as_posix(),
        "sha256": _sha256_file(path),
        "codec": probe.codec_name,
        "sample_rate_hz": probe.sample_rate_hz,
        "channels": probe.channels,
        "duration_sec": round(probe.duration_sec, 6),
        "decode": {
            "plain": _decode(model, path, phase_shift=False),
            "phase_shift": _decode(model, path, phase_shift=True),
        },
    }


def _summarize(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for stage_name in ("source_pcm16", "normalized_wav", "final_opus"):
        for decode_mode in ("plain", "phase_shift"):
            counts = Counter(
                record["stages"][stage_name]["decode"][decode_mode]["state"]
                for record in records
            )
            summary[f"{stage_name}.{decode_mode}"] = {
                state: counts[state]
                for state in ("exact", "mismatch", "undetected")
            }
    return summary


def main() -> int:
    args = _parse_args()
    scenarios_dir = _require_directory(args.scenarios_dir, "scenarios-dir")
    voices_dir = _require_directory(args.voices_dir, "voices-dir")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    jobs = [
        job
        for scenario_id in SCENARIO_IDS
        for job in _load_jobs(
            scenarios_dir,
            scenario_id=scenario_id,
            line_id=None,
        )
    ]
    if len(jobs) != 12:
        raise RuntimeError(f"測定対象は12行である必要があります: {len(jobs)}")

    adapter = create_adapter(MODEL_ID)
    adapter.prepare(jobs, output_dir, voices_dir)
    tools = find_audio_tools()
    profile = PostprocessProfile()
    records: list[dict[str, Any]] = []

    for job in jobs:
        clip_dir = output_dir / job.scenario_id
        clip_dir.mkdir(parents=True, exist_ok=True)
        source_wav = clip_dir / f"{job.line_id}-source.wav"
        normalized_wav = clip_dir / f"{job.line_id}-normalized.wav"
        final_opus = clip_dir / f"{job.line_id}.opus"

        realized = dict(adapter.generate(job, source_wav))
        loudness = normalize_wav(tools, source_wav, normalized_wav, profile)
        encode_opus(tools, normalized_wav, final_opus, profile)
        decoder = _decoder_model(adapter)

        records.append(
            {
                "scenario": job.scenario_id,
                "line": job.line_id,
                "generation_realized": realized,
                "loudness": loudness.as_dict(),
                "stages": {
                    "source_pcm16": _stage_result(
                        decoder,
                        source_wav,
                        tools=tools,
                        relative_to=output_dir,
                    ),
                    "normalized_wav": _stage_result(
                        decoder,
                        normalized_wav,
                        tools=tools,
                        relative_to=output_dir,
                    ),
                    "final_opus": _stage_result(
                        decoder,
                        final_opus,
                        tools=tools,
                        relative_to=output_dir,
                    ),
                },
            },
        )
        print(f"測定完了: {job.scenario_id}/{job.line_id}", flush=True)

    report = {
        "format_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {
            "id": MODEL_ID,
            "version": PROFILE_VERSION,
        },
        "payload": WATERMARK_PAYLOAD,
        "payload_bytes": EXPECTED_PAYLOAD,
        "scenario_ids": list(SCENARIO_IDS),
        "postprocess": profile.as_dict(),
        "summary": _summarize(records),
        "records": records,
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
