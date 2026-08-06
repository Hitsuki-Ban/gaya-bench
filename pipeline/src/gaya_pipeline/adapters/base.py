from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None


def _freeze_sampling(
    sampling: Mapping[str, Any],
) -> tuple[tuple[str, JsonScalar], ...]:
    frozen_items: list[tuple[str, JsonScalar]] = []
    for key, value in sampling.items():
        if not isinstance(key, str):
            raise TypeError("sampling のキーは文字列である必要があります。")
        if value is not None and not isinstance(value, str | int | float):
            raise TypeError(f"sampling.{key} は JSON scalar である必要があります。")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"sampling.{key} に非有限数は使用できません。")
        frozen_items.append((key, value))
    return tuple(sorted(frozen_items))


@dataclass(frozen=True)
class TakeContext:
    index: int
    seed: int | None
    recipe_version: str
    sampling: tuple[tuple[str, JsonScalar], ...]

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int) or self.index < 1:
            raise ValueError("take index は 1 以上の整数である必要があります。")
        if self.seed is not None and (
            isinstance(self.seed, bool) or not isinstance(self.seed, int)
        ):
            raise TypeError("take seed は整数または null である必要があります。")
        if not isinstance(self.recipe_version, str) or not self.recipe_version:
            raise ValueError("take recipe_version は空にできません。")
        if not isinstance(self.sampling, tuple):
            raise TypeError("take sampling は key 順に固定した tuple が必要です。")
        sampling_keys = tuple(
            item[0]
            for item in self.sampling
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str)
        )
        if (
            any(
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], str)
                or (
                    item[1] is not None
                    and not isinstance(item[1], str | int | float)
                )
                or (
                    isinstance(item[1], float)
                    and not math.isfinite(item[1])
                )
                for item in self.sampling
            )
            or tuple(sorted(sampling_keys)) != sampling_keys
            or len(set(sampling_keys)) != len(sampling_keys)
        ):
            raise TypeError("take sampling は key 順に固定した tuple が必要です。")

    @classmethod
    def create(
        cls,
        *,
        index: int,
        seed: int | None,
        recipe_version: str,
        sampling: Mapping[str, Any],
    ) -> TakeContext:
        return cls(
            index=index,
            seed=seed,
            recipe_version=recipe_version,
            sampling=_freeze_sampling(sampling),
        )

    def sampling_dict(self) -> dict[str, Any]:
        return dict(self.sampling)


@dataclass(frozen=True)
class TakeRecipe:
    version: str
    seed_policy: Literal["none", "fixed", "derived-sha256-v1"]
    single_take_seed: int | None
    seed_range: tuple[int, int] | None
    sampling: tuple[tuple[str, JsonScalar], ...]
    supports_multiple: bool

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("take recipe version は空にできません。")
        if self.seed_policy not in {"none", "fixed", "derived-sha256-v1"}:
            raise ValueError("take seed policy が不正です。")
        if not isinstance(self.supports_multiple, bool):
            raise TypeError("supports_multiple は bool が必要です。")
        if self.seed_policy == "none":
            if self.single_take_seed is not None or self.seed_range is not None:
                raise ValueError("seed を使わない recipe に seed 設定は指定できません。")
        else:
            if (
                isinstance(self.single_take_seed, bool)
                or not isinstance(self.single_take_seed, int)
            ):
                raise TypeError("seed recipe には single_take_seed が必要です。")
            if (
                not isinstance(self.seed_range, tuple)
                or len(self.seed_range) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in self.seed_range
                )
                or self.seed_range[0] > self.seed_range[1]
                or not self.seed_range[0] <= self.single_take_seed <= self.seed_range[1]
            ):
                raise ValueError("seed recipe の seed range が不正です。")
        TakeContext(
            index=1,
            seed=self.single_take_seed,
            recipe_version=self.version,
            sampling=self.sampling,
        )

    def single_take_context(self) -> TakeContext:
        return TakeContext(
            index=1,
            seed=self.single_take_seed,
            recipe_version=self.version,
            sampling=self.sampling,
        )


def require_take_context(
    context: TakeContext,
    recipe: TakeRecipe,
) -> None:
    if context.recipe_version != recipe.version:
        raise ValueError(
            "未対応の take recipe です: "
            f"expected={recipe.version}, actual={context.recipe_version}",
        )
    if context.sampling != recipe.sampling:
        raise ValueError("take sampling が adapter recipe と一致しません。")
    if not recipe.supports_multiple and context.index != 1:
        raise ValueError("この adapter は複数 take に対応していません。")
    if recipe.seed_policy == "none":
        if context.seed is not None:
            raise ValueError("deterministic adapter に seed は指定できません。")
        return
    if context.seed is None:
        raise ValueError("この adapter には明示的な seed が必要です。")
    if recipe.seed_range is None:
        raise ValueError("seed range が adapter recipe に定義されていません。")
    lower, upper = recipe.seed_range
    if not lower <= context.seed <= upper:
        raise ValueError(
            f"take seed は {lower}..{upper} の範囲である必要があります。",
        )
    if not recipe.supports_multiple and context.seed != recipe.single_take_seed:
        raise ValueError("この adapter は固定 single-take seed のみ対応しています。")


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
    # 条件バリアント列 (#201) だけが持つ optional field。
    # `{"base_model": ..., "mode": "human-reference"|"text-only"}`。
    conditioning: Mapping[str, str] | None = None

    def as_manifest_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "license_note": self.license_note,
            "capabilities": self.capabilities.as_dict(),
        }
        if self.conditioning is not None:
            entry["conditioning"] = dict(self.conditioning)
        return entry


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

    def take_recipe(self) -> TakeRecipe:
        """Return the exact take recipe and capability implemented by the adapter."""
        ...

    def prepare(
        self,
        jobs: Sequence[LineJob],
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        """Prepare persistent inputs required before generation hashes are built."""
        ...

    def generation_params(self) -> Mapping[str, Any]:
        """Return requested parameters that can change generated audio."""
        ...

    def generation_input(
        self,
        job: LineJob,
        take_context: TakeContext,
    ) -> Mapping[str, Any]:
        """Return the exact model input used for the idempotency hash."""
        ...

    def generate(
        self,
        job: LineJob,
        take_context: TakeContext,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        """Write native PCM WAV and return realized generation parameters."""
        ...
