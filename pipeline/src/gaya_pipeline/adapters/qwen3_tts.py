from __future__ import annotations

import gc
import hashlib
import importlib
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol, TypeVar

from gaya_pipeline.adapters.base import (
    Capabilities,
    LineJob,
    ModelProfile,
    TakeContext,
    TakeRecipe,
    require_take_context,
)

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
REFERENCE_TEXT_BY_EMOTION = {
    "neutral": "さて、今日も一日を始めましょう。",
    "cheerful": "やった！こんなに嬉しいことがあるなんて！",
    "angry": "いい加減にしろ！もう絶対に許さないぞ！",
    "sad": "もう会えないなんて、本当に寂しいよ……。",
    "fearful": "待って、何かいる……こっちに来ないで！",
    "surprised": "えっ、本当に？そんなことがあるなんて！",
    "tired": "はあ……今日はもう、すっかり疲れたよ。",
    "drunk": "へへっ、今夜はまだまだ飲めるぞぉ。",
    "whisper": "静かに……誰かに聞かれたら困るから。",
    "shout": "みんな、急げ！今すぐここから逃げろ！",
    "laughing": "はははっ、こんなに笑ったのは久しぶりだ！",
    "pain": "うっ……痛い、でもまだ動ける……。",
}
EMOTION_INSTRUCTION = {
    "neutral": "自然で落ち着いた中立の感情",
    "cheerful": "明るく弾むような喜び",
    "angry": "抑えきれない怒り",
    "sad": "深い悲しみと寂しさ",
    "fearful": "切迫した恐怖",
    "surprised": "思わず声が跳ねる驚き",
    "tired": "疲労で力の抜けた様子",
    "drunk": "酔いが回って舌が少し緩んだ様子",
    "whisper": "息を混ぜた小さな囁き",
    "shout": "遠くへ届く切迫した叫び",
    "laughing": "自然な笑いを含む楽しさ",
    "pain": "痛みに耐えながら絞り出す声",
}
DELIVERY_INSTRUCTION = {
    "neutral": "自然な間を取り、平静な口調で話す。",
    "cheerful": "声を弾ませ、明るいテンポで話す。",
    "angry": "語気を強め、短く鋭く言い切る。",
    "sad": "息を多めに含み、間を置きながら沈んだ調子で話す。",
    "fearful": "息を詰め、声を震わせながら切迫して話す。",
    "surprised": "冒頭で息をのみ、声を大きく跳ね上げる。",
    "tired": "力を抜き、息を漏らしながらゆっくり話す。",
    "drunk": "言葉を少しもつれさせ、揺れるテンポで話す。",
    "whisper": "声量を抑え、耳元で囁くように話す。",
    "shout": "腹から声を出し、遠くへ強く呼びかける。",
    "laughing": "言葉の合間に自然な笑いを混ぜる。",
    "pain": "息を詰まらせ、痛みに耐えながら絞り出す。",
}
INTENSITY_INSTRUCTION = {
    1: "弱め。声質を崩さず控えめに表す",
    2: "中程度。台詞の意図が明確に伝わる強さで表す",
    3: "強め。声質を保ったまま感情をはっきり表す",
}

_CACHE_FORMAT_VERSION = 2
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]*$")
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
_CACHE_PEAK_KEYS = {"voice_design_load", "voice_design_generate"}
_REFERENCE_CONTROL = "voice_design_emotion_bank"
_ReferenceKey = tuple[str, str, str, int]


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
    phase_peak_vram_mib: dict[str, dict[str, float]]


_T = TypeVar("_T")


class Qwen3TTSAdapter:
    profile = ModelProfile(
        id=MODEL_ID,
        name="Qwen3-TTS 12Hz 1.7B",
        version=PROFILE_VERSION,
        license_note=(
            "Apache-2.0（Qwen3-TTS code / Base / VoiceDesign）。"
            "VoiceDesign 感情参照 bank は A/B 検証前の実験経路"
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
            version="fixed-single-v1",
            seed_policy="fixed",
            single_take_seed=SEED,
            seed_range=(0, 2**32 - 1),
            sampling=tuple(sorted(_sampling().items())),
            supports_multiple=False,
        )

    def __init__(self, runtime: _Runtime | None = None) -> None:
        self._runtime = _NativeRuntime() if runtime is None else runtime
        self._references: dict[_ReferenceKey, _VoiceReference] = {}
        self._clone_prompts: dict[_ReferenceKey, Any] = {}
        self._base_model: Any | None = None
        self._base_load_peak: dict[str, float] | None = None
        self._prepared = False

    def prepare(
        self,
        jobs: Sequence[LineJob],
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        del voices_dir
        self._prepared = False
        self._references.clear()
        self._clone_prompts.clear()

        grouped_jobs: dict[_ReferenceKey, list[LineJob]] = {}
        for job in jobs:
            key = _job_key(job)
            grouped_jobs.setdefault(key, []).append(job)

        identities: dict[_ReferenceKey, dict[str, Any]] = {}
        paths: dict[_ReferenceKey, tuple[Path, Path]] = {}
        for key, bank_jobs in grouped_jobs.items():
            identity = _cache_identity(bank_jobs[0])
            if any(_cache_identity(job) != identity for job in bank_jobs[1:]):
                raise Qwen3TTSAdapterError(
                    "同じ感情参照 bank key に異なる VoiceDesign 入力があります: "
                    f"{_format_reference_key(key)}",
                )
            identities[key] = identity
            reference_dir = (
                artifacts_dir
                / "voices"
                / MODEL_ID
                / key[0]
                / key[1]
                / key[2]
                / f"intensity-{key[3]}"
            )
            paths[key] = (
                reference_dir / "reference.wav",
                reference_dir / "reference.json",
            )

        missing: list[_ReferenceKey] = []
        for key in sorted(identities):
            identity = identities[key]
            wav_path, metadata_path = paths[key]
            cached = _read_cached_reference(
                wav_path=wav_path,
                metadata_path=metadata_path,
                identity=identity,
            )
            if cached is None:
                missing.append(key)
            else:
                self._references[key] = cached

        if missing:
            snapshot = self._download_snapshot(
                VOICE_DESIGN_MODEL_ID,
                VOICE_DESIGN_REVISION,
            )
            self._runtime.reset_peak_memory_stats()
            voice_model = self._run_phase(
                "VoiceDesign model load",
                lambda: self._runtime.load_model(snapshot),
            )
            load_peak = self._runtime.peak_memory_mib()
            try:
                for key in missing:
                    identity = identities[key]
                    wav_path, metadata_path = paths[key]
                    self._runtime.seed(SEED)
                    self._runtime.reset_peak_memory_stats()
                    generated = self._run_phase(
                        f"VoiceDesign generation ({_format_reference_key(key)})",
                        lambda identity=identity: self._runtime.generate_voice_design(
                            voice_model,
                            text=str(identity["text"]),
                            language=LANGUAGE,
                            instruct=str(identity["instruct"]),
                            sampling=_sampling(),
                        ),
                    )
                    samples, sample_rate = _single_audio(
                        generated,
                        "VoiceDesign generation",
                    )
                    generation_peak = self._runtime.peak_memory_mib()
                    phase_peaks = {
                        "voice_design_load": _copy_peak(load_peak),
                        "voice_design_generate": _copy_peak(generation_peak),
                    }
                    reference = self._write_reference(
                        wav_path=wav_path,
                        metadata_path=metadata_path,
                        identity=identity,
                        samples=samples,
                        sample_rate=sample_rate,
                        phase_peaks=phase_peaks,
                    )
                    self._references[key] = reference
            finally:
                voice_model = None
                self._runtime.release_model()

        self._prepared = True

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
            "reference_control": _REFERENCE_CONTROL,
            "reference_key": [
                "scenario",
                "character",
                "emotion",
                "intensity",
            ],
            "reference_text_by_emotion": dict(REFERENCE_TEXT_BY_EMOTION),
            "emotion_instruction": dict(EMOTION_INSTRUCTION),
            "delivery_instruction": dict(DELIVERY_INSTRUCTION),
            "intensity_instruction": {
                str(intensity): instruction
                for intensity, instruction in INTENSITY_INSTRUCTION.items()
            },
        }

    def generation_input(
        self,
        job: LineJob,
        take_context: TakeContext,
    ) -> Mapping[str, Any]:
        require_take_context(take_context, self.take_recipe())
        reference = self._reference_for(job)
        text = _required_string(job.line, "text", "line")
        key = _job_key(job)
        reference_text = _reference_text(key[2])
        return {
            "text": text,
            "language": LANGUAGE,
            "emotion": key[2],
            "intensity": key[3],
            "reference_control": _REFERENCE_CONTROL,
            "reference_text": reference_text,
            "reference_sha256": reference.sha256,
        }

    def generate(
        self,
        job: LineJob,
        take_context: TakeContext,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        require_take_context(take_context, self.take_recipe())
        reference = self._reference_for(job)
        key = _job_key(job)
        model = self._ensure_base_model()
        prompt = self._clone_prompts.get(key)
        if prompt is None:
            reference_text = _reference_text(key[2])
            prompt = self._run_phase(
                f"Base clone prompt ({_format_reference_key(key)})",
                lambda: self._runtime.create_voice_clone_prompt(
                    model,
                    ref_audio=str(reference.wav_path),
                    ref_text=reference_text,
                ),
            )
            self._clone_prompts[key] = prompt

        text = _required_string(job.line, "text", "line")
        self._runtime.seed(SEED)
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
                "voice_clone_generate": _copy_peak(generation_peak),
            },
            "seed": SEED,
            "sample_rate_hz": sample_rate,
        }

    def _reference_for(self, job: LineJob) -> _VoiceReference:
        if not self._prepared:
            raise Qwen3TTSAdapterError("prepare() が完了していません。")
        key = _job_key(job)
        try:
            return self._references[key]
        except KeyError as error:
            raise Qwen3TTSAdapterError(
                f"VoiceDesign reference がありません: {_format_reference_key(key)}",
            ) from error

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

    def _write_reference(
        self,
        *,
        wav_path: Path,
        metadata_path: Path,
        identity: Mapping[str, Any],
        samples: Any,
        sample_rate: int,
        phase_peaks: dict[str, dict[str, float]],
    ) -> _VoiceReference:
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        pending_wav = wav_path.with_name(".reference.pending.wav")
        pending_metadata = metadata_path.with_name(".reference.pending.json")
        pending_wav.unlink(missing_ok=True)
        pending_metadata.unlink(missing_ok=True)
        try:
            self._runtime.write_pcm16(pending_wav, samples, sample_rate)
            if not pending_wav.is_file():
                raise Qwen3TTSAdapterError(
                    f"VoiceDesign PCM WAV が書き込まれませんでした: {pending_wav}",
                )
            wav_sha256 = _sha256_file(pending_wav)
            cache_metadata = {
                **identity,
                "phase_peak_vram_mib": phase_peaks,
                "wav_sha256": wav_sha256,
            }
            pending_metadata.write_text(
                json.dumps(cache_metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            pending_wav.replace(wav_path)
            pending_metadata.replace(metadata_path)
        finally:
            pending_wav.unlink(missing_ok=True)
            pending_metadata.unlink(missing_ok=True)

        return _VoiceReference(
            wav_path=wav_path,
            sha256=wav_sha256,
            phase_peak_vram_mib={
                phase: _copy_peak(peak) for phase, peak in phase_peaks.items()
            },
        )


def _sampling() -> dict[str, int | float | bool]:
    return dict(_SAMPLING)


def _job_key(job: LineJob) -> _ReferenceKey:
    if job.locale != "ja":
        raise Qwen3TTSAdapterError(
            f"Qwen3-TTS adapter の language は Japanese 固定です: {job.locale}",
        )
    scenario_id = _required_identifier(job.scene, "id", "scene")
    character_id = _required_identifier(job.character, "id", "character")
    emotion = _required_string(job.line, "emotion", "line")
    if emotion not in REFERENCE_TEXT_BY_EMOTION:
        raise Qwen3TTSAdapterError(f"未対応の line.emotion です: {emotion}")
    intensity = _required_intensity(job.line)
    return scenario_id, character_id, emotion, intensity


def _cache_identity(job: LineJob) -> dict[str, Any]:
    scenario_id, character_id, emotion, intensity = _job_key(job)
    voice = _required_string(job.character, "voice", "character")
    setting = _required_string(job.scene, "setting", "scene")
    instruction_parts = [f"声質: {voice}"]
    if "personality" in job.character:
        personality = job.character["personality"]
        if not isinstance(personality, str):
            raise Qwen3TTSAdapterError("character.personality は string が必要です。")
        instruction_parts.append(f"性格: {personality}")
    instruction_parts.extend(
        (
            f"場面: {setting}",
            f"感情: {EMOTION_INSTRUCTION[emotion]}",
            f"感情の強度: {INTENSITY_INSTRUCTION[intensity]}",
            f"演技: {DELIVERY_INSTRUCTION[emotion]}",
            "感情が変わっても、同じキャラクターの声質、年齢感、"
            "話者としての同一性を保つ。",
            "実在の人物や声優を模倣せず、この架空キャラクターの声として自然に発声する。",
        ),
    )
    return {
        "format_version": _CACHE_FORMAT_VERSION,
        "model": VOICE_DESIGN_MODEL_ID,
        "revision": VOICE_DESIGN_REVISION,
        "scenario": scenario_id,
        "character": character_id,
        "emotion": emotion,
        "intensity": intensity,
        "delivery": DELIVERY_INSTRUCTION[emotion],
        "language": LANGUAGE,
        "text": _reference_text(emotion),
        "instruct": "\n".join(instruction_parts),
        "seed": SEED,
        "sampling": _sampling(),
    }


def _reference_text(emotion: str) -> str:
    try:
        return REFERENCE_TEXT_BY_EMOTION[emotion]
    except KeyError as error:
        raise Qwen3TTSAdapterError(f"未対応の line.emotion です: {emotion}") from error


def _required_intensity(line: Mapping[str, Any]) -> int:
    intensity = line.get("intensity")
    if (
        not isinstance(intensity, int)
        or isinstance(intensity, bool)
        or intensity not in INTENSITY_INSTRUCTION
    ):
        raise Qwen3TTSAdapterError("line.intensity は 1 / 2 / 3 が必要です。")
    return intensity


def _format_reference_key(key: _ReferenceKey) -> str:
    return f"{key[0]}/{key[1]}/{key[2]}/intensity-{key[3]}"


def _read_cached_reference(
    *,
    wav_path: Path,
    metadata_path: Path,
    identity: Mapping[str, Any],
) -> _VoiceReference | None:
    wav_exists = wav_path.exists()
    metadata_exists = metadata_path.exists()
    if not wav_exists and not metadata_exists:
        return None
    if wav_exists != metadata_exists:
        raise Qwen3TTSAdapterError(
            f"VoiceDesign cache の WAV/metadata pair が壊れています: {wav_path.parent}",
        )
    if not wav_path.is_file() or not metadata_path.is_file():
        raise Qwen3TTSAdapterError(
            f"VoiceDesign cache が file ではありません: {wav_path.parent}",
        )
    try:
        cached = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise Qwen3TTSAdapterError(
            f"VoiceDesign cache metadata が不正な JSON です: {metadata_path}",
        ) from error
    if not isinstance(cached, dict):
        raise Qwen3TTSAdapterError(
            f"VoiceDesign cache metadata は object が必要です: {metadata_path}",
        )
    expected_keys = set(identity) | {"phase_peak_vram_mib", "wav_sha256"}
    if set(cached) != expected_keys:
        raise Qwen3TTSAdapterError(
            f"VoiceDesign cache metadata の項目が一致しません: {metadata_path}",
        )
    if any(cached[key] != value for key, value in identity.items()):
        raise Qwen3TTSAdapterError(
            f"VoiceDesign cache identity が一致しません: {metadata_path}",
        )
    peaks = cached["phase_peak_vram_mib"]
    if not _valid_cache_peaks(peaks):
        raise Qwen3TTSAdapterError(
            f"VoiceDesign cache の CUDA peak profile が不正です: {metadata_path}",
        )
    wav_sha256 = cached["wav_sha256"]
    if not isinstance(wav_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}",
        wav_sha256,
    ):
        raise Qwen3TTSAdapterError(
            f"VoiceDesign cache の wav_sha256 が不正です: {metadata_path}",
        )
    if _sha256_file(wav_path) != wav_sha256:
        raise Qwen3TTSAdapterError(
            f"VoiceDesign cache の WAV SHA-256 が一致しません: {wav_path}",
        )
    return _VoiceReference(
        wav_path=wav_path,
        sha256=wav_sha256,
        phase_peak_vram_mib={phase: _copy_peak(peak) for phase, peak in peaks.items()},
    )


def _valid_cache_peaks(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _CACHE_PEAK_KEYS:
        return False
    return all(_valid_peak(peak) for peak in value.values())


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
