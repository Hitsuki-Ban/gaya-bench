from __future__ import annotations

import hashlib
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import wave
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from gaya_pipeline.adapters.base import (
    Capabilities,
    LineJob,
    ModelProfile,
    TakeContext,
    TakeRecipe,
    require_take_context,
)

MODEL_ID = "aivisspeech-kohaku"
ENGINE_URL = "http://127.0.0.1:10101"
ENGINE_VERSION = "1.2.0"
ENGINE_MANIFEST_VERSION = "0.13.1"
ENGINE_MANIFEST_UUID = "1b4a5014-d9fd-11ee-b97d-83c170a68ed3"
ENGINE_MANIFEST_URL = "https://github.com/Aivis-Project/AivisSpeech-Engine"
MODEL_UUID = "22e8ed77-94fe-4ef2-871f-a86f94e9a579"
MODEL_VERSION = "1.1.0"
MODEL_NAME = "コハク"
MODEL_SHA256 = "3f5c08b52bb8a64efd361268580c81510f96c927cd6905aa7dbae6851333270a"
MODEL_FILE_SIZE = 255_326_987
SPEAKER_UUID = "5680ac39-43c9-487a-bc3e-018c0d29cc38"
SPEAKER_NAME = "コハク"
SAMPLE_RATE_HZ = 44_100
REQUEST_TIMEOUT_SEC = 120

STYLE_IDS = {
    "ノーマル": 1_878_365_376,
    "あまあま": 1_878_365_377,
    "せつなめ": 1_878_365_378,
    "ねむたい": 1_878_365_379,
}
STYLE_LOCAL_IDS = {
    "ノーマル": 0,
    "あまあま": 1,
    "せつなめ": 2,
    "ねむたい": 3,
}
STYLE_BY_EMOTION = {
    "neutral": "ノーマル",
    "cheerful": "あまあま",
    "angry": "ノーマル",
    "sad": "せつなめ",
    "fearful": "せつなめ",
    "surprised": "ノーマル",
    "tired": "ねむたい",
    "drunk": "ねむたい",
    "whisper": "ねむたい",
    "shout": "ノーマル",
    "laughing": "あまあま",
    "pain": "せつなめ",
}
INTONATION_SCALE_BY_INTENSITY = {1: 0.8, 2: 1.0, 3: 1.2}
TEMPO_DYNAMICS_SCALE_BY_INTENSITY = {1: 0.8, 2: 1.0, 3: 1.2}

PROFILE_VERSION = (
    f"AivisSpeech Engine {ENGINE_VERSION}; "
    f"{MODEL_NAME} AIVMX {MODEL_VERSION}@sha256:{MODEL_SHA256}"
)


class AivisSpeechAdapterError(RuntimeError):
    pass


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request:
        del request, file_pointer, message, headers
        raise AivisSpeechAdapterError(
            "AivisSpeech Engine が HTTP redirect を返しました: "
            f"status={code}, location={new_url!r}",
        )


def _loopback_opener() -> Callable[..., Any]:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirectHandler(),
    ).open


@dataclass(frozen=True)
class _PreparedInput:
    text: str
    reading: str | None
    reading_source: str
    emotion: str
    intensity: int
    style_name: str
    style_id: int
    intonation_scale: float
    tempo_dynamics_scale: float

    def as_generation_input(self) -> dict[str, Any]:
        result = {
            "text": self.text,
            "reading_source": self.reading_source,
            "model_uuid": MODEL_UUID,
            "speaker_uuid": SPEAKER_UUID,
            "speaker_style": {
                "name": self.style_name,
                "id": self.style_id,
            },
            "emotion": self.emotion,
            "intensity": self.intensity,
            "intonation_scale": self.intonation_scale,
            "tempo_dynamics_scale": self.tempo_dynamics_scale,
        }
        if self.reading is not None:
            result["reading"] = self.reading
            result["reading_control"] = "accent_phrases"
        return result


class _Runtime(Protocol):
    def prepare(self) -> None: ...

    def synthesize(
        self,
        *,
        text: str,
        reading: str | None,
        speaker_id: int,
        intonation_scale: float,
        tempo_dynamics_scale: float,
        output_wav: Path,
    ) -> Mapping[str, Any]: ...


class _HttpRuntime:
    def __init__(
        self,
        *,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._opener = opener if opener is not None else _loopback_opener()
        self._prepared = False

    def prepare(self) -> None:
        if self._prepared:
            raise AivisSpeechAdapterError(
                "AivisSpeech Engine runtime はすでに prepare 済みです。",
            )

        version = self._request_json("GET", "/version")
        if version != ENGINE_VERSION:
            raise AivisSpeechAdapterError(
                "AivisSpeech Engine version が一致しません: "
                f"expected={ENGINE_VERSION}, actual={version!r}",
            )

        engine_manifest = self._request_json("GET", "/engine_manifest")
        _validate_engine_manifest(engine_manifest)
        models = self._request_json("GET", "/aivm_models")
        _validate_model_inventory(models)
        speakers = self._request_json("GET", "/speakers")
        _validate_speaker_inventory(speakers)
        self._prepared = True

    def synthesize(
        self,
        *,
        text: str,
        reading: str | None,
        speaker_id: int,
        intonation_scale: float,
        tempo_dynamics_scale: float,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        if not self._prepared:
            raise AivisSpeechAdapterError(
                "AivisSpeech Engine runtime が prepare されていません。",
            )

        query = self._request_json(
            "POST",
            "/audio_query",
            query={"text": text, "speaker": speaker_id},
        )
        if not isinstance(query, dict) or not isinstance(
            query.get("accent_phrases"),
            list,
        ):
            raise AivisSpeechAdapterError(
                "AivisSpeech Engine /audio_query の応答が不正です。",
            )
        if reading is not None:
            accent_phrases = self._request_json(
                "POST",
                "/accent_phrases",
                query={
                    "text": reading,
                    "speaker": speaker_id,
                    "is_kana": "false",
                },
            )
            if (
                not isinstance(accent_phrases, list)
                or not accent_phrases
                or any(
                    not isinstance(phrase, Mapping)
                    or not isinstance(phrase.get("moras"), list)
                    or not phrase["moras"]
                    for phrase in accent_phrases
                )
            ):
                raise AivisSpeechAdapterError(
                    "AivisSpeech Engine /accent_phrases の応答が不正です。",
                )
            query["accent_phrases"] = accent_phrases
        query["intonationScale"] = intonation_scale
        query["tempoDynamicsScale"] = tempo_dynamics_scale
        query["outputSamplingRate"] = SAMPLE_RATE_HZ
        query["outputStereo"] = False
        query["kana"] = text

        wav_bytes, content_type = self._request_bytes(
            "POST",
            "/synthesis",
            query={"speaker": speaker_id},
            body=query,
        )
        if not content_type.lower().startswith("audio/wav"):
            raise AivisSpeechAdapterError(
                "AivisSpeech Engine /synthesis の Content-Type が不正です: "
                f"{content_type!r}",
            )
        audio = _inspect_pcm_wav(wav_bytes)

        output_wav.parent.mkdir(parents=True, exist_ok=True)
        output_wav.write_bytes(wav_bytes)
        return {
            "engine_version": ENGINE_VERSION,
            "model_uuid": MODEL_UUID,
            "model_version": MODEL_VERSION,
            "speaker_uuid": SPEAKER_UUID,
            "speaker_style_id": speaker_id,
            "intonation_scale": intonation_scale,
            "tempo_dynamics_scale": tempo_dynamics_scale,
            "sample_rate_hz": audio["sample_rate_hz"],
            "channels": audio["channels"],
            "sample_width_bytes": audio["sample_width_bytes"],
        }

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        payload, _content_type = self._request_bytes(
            method,
            path,
            query=query,
            body=body,
        )
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AivisSpeechAdapterError(
                f"AivisSpeech Engine {path} が有効な UTF-8 JSON を返しません。",
            ) from error

    def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> tuple[bytes, str]:
        url = f"{ENGINE_URL}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = None
        headers: dict[str, str] = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self._opener(
                request,
                timeout=REQUEST_TIMEOUT_SEC,
            ) as response:
                payload = response.read()
                content_type = str(response.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise AivisSpeechAdapterError(
                f"AivisSpeech Engine {path} が HTTP {error.code} を返しました: "
                f"{detail}",
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise AivisSpeechAdapterError(
                f"AivisSpeech Engine {ENGINE_URL} に接続できません: {error}",
            ) from error
        if not payload:
            raise AivisSpeechAdapterError(
                f"AivisSpeech Engine {path} の応答が空です。",
            )
        return payload, content_type


class AivisSpeechAdapter:
    profile = ModelProfile(
        id=MODEL_ID,
        name="AivisSpeech コハク",
        version=PROFILE_VERSION,
        license_note=(
            "Engine は LGPL-3.0、公式コハク 1.1.0 は ACML-1.0。"
            "営利利用・クレジットなしの利用は許諾されるが、なりすまし、"
            "攻撃・中傷、虚偽情報、特定の政治・宗教などへの賛否を呼びかける"
            "活動を含む ACML の禁止用途には利用しない。"
            "推奨クレジット: AivisSpeech: コハク。"
        ),
        capabilities=Capabilities(
            emotion=True,
            voice_prompt=False,
            clone=False,
            nonverbal=False,
            reading=True,
        ),
    )

    def take_recipe(self) -> TakeRecipe:
        return TakeRecipe(
            version="fixed-single-v1",
            seed_policy="none",
            single_take_seed=None,
            seed_range=None,
            sampling=(),
            supports_multiple=False,
        )

    def __init__(self, *, runtime: _Runtime | None = None) -> None:
        self._runtime = runtime if runtime is not None else _HttpRuntime()
        self._prepared_inputs: dict[tuple[str, str], _PreparedInput] = {}
        self._prepared = False

    def prepare(
        self,
        jobs: Sequence[LineJob],
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        del artifacts_dir, voices_dir
        self._prepared = False
        self._prepared_inputs.clear()

        for job in jobs:
            key = _job_key(job)
            if key in self._prepared_inputs:
                raise AivisSpeechAdapterError(
                    f"同じ line job が重複しています: {key[0]}/{key[1]}",
                )
            self._prepared_inputs[key] = _prepare_input(job)

        self._runtime.prepare()
        self._prepared = True

    def generation_params(self) -> Mapping[str, Any]:
        return {
            "engine": "AivisSpeech Engine",
            "engine_url": ENGINE_URL,
            "engine_version": ENGINE_VERSION,
            "engine_manifest_version": ENGINE_MANIFEST_VERSION,
            "engine_manifest_uuid": ENGINE_MANIFEST_UUID,
            "model_name": MODEL_NAME,
            "model_uuid": MODEL_UUID,
            "model_version": MODEL_VERSION,
            "model_sha256": MODEL_SHA256,
            "model_license": "ACML-1.0",
            "speaker_name": SPEAKER_NAME,
            "speaker_uuid": SPEAKER_UUID,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "style_ids": dict(STYLE_IDS),
            "style_by_emotion": dict(STYLE_BY_EMOTION),
            "intonation_scale_by_intensity": {
                str(key): value
                for key, value in INTONATION_SCALE_BY_INTENSITY.items()
            },
            "tempo_dynamics_scale_by_intensity": {
                str(key): value
                for key, value in TEMPO_DYNAMICS_SCALE_BY_INTENSITY.items()
            },
        }

    def generation_input(
        self,
        job: LineJob,
        take_context: TakeContext,
    ) -> Mapping[str, Any]:
        require_take_context(take_context, self.take_recipe())
        return self._prepared_input(job).as_generation_input()

    def generate(
        self,
        job: LineJob,
        take_context: TakeContext,
        output_wav: Path,
    ) -> Mapping[str, Any]:
        require_take_context(take_context, self.take_recipe())
        prepared = self._prepared_input(job)
        try:
            realized = self._runtime.synthesize(
                text=prepared.text,
                reading=prepared.reading,
                speaker_id=prepared.style_id,
                intonation_scale=prepared.intonation_scale,
                tempo_dynamics_scale=prepared.tempo_dynamics_scale,
                output_wav=output_wav,
            )
        except Exception as error:
            if isinstance(error, AivisSpeechAdapterError):
                raise
            raise AivisSpeechAdapterError(
                f"AivisSpeech 生成 ({job.scenario_id}/{job.line_id}) "
                f"に失敗しました: {error}",
            ) from error
        if not output_wav.is_file():
            raise AivisSpeechAdapterError(
                f"adapter 出力がありません: {output_wav}",
            )
        receipt = {
            **dict(realized),
            "speaker_style_name": prepared.style_name,
            "speaker_style_id": prepared.style_id,
            "intonation_scale": prepared.intonation_scale,
            "tempo_dynamics_scale": prepared.tempo_dynamics_scale,
        }
        if prepared.reading is not None:
            receipt["reading"] = prepared.reading
            receipt["reading_source"] = prepared.reading_source
            receipt["reading_control"] = "accent_phrases"
        return receipt

    def _prepared_input(self, job: LineJob) -> _PreparedInput:
        if not self._prepared:
            raise AivisSpeechAdapterError("prepare() が完了していません。")
        key = _job_key(job)
        try:
            return self._prepared_inputs[key]
        except KeyError as error:
            raise AivisSpeechAdapterError(
                f"prepare 済み input がありません: {key[0]}/{key[1]}",
            ) from error


def _prepare_input(job: LineJob) -> _PreparedInput:
    if job.locale != "ja":
        raise AivisSpeechAdapterError(
            f"AivisSpeech コハクは Japanese 固定です: locale={job.locale}",
        )
    text = _required_string(job.line, "text", "line")
    reading = job.line.get("reading")
    if reading is None:
        reading_source = "line.text"
    elif isinstance(reading, str) and reading.strip():
        reading_source = "line.reading"
    else:
        raise AivisSpeechAdapterError(
            "line.reading は non-empty string または null が必要です。",
        )

    emotion = _required_string(job.line, "emotion", "line")
    try:
        style_name = STYLE_BY_EMOTION[emotion]
    except KeyError as error:
        raise AivisSpeechAdapterError(
            f"未対応の line.emotion です: {emotion}",
        ) from error

    intensity = job.line.get("intensity", 2)
    if isinstance(intensity, bool) or not isinstance(intensity, int):
        raise AivisSpeechAdapterError("line.intensity は 1〜3 の整数が必要です。")
    try:
        intonation_scale = INTONATION_SCALE_BY_INTENSITY[intensity]
        tempo_dynamics_scale = TEMPO_DYNAMICS_SCALE_BY_INTENSITY[intensity]
    except KeyError as error:
        raise AivisSpeechAdapterError(
            f"line.intensity は 1〜3 が必要です: {intensity}",
        ) from error

    return _PreparedInput(
        text=text,
        reading=reading,
        reading_source=reading_source,
        emotion=emotion,
        intensity=intensity,
        style_name=style_name,
        style_id=STYLE_IDS[style_name],
        intonation_scale=intonation_scale,
        tempo_dynamics_scale=tempo_dynamics_scale,
    )


def _validate_model_inventory(value: Any) -> None:
    if not isinstance(value, dict):
        raise AivisSpeechAdapterError(
            "AivisSpeech Engine /aivm_models の応答が object ではありません。",
        )
    model = value.get(MODEL_UUID)
    if not isinstance(model, dict):
        raise AivisSpeechAdapterError(
            f"公式 {MODEL_NAME} model が未インストールです: {MODEL_UUID}",
        )
    if model.get("is_loaded") is not True:
        raise AivisSpeechAdapterError(
            f"公式 {MODEL_NAME} model がロードされていません。",
        )
    if model.get("is_private_model") is not False:
        raise AivisSpeechAdapterError(
            f"公式 {MODEL_NAME} model が private model として登録されています。",
        )
    if model.get("file_size") != MODEL_FILE_SIZE:
        raise AivisSpeechAdapterError(
            f"{MODEL_NAME} model の file size が一致しません。",
        )

    manifest = model.get("manifest")
    if not isinstance(manifest, dict):
        raise AivisSpeechAdapterError(
            f"{MODEL_NAME} model manifest がありません。",
        )
    expected_manifest = {
        "manifest_version": "1.0",
        "name": MODEL_NAME,
        "uuid": MODEL_UUID,
        "version": MODEL_VERSION,
        "model_architecture": "Style-Bert-VITS2 (JP-Extra)",
        "model_format": "ONNX",
    }
    for key, expected in expected_manifest.items():
        actual = manifest.get(key)
        if actual != expected:
            raise AivisSpeechAdapterError(
                f"{MODEL_NAME} manifest {key} が一致しません: "
                f"expected={expected!r}, actual={actual!r}",
            )
    license_text = manifest.get("license")
    if (
        not isinstance(license_text, str)
        or "Aivis Common Model License (ACML) 1.0" not in license_text
    ):
        raise AivisSpeechAdapterError(
            f"{MODEL_NAME} model license が ACML-1.0 ではありません。",
        )

    manifest_speakers = manifest.get("speakers")
    if not isinstance(manifest_speakers, list) or len(manifest_speakers) != 1:
        raise AivisSpeechAdapterError(
            f"{MODEL_NAME} manifest の speaker 構成が一致しません。",
        )
    manifest_speaker = manifest_speakers[0]
    if not isinstance(manifest_speaker, dict):
        raise AivisSpeechAdapterError(
            f"{MODEL_NAME} manifest の speaker が不正です。",
        )
    if (
        manifest_speaker.get("name") != SPEAKER_NAME
        or manifest_speaker.get("uuid") != SPEAKER_UUID
    ):
        raise AivisSpeechAdapterError(
            f"{MODEL_NAME} manifest の speaker が一致しません。",
        )
    _validate_styles(
        manifest_speaker.get("styles"),
        id_key="local_id",
        expected_ids=STYLE_LOCAL_IDS,
        label=f"{MODEL_NAME} manifest",
    )

    file_path_value = model.get("file_path")
    if not isinstance(file_path_value, str) or not file_path_value:
        raise AivisSpeechAdapterError(
            f"{MODEL_NAME} model の file_path が不正です。",
        )
    file_path = _validated_model_path(file_path_value)
    if not file_path.is_file():
        raise AivisSpeechAdapterError(
            f"{MODEL_NAME} model file がありません: {file_path}",
        )
    actual_sha256 = _sha256_file(file_path)
    if actual_sha256 != MODEL_SHA256:
        raise AivisSpeechAdapterError(
            f"{MODEL_NAME} model SHA-256 が一致しません: "
            f"expected={MODEL_SHA256}, actual={actual_sha256}",
        )


def _validate_engine_manifest(value: Any) -> None:
    if not isinstance(value, dict):
        raise AivisSpeechAdapterError(
            "AivisSpeech Engine /engine_manifest の応答が object ではありません。",
        )
    expected = {
        "manifest_version": ENGINE_MANIFEST_VERSION,
        "name": "AivisSpeech Engine",
        "brand_name": "AivisSpeech",
        "uuid": ENGINE_MANIFEST_UUID,
        "url": ENGINE_MANIFEST_URL,
    }
    for key, expected_value in expected.items():
        actual = value.get(key)
        if actual != expected_value:
            raise AivisSpeechAdapterError(
                f"AivisSpeech Engine manifest {key} が一致しません: "
                f"expected={expected_value!r}, actual={actual!r}",
            )


def _validated_model_path(value: str) -> Path:
    if value.startswith(("\\\\", "//")):
        raise AivisSpeechAdapterError(
            f"{MODEL_NAME} model file に UNC path は使用できません。",
        )
    appdata = os.environ.get("APPDATA")
    if not isinstance(appdata, str) or not appdata.strip():
        raise AivisSpeechAdapterError(
            "APPDATA がないため AivisSpeech model path を検証できません。",
        )
    models_root = Path(
        os.path.abspath(
            os.path.join(appdata, "AivisSpeech-Engine", "Models"),
        ),
    )
    model_path = Path(os.path.abspath(value))
    try:
        model_path.relative_to(models_root)
    except ValueError as error:
        raise AivisSpeechAdapterError(
            f"{MODEL_NAME} model file が Models directory 外です: {model_path}",
        ) from error
    if model_path.suffix.lower() != ".aivmx":
        raise AivisSpeechAdapterError(
            f"{MODEL_NAME} model file は .aivmx が必要です: {model_path}",
        )
    if model_path.is_symlink():
        raise AivisSpeechAdapterError(
            f"{MODEL_NAME} model file に symlink は使用できません: {model_path}",
        )
    return model_path


def _validate_speaker_inventory(value: Any) -> None:
    if not isinstance(value, list):
        raise AivisSpeechAdapterError(
            "AivisSpeech Engine /speakers の応答が array ではありません。",
        )
    matches = [
        speaker
        for speaker in value
        if isinstance(speaker, dict)
        and speaker.get("speaker_uuid") == SPEAKER_UUID
    ]
    if len(matches) != 1:
        raise AivisSpeechAdapterError(
            f"{SPEAKER_NAME} speaker が一意に見つかりません: {SPEAKER_UUID}",
        )
    speaker = matches[0]
    if (
        speaker.get("name") != SPEAKER_NAME
        or speaker.get("version") != MODEL_VERSION
    ):
        raise AivisSpeechAdapterError(
            f"{SPEAKER_NAME} speaker metadata が一致しません。",
        )
    _validate_styles(
        speaker.get("styles"),
        id_key="id",
        expected_ids=STYLE_IDS,
        label=f"{SPEAKER_NAME} /speakers",
        required_type="talk",
    )


def _validate_styles(
    value: Any,
    *,
    id_key: str,
    expected_ids: Mapping[str, int],
    label: str,
    required_type: str | None = None,
) -> None:
    if not isinstance(value, list):
        raise AivisSpeechAdapterError(f"{label} の styles が array ではありません。")
    actual: dict[str, int] = {}
    for style in value:
        if not isinstance(style, dict):
            raise AivisSpeechAdapterError(f"{label} の style が不正です。")
        name = style.get("name")
        style_id = style.get(id_key)
        if not isinstance(name, str) or isinstance(style_id, bool) or not isinstance(
            style_id,
            int,
        ):
            raise AivisSpeechAdapterError(f"{label} の style metadata が不正です。")
        if required_type is not None and style.get("type") != required_type:
            raise AivisSpeechAdapterError(
                f"{label} の style type が一致しません: "
                f"expected={required_type!r}, actual={style.get('type')!r}",
            )
        if name in actual:
            raise AivisSpeechAdapterError(
                f"{label} に重複した style name があります: {name}",
            )
        actual[name] = style_id
    if actual != dict(expected_ids):
        raise AivisSpeechAdapterError(
            f"{label} の styles が一致しません: "
            f"expected={dict(expected_ids)!r}, actual={actual!r}",
        )


def _inspect_pcm_wav(payload: bytes) -> dict[str, int]:
    try:
        with wave.open(io.BytesIO(payload), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frames = wav_file.getnframes()
    except (EOFError, wave.Error) as error:
        raise AivisSpeechAdapterError(
            "AivisSpeech Engine が有効な PCM WAV を返しません。",
        ) from error
    if channels != 1 or sample_width != 2 or sample_rate != SAMPLE_RATE_HZ:
        raise AivisSpeechAdapterError(
            "AivisSpeech Engine の WAV 形式が一致しません: "
            f"channels={channels}, sample_width={sample_width}, "
            f"sample_rate={sample_rate}",
        )
    if frames <= 0:
        raise AivisSpeechAdapterError(
            "AivisSpeech Engine の WAV に audio frame がありません。",
        )
    return {
        "channels": channels,
        "sample_width_bytes": sample_width,
        "sample_rate_hz": sample_rate,
        "frames": frames,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _job_key(job: LineJob) -> tuple[str, str]:
    scenario_id = _required_string(job.scene, "id", "scene")
    line_id = _required_string(job.line, "id", "line")
    return scenario_id, line_id


def _required_string(
    value: Mapping[str, Any],
    key: str,
    label: str,
) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise AivisSpeechAdapterError(
            f"{label}.{key} は non-empty string が必要です。",
        )
    return result
