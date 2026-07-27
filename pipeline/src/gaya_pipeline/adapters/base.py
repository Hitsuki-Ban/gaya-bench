from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Capabilities:
    emotion: bool
    voice_prompt: bool
    clone: bool
    nonverbal: bool
    reading: bool

    def as_dict(self) -> dict[str, bool]:
        return {
            "emotion": self.emotion,
            "voice_prompt": self.voice_prompt,
            "clone": self.clone,
            "nonverbal": self.nonverbal,
            "reading": self.reading,
        }


@dataclass(frozen=True)
class ModelProfile:
    id: str
    name: str
    version: str
    license_note: str
    capabilities: Capabilities

    def as_manifest_entry(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "license_note": self.license_note,
            "capabilities": self.capabilities.as_dict(),
        }


@dataclass(frozen=True)
class LineJob:
    scene: Mapping[str, Any]
    character: Mapping[str, Any]
    line: Mapping[str, Any]
    locale: str

    @property
    def scenario_id(self) -> str:
        return str(self.scene["id"])

    @property
    def line_id(self) -> str:
        return str(self.line["id"])


class Adapter(Protocol):
    profile: ModelProfile

    def generation_params(self) -> Mapping[str, Any]:
        """Return requested parameters that can change generated audio."""
        ...

    def generation_input(self, job: LineJob) -> Mapping[str, Any]:
        """Return the exact model input used for the idempotency hash."""
        ...

    def generate(
        self,
        job: LineJob,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        """Write native PCM WAV and return realized generation parameters."""
        ...
