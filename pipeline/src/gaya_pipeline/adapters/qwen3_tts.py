from __future__ import annotations

import gc
import hashlib
import importlib
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol, TypeVar

import yaml

from gaya_pipeline.adapters.base import (
    Capabilities,
    LineJob,
    ModelProfile,
    TakeContext,
    TakeRecipe,
    require_take_context,
)
from gaya_pipeline.adapters.conditioning import (
    effective_reference_voice,
    normalize_conditioning_mode,
    variant_profile,
)
from gaya_pipeline.conditioning_variants import (
    ROLE_SCOPE_NO_REFERENCE,
    anchor_scope_allows_explicit_reference,
)
from gaya_pipeline.completion_anchor import CompletionAnchorError
from gaya_pipeline.completion_plan import (
    CompletionPlanError,
    RoleSnapshot,
    build_role_snapshot,
)
from gaya_pipeline.voice_assets import validate_voice_metadata

MODEL_ID = "qwen3-tts-12hz-1.7b"
QWEN_TTS_VERSION = "0.1.1"
BASE_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
BASE_REVISION = "fd4b254389122332181a7c3db7f27e918eec64e3"
VOICE_DESIGN_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
VOICE_DESIGN_REVISION = "5ecdb67327fd37bb2e042aab12ff7391903235d3"
PROFILE_VERSION = (
    f"qwen-tts {QWEN_TTS_VERSION}; "
    f"Base {BASE_REVISION}; VoiceDesign {VOICE_DESIGN_REVISION}"
)
DEVICE = "cuda:0"
DTYPE = "bfloat16"
ATTENTION_BACKEND = "sdpa"
LANGUAGE = "Japanese"
SEED = 0
REFERENCE_TEXT = "さて、きょうもいちにちをはじめましょう。"
# increment / conditioning variant の anchor bootstrap は adapter module が公開する
# `ROLE_ANCHOR_TEXT` を役別anchorの発話文として読む (Irodori v4 も v3 の同名定数を
# そのまま再公開している)。Qwen の anchor 文は VoiceDesign reference と同一なので、
# 文字列を複製せず別名で公開する。#174 の凍結 anchor source plan
# (`phase_a.anchor_texts`) と `completion_anchor._anchor_texts()` も同じ定数を指す。
ROLE_ANCHOR_TEXT = REFERENCE_TEXT
_GENDER_LABELS = {
    "female": "女性",
    "male": "男性",
    "neutral": "中性的",
}
_AGE_LABELS = {
    "child": "子ども",
    "teen": "10代",
    "young_adult": "若い成人",
    "adult": "成人",
    "middle_aged": "中年",
    "elderly": "高齢者",
}
_KIND_LABELS = {
    "human": "人間",
    "machine": "機械",
    "creature": "生物",
    "spirit": "精霊",
}

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MIB = 1024 * 1024
_SAMPLING: dict[str, int | float | bool] = {
    "do_sample": True,
    "repetition_penalty": 1.05,
    "temperature": 0.9,
    "top_p": 1.0,
    "top_k": 50,
    "subtalker_dosample": True,
    "subtalker_temperature": 0.9,
    "subtalker_top_p": 1.0,
    "subtalker_top_k": 50,
    "max_new_tokens": 2048,
}
_PEAK_KEYS = {"allocated_mib", "reserved_mib"}
_SELECTED_ANCHOR_CONTROL = "selected_voice_design_anchor"
_ASSET_REFERENCE_CONTROL = "voice_asset"
_ReferenceKey = tuple[str, str]


class Qwen3TTSAdapterError(RuntimeError):
    pass


class _Runtime(Protocol):
    def snapshot_download(self, repo_id: str, revision: str) -> Path: ...

    def load_model(self, snapshot_path: Path) -> Any: ...

    def generate_voice_design(
        self,
        model: Any,
        *,
        text: str,
        language: str,
        instruct: str,
        sampling: Mapping[str, int | float | bool],
    ) -> tuple[Sequence[Any], int]: ...

    def create_voice_clone_prompt(
        self,
        model: Any,
        *,
        ref_audio: str,
        ref_text: str,
    ) -> Any: ...

    def generate_voice_clone(
        self,
        model: Any,
        *,
        text: str,
        language: str,
        voice_clone_prompt: Any,
        sampling: Mapping[str, int | float | bool],
    ) -> tuple[Sequence[Any], int]: ...

    def write_pcm16(self, path: Path, samples: Any, sample_rate: int) -> None: ...

    def seed(self, seed: int) -> None: ...

    def reset_peak_memory_stats(self) -> None: ...

    def peak_memory_mib(self) -> dict[str, float]: ...

    def release_model(self) -> None: ...

    def is_out_of_memory(self, error: BaseException) -> bool: ...


class _NativeRuntime:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise Qwen3TTSAdapterError(
                "Qwen3-TTS は Windows native CUDA:0 だけをサポートします。",
            )

        try:
            installed_version = metadata.version("qwen-tts")
        except metadata.PackageNotFoundError as error:
            raise Qwen3TTSAdapterError(
                "依存 qwen-tts==0.1.1 がインストールされていません。",
            ) from error
        if installed_version != QWEN_TTS_VERSION:
            raise Qwen3TTSAdapterError(
                "qwen-tts の version が一致しません: "
                f"expected={QWEN_TTS_VERSION}, actual={installed_version}",
            )

        try:
            self.torch = importlib.import_module("torch")
            self.soundfile = importlib.import_module("soundfile")
            huggingface_hub = importlib.import_module("huggingface_hub")
            qwen_tts = importlib.import_module("qwen_tts")
        except (ImportError, ModuleNotFoundError) as error:
            raise Qwen3TTSAdapterError(
                f"Qwen3-TTS の必須依存を import できません: {error}",
            ) from error

        if not self.torch.cuda.is_available():
            raise Qwen3TTSAdapterError("CUDA:0 を利用できません。")
        if self.torch.cuda.device_count() < 1:
            raise Qwen3TTSAdapterError("CUDA:0 が存在しません。")
        capability = self.torch.cuda.get_device_capability(0)
        if capability[0] < 8 or not self.torch.cuda.is_bf16_supported():
            raise Qwen3TTSAdapterError(
                "CUDA:0 は native BF16 をサポートしていません。",
            )
        functional = self.torch.nn.functional
        if not hasattr(functional, "scaled_dot_product_attention"):
            raise Qwen3TTSAdapterError("PyTorch SDPA backend を利用できません。")

        try:
            self._snapshot_download = huggingface_hub.snapshot_download
            self._model_class = qwen_tts.Qwen3TTSModel
        except AttributeError as error:
            raise Qwen3TTSAdapterError(
                f"Qwen3-TTS の必須 API がありません: {error}",
            ) from error

    def snapshot_download(self, repo_id: str, revision: str) -> Path:
        snapshot_path = Path(
            self._snapshot_download(
                repo_id=repo_id,
                revision=revision,
            ),
        )
        if not snapshot_path.is_dir():
            raise Qwen3TTSAdapterError(
                f"snapshot_download の結果が directory ではありません: {snapshot_path}",
            )
        return snapshot_path

    def load_model(self, snapshot_path: Path) -> Any:
        return self._model_class.from_pretrained(
            str(snapshot_path),
            device_map=DEVICE,
            dtype=self.torch.bfloat16,
            attn_implementation=ATTENTION_BACKEND,
        )

    def generate_voice_design(
        self,
        model: Any,
        *,
        text: str,
        language: str,
        instruct: str,
        sampling: Mapping[str, int | float | bool],
    ) -> tuple[Sequence[Any], int]:
        return model.generate_voice_design(
            text=text,
            language=language,
            instruct=instruct,
            **sampling,
        )

    def create_voice_clone_prompt(
        self,
        model: Any,
        *,
        ref_audio: str,
        ref_text: str,
    ) -> Any:
        return model.create_voice_clone_prompt(
            ref_audio=ref_audio,
            ref_text=ref_text,
            x_vector_only_mode=False,
        )

    def generate_voice_clone(
        self,
        model: Any,
        *,
        text: str,
        language: str,
        voice_clone_prompt: Any,
        sampling: Mapping[str, int | float | bool],
    ) -> tuple[Sequence[Any], int]:
        return model.generate_voice_clone(
            text=text,
            language=language,
            voice_clone_prompt=voice_clone_prompt,
            **sampling,
        )

    def write_pcm16(self, path: Path, samples: Any, sample_rate: int) -> None:
        self.soundfile.write(
            str(path),
            samples,
            samplerate=sample_rate,
            format="WAV",
            subtype="PCM_16",
        )

    def seed(self, seed: int) -> None:
        self.torch.manual_seed(seed)
        self.torch.cuda.manual_seed_all(seed)

    def reset_peak_memory_stats(self) -> None:
        self.torch.cuda.synchronize(0)
        self.torch.cuda.reset_peak_memory_stats(0)

    def peak_memory_mib(self) -> dict[str, float]:
        self.torch.cuda.synchronize(0)
        return {
            "allocated_mib": round(
                self.torch.cuda.max_memory_allocated(0) / _MIB,
                3,
            ),
            "reserved_mib": round(
                self.torch.cuda.max_memory_reserved(0) / _MIB,
                3,
            ),
        }

    def release_model(self) -> None:
        gc.collect()
        self.torch.cuda.empty_cache()

    def is_out_of_memory(self, error: BaseException) -> bool:
        return isinstance(error, self.torch.OutOfMemoryError)


@dataclass(frozen=True)
class _VoiceReference:
    wav_path: Path
    sha256: str
    text: str
    control: str
    source_id: str
    character_identity: Mapping[str, Any]
    phase_peak_vram_mib: dict[str, dict[str, float]]
    selected_anchor_receipt: Mapping[str, Any] | None

    def receipt(self) -> dict[str, Any]:
        receipt = {
            "character_identity": dict(self.character_identity),
            "reference_control": self.control,
            "reference_source_id": self.source_id,
            "reference_sha256": self.sha256,
            "reference_text": self.text,
        }
        if self.selected_anchor_receipt is not None:
            receipt["selected_anchor"] = dict(self.selected_anchor_receipt)
        return receipt


_T = TypeVar("_T")


class Qwen3TTSAdapter:
    profile = ModelProfile(
        id=MODEL_ID,
        name="Qwen3-TTS 12Hz 1.7B",
        version=PROFILE_VERSION,
        license_note=(
            "Apache-2.0（Qwen3-TTS code / Base / VoiceDesign）。"
            "明示参照音声またはキャラクター単位の VoiceDesign anchor を Base で clone する"
        ),
        capabilities=Capabilities(
            emotion=False,
            voice_prompt=True,
            clone=True,
            nonverbal=False,
            reading=False,
        ),
    )

    def take_recipe(self) -> TakeRecipe:
        return TakeRecipe(
            version="seed-only-v1",
            seed_policy="derived-sha256-v1",
            single_take_seed=SEED,
            seed_range=(0, 2**32 - 1),
            sampling=tuple(sorted(_sampling().items())),
            supports_multiple=True,
        )

    def __init__(
        self,
        runtime: _Runtime | None = None,
        *,
        role_anchor_selection_path: Path | None = None,
        role_anchor_plan_sha256: str | None = None,
        conditioning_mode: str | None = None,
    ) -> None:
        if (role_anchor_selection_path is None) != (
            role_anchor_plan_sha256 is None
        ):
            raise Qwen3TTSAdapterError(
                "role anchor selection pathとfrozen plan SHAは同時に指定してください。",
            )
        if (
            role_anchor_selection_path is not None
            and not role_anchor_selection_path.is_absolute()
        ):
            raise Qwen3TTSAdapterError(
                "role anchor selectionは絶対pathが必要です。",
            )
        if (
            role_anchor_plan_sha256 is not None
            and re.fullmatch(r"[0-9a-f]{64}", role_anchor_plan_sha256) is None
        ):
            raise Qwen3TTSAdapterError(
                "role anchor frozen plan SHAは完全な小文字SHA-256が必要です。",
            )
        self._runtime = _NativeRuntime() if runtime is None else runtime
        self._role_anchor_selection_path = role_anchor_selection_path
        self._role_anchor_plan_sha256 = role_anchor_plan_sha256
        self._conditioning_mode = normalize_conditioning_mode(conditioning_mode)
        self.profile = variant_profile(type(self).profile, self._conditioning_mode)
        self._references: dict[_ReferenceKey, _VoiceReference] = {}
        self._clone_prompts: dict[_ReferenceKey, Any] = {}
        self._clone_prompt_peaks: dict[_ReferenceKey, dict[str, float]] = {}
        self._base_model: Any | None = None
        self._base_load_peak: dict[str, float] | None = None
        self._anchor_model: Any | None = None
        self._anchor_load_peak: dict[str, float] | None = None
        self._prepared = False

    def prepare(
        self,
        jobs: Sequence[LineJob],
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        self.close_role_anchor_generation()
        if self._base_model is not None:
            self._base_model = None
            self._base_load_peak = None
            self._runtime.release_model()
        self._prepared = False
        self._references.clear()
        self._clone_prompts.clear()
        self._clone_prompt_peaks.clear()

        grouped_jobs: dict[_ReferenceKey, list[LineJob]] = {}
        for job in jobs:
            key = _job_key(job)
            grouped_jobs.setdefault(key, []).append(job)

        identities: dict[_ReferenceKey, dict[str, Any]] = {}
        reference_voices: dict[_ReferenceKey, str | None] = {}
        roles: dict[_ReferenceKey, RoleSnapshot] = {}
        for key, character_jobs in grouped_jobs.items():
            identity = _character_identity(character_jobs[0])
            if any(
                _character_identity(job) != identity for job in character_jobs[1:]
            ):
                raise Qwen3TTSAdapterError(
                    "同じ scenario/character に異なる character 入力があります: "
                    f"{_format_reference_key(key)}",
                )
            identities[key] = identity
            roles[key] = _role_snapshot(character_jobs[0])
            if any(
                _role_snapshot(job) != roles[key]
                for job in character_jobs[1:]
            ):
                raise Qwen3TTSAdapterError(
                    "同じscenario/characterに異なるrole snapshotがあります: "
                    f"{_format_reference_key(key)}",
                )
            reference_voice = _reference_voice_value(character_jobs[0])
            if any(
                _reference_voice_value(job) != reference_voice
                for job in character_jobs[1:]
            ):
                raise Qwen3TTSAdapterError(
                    "同じ scenario/character に異なる reference_voice があります: "
                    f"{_format_reference_key(key)}",
                )
            reference_voices[key] = reference_voice
        del artifacts_dir

        effective_voices = {
            key: effective_reference_voice(
                mode=self._conditioning_mode,
                scenario=key[0],
                character=key[1],
                explicit=explicit,
            )
            for key, explicit in reference_voices.items()
        }
        explicit_entries = (
            _load_reference_entries(voices_dir)
            if any(value is not None for value in effective_voices.values())
            else {}
        )
        for key in sorted(identities):
            identity = identities[key]
            reference_voice = effective_voices[key]
            if reference_voice is not None:
                self._references[key] = _explicit_reference(
                    voice_id=reference_voice,
                    voices_dir=voices_dir,
                    entries=explicit_entries,
                    character_identity=identity,
                )
                continue
            if (
                self._role_anchor_selection_path is None
                or self._role_anchor_plan_sha256 is None
            ):
                raise Qwen3TTSAdapterError(
                    "reference_voice=nullのPhase B準備には"
                    "role anchor selectionの絶対pathとfrozen plan SHAが必要です。",
                )
            from gaya_pipeline.increment_anchor import (
                IncrementAnchorError,
                resolve_increment_anchor,
            )

            try:
                selected = resolve_increment_anchor(
                    selection_path=self._role_anchor_selection_path,
                    plan_sha256=self._role_anchor_plan_sha256,
                    model=self.profile.id,
                    model_revision=PROFILE_VERSION,
                    role=roles[key],
                )
            except (CompletionAnchorError, IncrementAnchorError) as error:
                raise Qwen3TTSAdapterError(
                    f"selected role anchorが不正です: {error}",
                ) from error
            if selected.anchor_text != REFERENCE_TEXT:
                raise Qwen3TTSAdapterError(
                    "selected role anchor textがQwen固定値と一致しません。",
                )
            self._references[key] = _VoiceReference(
                wav_path=selected.audio_path,
                sha256=selected.audio_sha256,
                text=selected.anchor_text,
                control=_SELECTED_ANCHOR_CONTROL,
                source_id=selected.anchor_id,
                character_identity=identity,
                phase_peak_vram_mib={},
                selected_anchor_receipt=selected.receipt(),
            )

        self._prepared = True

    def role_anchor_generation_input(
        self,
        role: RoleSnapshot,
        *,
        role_scope: str = ROLE_SCOPE_NO_REFERENCE,
    ) -> Mapping[str, Any]:
        _validate_anchor_role(role, role_scope=role_scope)
        return {
            "model": VOICE_DESIGN_MODEL_ID,
            "revision": VOICE_DESIGN_REVISION,
            "language": LANGUAGE,
            "text": REFERENCE_TEXT,
            "instruct": _voice_design_instruct(_identity_from_role(role)),
            "sampling": _sampling(),
        }

    def generate_role_anchor(
        self,
        role: RoleSnapshot,
        *,
        seed: int,
        output_wav: Path,
        role_scope: str = ROLE_SCOPE_NO_REFERENCE,
    ) -> Mapping[str, Any]:
        generation_input = self.role_anchor_generation_input(
            role,
            role_scope=role_scope,
        )
        if self._anchor_model is None:
            if self._base_model is not None:
                raise Qwen3TTSAdapterError(
                    "Base generation中にVoiceDesign anchorは生成できません。",
                )
            snapshot = self._download_snapshot(
                VOICE_DESIGN_MODEL_ID,
                VOICE_DESIGN_REVISION,
            )
            self._runtime.reset_peak_memory_stats()
            self._anchor_model = self._run_phase(
                "VoiceDesign model load",
                lambda: self._runtime.load_model(snapshot),
            )
            self._anchor_load_peak = self._runtime.peak_memory_mib()
        self._runtime.seed(seed)
        self._runtime.reset_peak_memory_stats()
        generated = self._run_phase(
            f"VoiceDesign anchor generation ({role.scenario}/{role.character})",
            lambda: self._runtime.generate_voice_design(
                self._anchor_model,
                text=REFERENCE_TEXT,
                language=LANGUAGE,
                instruct=str(generation_input["instruct"]),
                sampling=_sampling(),
            ),
        )
        samples, sample_rate = _single_audio(generated, "VoiceDesign generation")
        generation_peak = self._runtime.peak_memory_mib()
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        self._runtime.write_pcm16(output_wav, samples, sample_rate)
        if not output_wav.is_file():
            raise Qwen3TTSAdapterError(
                f"VoiceDesign anchor WAVが書き込まれませんでした: {output_wav}",
            )
        if self._anchor_load_peak is None:
            raise Qwen3TTSAdapterError("VoiceDesign load profileがありません。")
        return {
            "seed": seed,
            "sample_rate_hz": sample_rate,
            "phase_peak_vram_mib": {
                "voice_design_load": _copy_peak(self._anchor_load_peak),
                "voice_design_generate": _copy_peak(generation_peak),
            },
            "role_identity_sha256": role.role_identity_sha256,
        }

    def close_role_anchor_generation(self) -> None:
        if self._anchor_model is None:
            self._anchor_load_peak = None
            return
        self._anchor_model = None
        self._anchor_load_peak = None
        self._runtime.release_model()

    def generation_params(self) -> Mapping[str, Any]:
        return {
            "qwen_tts_version": QWEN_TTS_VERSION,
            "base_model": BASE_MODEL_ID,
            "base_revision": BASE_REVISION,
            "voice_design_model": VOICE_DESIGN_MODEL_ID,
            "voice_design_revision": VOICE_DESIGN_REVISION,
            "device": DEVICE,
            "dtype": DTYPE,
            "attention_backend": ATTENTION_BACKEND,
            "sampling": _sampling(),
            "reference_key": ["scenario", "character"],
            "reference_controls": {
                "explicit_reference": _ASSET_REFERENCE_CONTROL,
                "selected_anchor": _SELECTED_ANCHOR_CONTROL,
            },
            "voice_design_anchor_text": REFERENCE_TEXT,
            "role_anchor_selection_protocol": "role-anchor-selection-v1",
            "role_anchor_selection_required_for_null_reference": True,
            "character_identity_fields": [
                "scenario",
                "character",
                "name",
                "kind",
                "gender",
                "age",
                "archetype",
                "voice",
                "personality",
                "scene_setting",
            ],
            "gender_labels": dict(_GENDER_LABELS),
            "age_labels": dict(_AGE_LABELS),
            "kind_labels": dict(_KIND_LABELS),
        }

    def generation_input(
        self,
        job: LineJob,
        take_context: TakeContext,
    ) -> Mapping[str, Any]:
        require_take_context(take_context, self.take_recipe())
        reference = self._reference_for(job)
        text = _required_string(job.line, "text", "line")
        return {
            "text": text,
            "language": LANGUAGE,
            **reference.receipt(),
        }

    def generate(
        self,
        job: LineJob,
        take_context: TakeContext,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        require_take_context(take_context, self.take_recipe())
        seed = take_context.seed
        assert seed is not None
        reference = self._reference_for(job)
        key = _job_key(job)
        model = self._ensure_base_model()
        prompt = self._clone_prompts.get(key)
        if prompt is None:
            _verify_reference_unchanged(reference)
            self._runtime.reset_peak_memory_stats()
            prompt = self._run_phase(
                f"Base clone prompt ({_format_reference_key(key)})",
                lambda: self._runtime.create_voice_clone_prompt(
                    model,
                    ref_audio=str(reference.wav_path),
                    ref_text=reference.text,
                ),
            )
            prompt_peak = self._runtime.peak_memory_mib()
            self._clone_prompts[key] = prompt
            self._clone_prompt_peaks[key] = _copy_peak(prompt_peak)
        else:
            try:
                prompt_peak = self._clone_prompt_peaks[key]
            except KeyError as error:
                raise Qwen3TTSAdapterError(
                    "Base clone prompt の VRAM profile がありません: "
                    f"{_format_reference_key(key)}",
                ) from error

        text = _required_string(job.line, "text", "line")
        self._runtime.seed(seed)
        self._runtime.reset_peak_memory_stats()
        generated = self._run_phase(
            f"Base voice clone generation ({key[0]}/{job.line_id})",
            lambda: self._runtime.generate_voice_clone(
                model,
                text=text,
                language=LANGUAGE,
                voice_clone_prompt=prompt,
                sampling=_sampling(),
            ),
        )
        samples, sample_rate = _single_audio(generated, "Base voice clone generation")
        generation_peak = self._runtime.peak_memory_mib()

        output_wav.parent.mkdir(parents=True, exist_ok=True)
        self._runtime.write_pcm16(output_wav, samples, sample_rate)
        if not output_wav.is_file():
            raise Qwen3TTSAdapterError(
                f"PCM WAV が書き込まれませんでした: {output_wav}",
            )

        if self._base_load_peak is None:
            raise Qwen3TTSAdapterError("Base model load profile がありません。")
        return {
            "phase_peak_vram_mib": {
                **{
                    phase: _copy_peak(peak)
                    for phase, peak in reference.phase_peak_vram_mib.items()
                },
                "base_load": _copy_peak(self._base_load_peak),
                "voice_clone_prompt_create": _copy_peak(prompt_peak),
                "voice_clone_generate": _copy_peak(generation_peak),
            },
            "seed": seed,
            "sampling": take_context.sampling_dict(),
            "sample_rate_hz": sample_rate,
            **reference.receipt(),
        }

    def _reference_for(self, job: LineJob) -> _VoiceReference:
        if not self._prepared:
            raise Qwen3TTSAdapterError("prepare() が完了していません。")
        key = _job_key(job)
        try:
            reference = self._references[key]
        except KeyError as error:
            raise Qwen3TTSAdapterError(
                f"character reference がありません: {_format_reference_key(key)}",
            ) from error
        if reference.character_identity != _character_identity(job):
            raise Qwen3TTSAdapterError(
                "prepare 済み character identity が一致しません: "
                f"{_format_reference_key(key)}",
            )
        return reference

    def _ensure_base_model(self) -> Any:
        if self._base_model is not None:
            return self._base_model
        snapshot = self._download_snapshot(BASE_MODEL_ID, BASE_REVISION)
        self._runtime.reset_peak_memory_stats()
        self._base_model = self._run_phase(
            "Base model load",
            lambda: self._runtime.load_model(snapshot),
        )
        self._base_load_peak = self._runtime.peak_memory_mib()
        return self._base_model

    def _download_snapshot(self, repo_id: str, revision: str) -> Path:
        try:
            return self._runtime.snapshot_download(repo_id, revision)
        except Exception as error:
            raise Qwen3TTSAdapterError(
                f"固定 revision snapshot の取得に失敗しました: "
                f"{repo_id}@{revision}: {error}",
            ) from error

    def _run_phase(self, phase: str, action: Callable[[], _T]) -> _T:
        try:
            return action()
        except Exception as error:
            if self._runtime.is_out_of_memory(error):
                raise Qwen3TTSAdapterError(
                    f"{phase} で CUDA out of memory が発生しました。",
                ) from error
            raise Qwen3TTSAdapterError(f"{phase} に失敗しました: {error}") from error

def _sampling() -> dict[str, int | float | bool]:
    return dict(_SAMPLING)


def _job_key(job: LineJob) -> _ReferenceKey:
    if job.locale != "ja":
        raise Qwen3TTSAdapterError(
            f"Qwen3-TTS adapter の language は Japanese 固定です: {job.locale}",
        )
    scenario_id = _required_identifier(job.scene, "id", "scene")
    character_id = _required_identifier(job.character, "id", "character")
    return scenario_id, character_id


def _character_identity(job: LineJob) -> dict[str, Any]:
    scenario_id, character_id = _job_key(job)
    name = _required_string(job.character, "name", "character")
    kind = _character_kind(job.character)
    gender = _enum_label(
        job.character,
        "gender",
        "character",
        _GENDER_LABELS,
    )
    age = _enum_label(job.character, "age", "character", _AGE_LABELS)
    archetype = _required_string(job.character, "archetype", "character")
    voice = _required_string(job.character, "voice", "character")
    personality = _required_string(job.character, "personality", "character")
    setting = _required_string(job.scene, "setting", "scene")
    return {
        "scenario": scenario_id,
        "character": character_id,
        "name": name,
        "kind": kind,
        "gender": gender,
        "age": age,
        "archetype": archetype,
        "voice": voice,
        "personality": personality,
        "scene_setting": setting,
    }


def _role_snapshot(job: LineJob) -> RoleSnapshot:
    scenario_id, character_id = _job_key(job)
    try:
        return build_role_snapshot(
            scenario=scenario_id,
            character=character_id,
            character_document=job.character,
            scene_setting=_required_string(job.scene, "setting", "scene"),
        )
    except CompletionPlanError as error:
        raise Qwen3TTSAdapterError(f"role snapshotが不正です: {error}") from error


def _identity_from_role(role: RoleSnapshot) -> dict[str, Any]:
    return {
        "scenario": role.scenario,
        "character": role.character,
        **dict(role.role),
        "scene_setting": role.scene_setting,
    }


def _validate_anchor_role(
    role: RoleSnapshot,
    *,
    role_scope: str = ROLE_SCOPE_NO_REFERENCE,
) -> None:
    # 既定scopeでは凍結契約どおり明示reference役を拒否する。`--text` バリアント用の
    # explicit-reference scope では明示referenceを無視して anchor を作る (#201)。
    if role.reference_voice is not None and not anchor_scope_allows_explicit_reference(
        role_scope,
    ):
        raise Qwen3TTSAdapterError(
            "明示reference roleはVoiceDesign anchor対象にできません。",
        )
    identity = _identity_from_role(role)
    for key, labels in (
        ("kind", _KIND_LABELS),
        ("gender", _GENDER_LABELS),
        ("age", _AGE_LABELS),
    ):
        if identity[key] not in labels:
            raise Qwen3TTSAdapterError(
                f"anchor roleの{key}が未対応です: {identity[key]}",
            )


def _voice_design_instruct(character_identity: Mapping[str, Any]) -> str:
    gender = str(character_identity["gender"])
    age = str(character_identity["age"])
    kind = str(character_identity["kind"])
    return "\n".join(
        (
            "以下の架空キャラクター専用の、一貫した話者 anchor を作成する。",
            "[キャラクター情報]",
            f"名前: {character_identity['name']}",
            f"種別: {_KIND_LABELS[kind]} ({kind})",
            f"性別: {_GENDER_LABELS[gender]} ({gender})",
            f"年齢: {_AGE_LABELS[age]} ({age})",
            f"役柄: {character_identity['archetype']}",
            f"声質: {character_identity['voice']}",
            f"性格: {character_identity['personality']}",
            f"場面: {character_identity['scene_setting']}",
            "[発声条件]",
            "感情や演技を強調せず、自然で落ち着いた中立の発声にする。",
            "指定した性別と年齢から絶対に逸脱しない。"
            "声質、年齢感、話者としての同一性を一貫させる。",
            "実在の人物や声優を模倣しない。",
        ),
    )


def _character_kind(character: Mapping[str, Any]) -> str:
    if "kind" not in character:
        return "human"
    return _enum_label(character, "kind", "character", _KIND_LABELS)


def _enum_label(
    value: Mapping[str, Any],
    key: str,
    owner: str,
    labels: Mapping[str, str],
) -> str:
    item = _required_string(value, key, owner)
    try:
        labels[item]
    except KeyError as error:
        raise Qwen3TTSAdapterError(
            f"未対応の {owner}.{key} です: {item}",
        ) from error
    return item


def _reference_voice_value(job: LineJob) -> str | None:
    if "reference_voice" not in job.character:
        raise Qwen3TTSAdapterError(
            "character.reference_voice は string または null が必要です。",
        )
    value = job.character["reference_voice"]
    if value is None:
        return None
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise Qwen3TTSAdapterError(
            "character.reference_voice は identifier または null が必要です。",
        )
    return value


def _format_reference_key(key: _ReferenceKey) -> str:
    return f"{key[0]}/{key[1]}"


def _load_reference_entries(voices_dir: Path) -> dict[str, Mapping[str, Any]]:
    voices_dir = voices_dir.resolve()
    validation = validate_voice_metadata(voices_dir)
    if validation.problems:
        details = "; ".join(str(problem) for problem in validation.problems)
        raise Qwen3TTSAdapterError(
            f"参照音声 metadata の検証に失敗しました: {details}",
        )
    metadata_path = voices_dir / "metadata.yaml"
    try:
        document = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise Qwen3TTSAdapterError(
            f"参照音声 metadata を読み込めません: {metadata_path}: {error}",
        ) from error
    if not isinstance(document, Mapping):
        raise Qwen3TTSAdapterError(
            f"参照音声 metadata は object が必要です: {metadata_path}",
        )
    voices = document["voices"]
    if not isinstance(voices, list):
        raise Qwen3TTSAdapterError(
            f"参照音声 metadata.voices は array が必要です: {metadata_path}",
        )
    return {str(entry["id"]): entry for entry in voices}


def _explicit_reference(
    *,
    voice_id: str,
    voices_dir: Path,
    entries: Mapping[str, Mapping[str, Any]],
    character_identity: Mapping[str, Any],
) -> _VoiceReference:
    try:
        entry = entries[voice_id]
    except KeyError as error:
        raise Qwen3TTSAdapterError(
            f"reference_voice が metadata にありません: {voice_id}",
        ) from error
    expected_file = f"{voice_id}/reference.wav"
    if entry["file"] != expected_file:
        raise Qwen3TTSAdapterError(
            f"参照音声 file が一致しません: expected={expected_file}",
        )
    if entry["language"] != "ja":
        raise Qwen3TTSAdapterError(
            f"参照音声 language は ja が必要です: {voice_id}",
        )
    transcript = entry["transcript"]
    if not isinstance(transcript, str) or not transcript:
        raise Qwen3TTSAdapterError(
            f"参照音声 transcript は non-empty string が必要です: {voice_id}",
        )
    expected_sha256 = entry["sha256"]
    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(
        expected_sha256,
    ) is None:
        raise Qwen3TTSAdapterError(
            f"参照音声 sha256 が不正です: {voice_id}",
        )

    voices_root = voices_dir.resolve()
    wav_path = voices_root / expected_file
    resolved_wav = wav_path.resolve()
    if (
        wav_path.is_symlink()
        or not resolved_wav.is_relative_to(voices_root)
        or not wav_path.is_file()
    ):
        raise Qwen3TTSAdapterError(
            f"参照音声 WAV は voices 内の通常ファイルが必要です: {wav_path}",
        )
    actual_sha256 = _sha256_file(wav_path)
    if actual_sha256 != expected_sha256:
        raise Qwen3TTSAdapterError(
            "参照音声 WAV SHA-256 が一致しません: "
            f"{voice_id}: expected={expected_sha256}, actual={actual_sha256}",
        )
    return _VoiceReference(
        wav_path=wav_path,
        sha256=actual_sha256,
        text=transcript,
        control=_ASSET_REFERENCE_CONTROL,
        source_id=voice_id,
        character_identity=dict(character_identity),
        phase_peak_vram_mib={},
        selected_anchor_receipt=None,
    )


def _valid_peak(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _PEAK_KEYS:
        return False
    return all(
        isinstance(measurement, (int, float))
        and not isinstance(measurement, bool)
        and measurement >= 0
        for measurement in value.values()
    )


def _copy_peak(peak: Mapping[str, int | float]) -> dict[str, float]:
    if not _valid_peak(peak):
        raise Qwen3TTSAdapterError(f"不正な CUDA peak profile です: {peak}")
    return {
        "allocated_mib": float(peak["allocated_mib"]),
        "reserved_mib": float(peak["reserved_mib"]),
    }


def _single_audio(
    generated: tuple[Sequence[Any], int],
    phase: str,
) -> tuple[Any, int]:
    if not isinstance(generated, tuple) or len(generated) != 2:
        raise Qwen3TTSAdapterError(f"{phase} の戻り値が不正です。")
    waveforms, sample_rate = generated
    if not hasattr(waveforms, "__len__") or len(waveforms) != 1:
        raise Qwen3TTSAdapterError(f"{phase} は waveform を1件返す必要があります。")
    if (
        not isinstance(sample_rate, int)
        or isinstance(sample_rate, bool)
        or sample_rate <= 0
    ):
        raise Qwen3TTSAdapterError(f"{phase} の sample rate が不正です。")
    return waveforms[0], sample_rate


def _required_string(
    value: Mapping[str, Any],
    key: str,
    owner: str,
) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise Qwen3TTSAdapterError(f"{owner}.{key} は non-empty string が必要です。")
    return item


def _required_identifier(
    value: Mapping[str, Any],
    key: str,
    owner: str,
) -> str:
    identifier = _required_string(value, key, owner)
    if _IDENTIFIER.fullmatch(identifier) is None:
        raise Qwen3TTSAdapterError(
            f"{owner}.{key} が identifier 形式ではありません: {identifier}",
        )
    return identifier


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_reference_unchanged(reference: _VoiceReference) -> None:
    path = reference.wav_path
    if path.is_symlink() or not path.is_file():
        raise Qwen3TTSAdapterError(
            f"参照音声 WAV が通常ファイルではありません: {path}",
        )
    try:
        actual_sha256 = _sha256_file(path)
    except OSError as error:
        raise Qwen3TTSAdapterError(
            f"参照音声 WAV を再検証できません: {path}: {error}",
        ) from error
    if actual_sha256 != reference.sha256:
        raise Qwen3TTSAdapterError(
            "参照音声 WAV がprepare後に変更されました: "
            f"expected={reference.sha256}, actual={actual_sha256}",
        )
