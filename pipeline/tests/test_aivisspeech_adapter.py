from __future__ import annotations

import struct
import urllib.error
import urllib.request
import wave
from collections.abc import Mapping, Sequence
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline.adapters import create_adapter
from gaya_pipeline.adapters.aivisspeech import (
    ENGINE_MANIFEST_UUID,
    ENGINE_MANIFEST_VERSION,
    ENGINE_URL,
    ENGINE_VERSION,
    INTONATION_SCALE_BY_INTENSITY,
    MODEL_ID,
    MODEL_SHA256,
    MODEL_UUID,
    MODEL_VERSION,
    SAMPLE_RATE_HZ,
    SPEAKER_UUID,
    STYLE_BY_EMOTION,
    STYLE_IDS,
    TEMPO_DYNAMICS_SCALE_BY_INTENSITY,
    AivisSpeechAdapter,
    AivisSpeechAdapterError,
    _HttpRuntime,
    _RejectRedirectHandler,
    _validate_engine_manifest,
    _validate_model_inventory,
    _validated_model_path,
    _validate_speaker_inventory,
)
from gaya_pipeline.adapters.base import LineJob

TAKE_CONTEXT = AivisSpeechAdapter().take_recipe().single_take_context()


class FakeRuntime:
    def __init__(self) -> None:
        self.prepare_count = 0
        self.synthesize_calls: list[dict[str, Any]] = []

    def prepare(self) -> None:
        self.prepare_count += 1

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
        self.synthesize_calls.append(
            {
                "text": text,
                "reading": reading,
                "speaker_id": speaker_id,
                "intonation_scale": intonation_scale,
                "tempo_dynamics_scale": tempo_dynamics_scale,
            },
        )
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_wav), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE_HZ)
            wav_file.writeframes(struct.pack("<h", 0) * 1_000)
        return {
            "engine_version": ENGINE_VERSION,
            "sample_rate_hz": SAMPLE_RATE_HZ,
        }


def _job(
    *,
    line_id: str = "barmaid-001",
    locale: str = "ja",
    text: str = "はいよっ、エール二つお待ち！",
    reading: object = None,
    emotion: str = "cheerful",
    intensity: object = 2,
) -> LineJob:
    line: dict[str, Any] = {
        "id": line_id,
        "character": "barmaid",
        "text": text,
        "emotion": emotion,
        "intensity": intensity,
        "delivery": "明るく呼びかける。",
    }
    if reading is not None:
        line["reading"] = reading
    return LineJob(
        scene={"id": "tavern-night", "setting": "酒場"},
        character={
            "id": "barmaid",
            "name": "給仕",
            "voice": "明るい声",
        },
        line=line,
        locale=locale,
    )


def _prepare(
    adapter: AivisSpeechAdapter,
    jobs: Sequence[LineJob],
    tmp_path: Path,
) -> None:
    adapter.prepare(jobs, tmp_path / "artifacts", tmp_path / "voices")


def test_profile_registry_and_generation_params_are_pinned() -> None:
    adapter = create_adapter(MODEL_ID)

    assert isinstance(adapter, AivisSpeechAdapter)
    assert adapter.profile.id == MODEL_ID
    assert ENGINE_VERSION in adapter.profile.version
    assert MODEL_VERSION in adapter.profile.version
    assert MODEL_SHA256 in adapter.profile.version
    assert "LGPL-3.0" in adapter.profile.license_note
    assert "ACML-1.0" in adapter.profile.license_note
    assert adapter.profile.capabilities.as_dict() == {
        "emotion": True,
        "voice_prompt": False,
        "clone": False,
        "nonverbal": False,
        "reading": True,
    }
    recipe = adapter.take_recipe()
    assert recipe.version == "fixed-single-v1"
    assert recipe.seed_policy == "none"
    assert recipe.single_take_seed is None
    assert recipe.seed_range is None
    assert recipe.sampling == ()
    assert recipe.supports_multiple is False

    params = adapter.generation_params()
    assert params["engine_url"] == ENGINE_URL
    assert params["engine_version"] == ENGINE_VERSION
    assert params["engine_manifest_version"] == ENGINE_MANIFEST_VERSION
    assert params["engine_manifest_uuid"] == ENGINE_MANIFEST_UUID
    assert params["model_uuid"] == MODEL_UUID
    assert params["model_sha256"] == MODEL_SHA256
    assert params["speaker_uuid"] == SPEAKER_UUID
    assert params["style_by_emotion"] == STYLE_BY_EMOTION
    assert "device" not in params


@pytest.mark.parametrize(
    ("emotion", "style_name"),
    list(STYLE_BY_EMOTION.items()),
)
def test_emotion_maps_to_exact_kohaku_style(
    tmp_path: Path,
    emotion: str,
    style_name: str,
) -> None:
    adapter = AivisSpeechAdapter(runtime=FakeRuntime())
    job = _job(emotion=emotion)
    _prepare(adapter, [job], tmp_path)

    generation_input = adapter.generation_input(job, TAKE_CONTEXT)
    assert generation_input["speaker_style"] == {
        "name": style_name,
        "id": STYLE_IDS[style_name],
    }
    assert generation_input["emotion"] == emotion


@pytest.mark.parametrize("intensity", [1, 2, 3])
def test_intensity_maps_to_supported_scales(
    tmp_path: Path,
    intensity: int,
) -> None:
    adapter = AivisSpeechAdapter(runtime=FakeRuntime())
    job = _job(intensity=intensity)
    _prepare(adapter, [job], tmp_path)

    generation_input = adapter.generation_input(job, TAKE_CONTEXT)
    assert generation_input["intonation_scale"] == (
        INTONATION_SCALE_BY_INTENSITY[intensity]
    )
    assert generation_input["tempo_dynamics_scale"] == (
        TEMPO_DYNAMICS_SCALE_BY_INTENSITY[intensity]
    )


def test_explicit_reading_keeps_surface_and_controls_accent_phrases(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    adapter = AivisSpeechAdapter(runtime=runtime)
    job = _job(reading="ハイヨッ、エールフタツオマチ！")
    _prepare(adapter, [job], tmp_path)

    generation_input = adapter.generation_input(job, TAKE_CONTEXT)
    assert generation_input["text"] == "はいよっ、エール二つお待ち！"
    assert generation_input["reading"] == "ハイヨッ、エールフタツオマチ！"
    assert generation_input["reading_source"] == "line.reading"
    assert generation_input["reading_control"] == "accent_phrases"

    adapter.generate(job, TAKE_CONTEXT, tmp_path / "reading.wav")
    assert runtime.synthesize_calls[0]["text"] == "はいよっ、エール二つお待ち！"
    assert runtime.synthesize_calls[0]["reading"] == (
        "ハイヨッ、エールフタツオマチ！"
    )


def test_generate_writes_pcm16_without_claiming_unverified_device(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    adapter = AivisSpeechAdapter(runtime=runtime)
    job = _job(emotion="fearful", intensity=3)
    _prepare(adapter, [job], tmp_path)

    output_wav = tmp_path / "output.wav"
    realized = adapter.generate(job, TAKE_CONTEXT, output_wav)

    assert runtime.prepare_count == 1
    assert runtime.synthesize_calls == [
        {
            "text": "はいよっ、エール二つお待ち！",
            "reading": None,
            "speaker_id": STYLE_IDS["せつなめ"],
            "intonation_scale": 1.2,
            "tempo_dynamics_scale": 1.2,
        },
    ]
    assert realized["speaker_style_name"] == "せつなめ"
    assert "device" not in realized
    assert "peak_vram_mib" not in realized
    with wave.open(str(output_wav), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == SAMPLE_RATE_HZ


def test_http_runtime_builds_accent_phrases_from_plain_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _HttpRuntime(opener=lambda *_args, **_kwargs: None)
    runtime._prepared = True
    json_calls: list[tuple[str, str, Mapping[str, Any] | None]] = []
    synthesis_body: dict[str, Any] = {}

    def request_json(
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        del body
        json_calls.append((method, path, query))
        if path == "/audio_query":
            return {
                "accent_phrases": [{"moras": [{"text": "ハ"}]}],
                "kana": "engine-generated",
            }
        if path == "/accent_phrases":
            return [{"moras": [{"text": "サ"}, {"text": "ガ"}]}]
        raise AssertionError(path)

    wav_buffer = BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE_HZ)
        wav_file.writeframes(struct.pack("<h", 0) * 100)

    def request_bytes(
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> tuple[bytes, str]:
        assert method == "POST"
        assert path == "/synthesis"
        assert query == {"speaker": STYLE_IDS["ノーマル"]}
        assert body is not None
        synthesis_body.update(body)
        return wav_buffer.getvalue(), "audio/wav"

    monkeypatch.setattr(runtime, "_request_json", request_json)
    monkeypatch.setattr(runtime, "_request_bytes", request_bytes)
    output = tmp_path / "http.wav"
    runtime.synthesize(
        text="退がれ！",
        reading="サガレ！",
        speaker_id=STYLE_IDS["ノーマル"],
        intonation_scale=1.0,
        tempo_dynamics_scale=1.0,
        output_wav=output,
    )

    assert json_calls == [
        (
            "POST",
            "/audio_query",
            {"text": "退がれ！", "speaker": STYLE_IDS["ノーマル"]},
        ),
        (
            "POST",
            "/accent_phrases",
            {
                "text": "サガレ！",
                "speaker": STYLE_IDS["ノーマル"],
                "is_kana": "false",
            },
        ),
    ]
    assert synthesis_body["kana"] == "退がれ！"
    assert synthesis_body["accent_phrases"] == [
        {"moras": [{"text": "サ"}, {"text": "ガ"}]},
    ]
    assert output.is_file()


def test_unprepared_unknown_and_duplicate_jobs_fail_fast(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    adapter = AivisSpeechAdapter(runtime=runtime)
    job = _job()

    with pytest.raises(AivisSpeechAdapterError, match=r"prepare\(\)"):
        adapter.generation_input(job, TAKE_CONTEXT)
    with pytest.raises(AivisSpeechAdapterError, match="重複"):
        _prepare(adapter, [job, job], tmp_path)

    _prepare(adapter, [job], tmp_path)
    with pytest.raises(AivisSpeechAdapterError, match="prepare 済み input"):
        adapter.generation_input(_job(line_id="other"), TAKE_CONTEXT)


@pytest.mark.parametrize(
    ("job", "message"),
    [
        (_job(locale="en"), "Japanese 固定"),
        (_job(emotion="unknown"), "未対応"),
        (_job(intensity=0), "1〜3"),
        (_job(intensity=True), "整数"),
        (_job(reading=" "), "non-empty string"),
    ],
)
def test_invalid_job_contract_fails_before_engine(
    tmp_path: Path,
    job: LineJob,
    message: str,
) -> None:
    runtime = FakeRuntime()
    adapter = AivisSpeechAdapter(runtime=runtime)

    with pytest.raises(AivisSpeechAdapterError, match=message):
        _prepare(adapter, [job], tmp_path)
    assert runtime.prepare_count == 0


def test_unreachable_engine_fails_without_fallback() -> None:
    def unreachable(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.URLError("connection refused")

    runtime = _HttpRuntime(opener=unreachable)
    with pytest.raises(
        AivisSpeechAdapterError,
        match=r"http://127\.0\.0\.1:10101.*接続できません",
    ):
        runtime.prepare()


def test_http_redirect_is_rejected() -> None:
    handler = _RejectRedirectHandler()
    with pytest.raises(AivisSpeechAdapterError, match="HTTP redirect"):
        handler.redirect_request(
            urllib.request.Request("http://127.0.0.1:10101/version"),
            None,
            302,
            "Found",
            {},
            "https://example.com/",
        )


def test_wrong_engine_version_fails_without_fallback() -> None:
    class Response:
        headers: dict[str, str] = {"Content-Type": "application/json"}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'"1.1.0"'

    runtime = _HttpRuntime(opener=lambda *_args, **_kwargs: Response())
    with pytest.raises(AivisSpeechAdapterError, match="version が一致"):
        runtime.prepare()


def test_engine_manifest_identity_drift_fails_fast() -> None:
    with pytest.raises(AivisSpeechAdapterError, match="uuid が一致"):
        _validate_engine_manifest(
            {
                "manifest_version": ENGINE_MANIFEST_VERSION,
                "name": "AivisSpeech Engine",
                "brand_name": "AivisSpeech",
                "uuid": "00000000-0000-0000-0000-000000000000",
                "url": "https://github.com/Aivis-Project/AivisSpeech-Engine",
            },
        )


def test_missing_kohaku_model_fails_without_using_other_model() -> None:
    with pytest.raises(AivisSpeechAdapterError, match="未インストール"):
        _validate_model_inventory(
            {
                "a59cb814-0083-4369-8542-f51a29e72af7": {
                    "manifest": {"name": "まお"},
                },
            },
        )


def test_model_path_rejects_unc_before_file_access() -> None:
    with pytest.raises(AivisSpeechAdapterError, match="UNC path"):
        _validated_model_path(r"\\server\share\kohaku.aivmx")


def test_global_style_id_drift_fails_fast() -> None:
    styles = [
        {"name": name, "id": style_id, "type": "talk"}
        for name, style_id in STYLE_IDS.items()
    ]
    styles[0]["id"] = 0
    with pytest.raises(AivisSpeechAdapterError, match="styles が一致"):
        _validate_speaker_inventory(
            [
                {
                    "name": "コハク",
                    "speaker_uuid": SPEAKER_UUID,
                    "version": MODEL_VERSION,
                    "styles": styles,
                },
            ],
        )
