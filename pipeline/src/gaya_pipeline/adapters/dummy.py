from __future__ import annotations

import hashlib
import math
import struct
import wave
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from gaya_pipeline.adapters.base import Capabilities, LineJob, ModelProfile


class DummyAdapter:
    profile = ModelProfile(
        id="dummy",
        name="Dummy Beep",
        version="1",
        license_note="テスト専用（外部モデルなし）",
        capabilities=Capabilities(
            emotion=False,
            voice_prompt=False,
            clone=False,
            nonverbal=False,
            reading=False,
        ),
    )

    def prepare(
        self,
        jobs: Sequence[LineJob],
        artifacts_dir: Path,
    ) -> None:
        del jobs, artifacts_dir

    def generation_params(self) -> Mapping[str, Any]:
        return {
            "sample_rate_hz": 48_000,
            "duration_sec": 1.2,
            "tone_sec": 0.6,
            "amplitude": 0.2,
        }

    def generation_input(self, job: LineJob) -> Mapping[str, Any]:
        return {"text": str(job.line["text"])}

    def generate(
        self,
        job: LineJob,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        params = self.generation_params()
        sample_rate = int(params["sample_rate_hz"])
        duration_sec = float(params["duration_sec"])
        tone_sec = float(params["tone_sec"])
        amplitude = float(params["amplitude"])
        text_digest = hashlib.sha256(
            str(job.line["text"]).encode("utf-8"),
        ).digest()
        tone_hz = 360 + int.from_bytes(text_digest[:2], "big") % 360
        total_frames = round(sample_rate * duration_sec)
        tone_frames = round(sample_rate * tone_sec)
        tone_start = (total_frames - tone_frames) // 2

        output_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_wav), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            frames = bytearray()
            for frame_index in range(total_frames):
                if tone_start <= frame_index < tone_start + tone_frames:
                    tone_index = frame_index - tone_start
                    sample = amplitude * math.sin(
                        2 * math.pi * tone_hz * tone_index / sample_rate,
                    )
                else:
                    sample = 0.0
                frames.extend(struct.pack("<h", round(sample * 32767)))
            wav_file.writeframes(frames)

        return {"tone_hz": tone_hz}
