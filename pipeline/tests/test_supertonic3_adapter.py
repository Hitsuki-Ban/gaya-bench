from __future__ import annotations

import json
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaya_pipeline.adapters import supertonic3 as supertonic3_module
from gaya_pipeline.adapters import create_adapter
from gaya_pipeline.adapters.base import LineJob
from gaya_pipeline.adapters.supertonic3 import (
    EXPECTED_PROVIDERS,
    EXPECTED_VOICE_STYLES,
    INTER_OP_THREADS,
    INTRA_OP_THREADS,
    LANGUAGE_ID,
    MODEL_FILES,
    MODEL_ROOT_ENV,
    NUMPY_VERSION,
    ONNXRUNTIME_VERSION,
    SAMPLE_RATE_HZ,
    SDK_VERSION,
    SEED,
    SOUNDFILE_VERSION,
    Supertonic3Adapter,
    Supertonic3AdapterError,
    _LocalRuntime,
    _inspect_pcm_wav,
    _prepare_input,
    _validate_model_root,
)

TAKE_CONTEXT = Supertonic3Adapter().take_recipe().single_take_context()


class _FakeRuntime:
    def __init__(self, *, write_output: bool = True) -> None:
        self.write_output = write_output
        self.model_root: Path | None = None
        self.calls: list[dict[str, Any]] = []

    def prepare(self, model_root: Path) -> None:
        self.model_root = model_root

    def synthesize(
        self,
        *,
        text: str,
        voice_style: str,
        output_wav: Path,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "text": text,
                "voice_style": voice_style,
                "output_wav": output_wav,
            },
        )
        if self.write_output:
            _write_pcm_wav(output_wav)
        return {
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "seed": SEED,
            "voice_style": voice_style,
        }


def _job(
    *,
    scenario: str = "tavern-night",
    character: str = "barmaid",
    line_id: str = "barmaid-001",
    text: str = "はいよっ、エール二つお待ち！",
    reading: object = None,
    locale: str = "ja",
    emotion: str = "cheerful",
    intensity: int = 2,
    delivery: str = "明るく話す。",
) -> LineJob:
    line: dict[str, Any] = {
        "id": line_id,
        "character": character,
        "text": text,
        "emotion": emotion,
        "intensity": intensity,
        "delivery": delivery,
    }
    if reading is not None:
        line["reading"] = reading
    return LineJob(
        scene={"id": scenario},
        character={
            "id": character,
            "voice": "adapter では使用しない声質記述",
            "reference_voice": "adapter では使用しない参照音声",
        },
        line=line,
        locale=locale,
    )


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    jobs: list[LineJob],
    *,
    runtime: _FakeRuntime | None = None,
) -> tuple[Supertonic3Adapter, _FakeRuntime]:
    model_root = tmp_path / "model"
    model_root.mkdir(parents=True)
    monkeypatch.setenv(MODEL_ROOT_ENV, str(model_root.resolve()))
    selected_runtime = runtime or _FakeRuntime()
    adapter = Supertonic3Adapter(runtime=selected_runtime)
    adapter.prepare(jobs, tmp_path / "artifacts", tmp_path / "voices")
    return adapter, selected_runtime


def _write_pcm_wav(
    path: Path,
    *,
    sample_rate: int = SAMPLE_RATE_HZ,
    channels: int = 1,
    sample_width: int = 2,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\0" * sample_width * channels * 16)


def test_profile_params_and_registry_are_exact() -> None:
    adapter = Supertonic3Adapter(runtime=_FakeRuntime())
    assert adapter.profile.id == "supertonic-3"
    assert adapter.profile.capabilities.as_dict() == {
        "emotion": False,
        "voice_prompt": False,
        "clone": False,
        "nonverbal": False,
        "reading": True,
    }
    recipe = adapter.take_recipe()
    assert recipe.version == "fixed-single-v1"
    assert recipe.seed_policy == "fixed"
    assert recipe.single_take_seed == SEED
    assert recipe.seed_range == (0, 2**32 - 1)
    assert recipe.supports_multiple is False
    assert "Open RAIL-M" in adapter.profile.license_note
    assert "機械生成" in adapter.profile.license_note

    params = dict(adapter.generation_params())
    assert params["weights_revision"] == (
        "724fb5abbf5502583fb520898d45929e62f02c0b"
    )
    assert params["provider"] == "CPUExecutionProvider"
    assert params["intra_op_num_threads"] == 10
    assert params["inter_op_num_threads"] == 1
    assert params["expression_tags"] is False
    assert params["voice_builder"] is False
    assert params["auto_download"] is False
    assert params["model_files"]["onnx/vocoder.onnx"] == {
        "size": 101_424_195,
        "sha256": (
            "085de76dd8e8d5836d6ca66826601f615939218f90e519f70ee8a36ed2a4c4ba"
        ),
    }
    canonical = json.dumps(params, ensure_ascii=False, sort_keys=True)
    assert MODEL_ROOT_ENV in canonical
    assert "F:\\" not in canonical
    assert isinstance(create_adapter("supertonic-3"), Supertonic3Adapter)


@pytest.mark.parametrize(
    ("scenario", "character", "voice"),
    [
        ("tavern-night", "barmaid", "F2"),
        ("tavern-night", "drunkard", "M1"),
        ("tavern-night", "old-regular", "M5"),
        ("market-day", "fruit-vendor", "M1"),
        ("market-day", "shopper", "F1"),
        ("market-day", "street-kid", "F2"),
    ],
)
def test_fixed_voice_assignments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: str,
    character: str,
    voice: str,
) -> None:
    job = _job(
        scenario=scenario,
        character=character,
        line_id=f"{character}-001",
    )
    adapter, _runtime = _prepare(monkeypatch, tmp_path, [job])
    generation_input = adapter.generation_input(job, TAKE_CONTEXT)
    assert generation_input["voice_style"] == voice
    assert generation_input["voice_style_sha256"] == MODEL_FILES[
        f"voice_styles/{voice}.json"
    ][1]
    assert generation_input["voice_selection_source"] == (
        f"adapter.assignment:{scenario}/{character}"
    )


def test_reading_priority_and_unused_metadata_do_not_change_model_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = _job(
        line_id="barmaid-001",
        reading="ハイヨッ、エールフタツオマチ！",
        emotion="angry",
        intensity=1,
        delivery="囁く。",
    )
    second = _job(
        line_id="barmaid-002",
        reading="ハイヨッ、エールフタツオマチ！",
        emotion="cheerful",
        intensity=3,
        delivery="叫ぶ。",
    )
    adapter, _runtime = _prepare(monkeypatch, tmp_path, [first, second])
    first_input = dict(adapter.generation_input(first, TAKE_CONTEXT))
    second_input = dict(adapter.generation_input(second, TAKE_CONTEXT))
    assert first_input == second_input
    assert first_input["source_text"] == "はいよっ、エール二つお待ち！"
    assert first_input["tts_text"] == "ハイヨッ、エールフタツオマチ！"
    assert first_input["reading_source"] == "line.reading"
    assert "emotion" not in first_input
    assert "intensity" not in first_input
    assert "delivery" not in first_input
    assert "reference_voice" not in first_input


def test_missing_reading_uses_original_japanese_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = _job()
    adapter, _runtime = _prepare(monkeypatch, tmp_path, [job])
    generation_input = adapter.generation_input(job, TAKE_CONTEXT)
    assert generation_input["source_text"] == job.line["text"]
    assert generation_input["tts_text"] == job.line["text"]
    assert generation_input["reading_source"] == "line.text"


@pytest.mark.parametrize("reading", ["", "   ", 123, False])
def test_invalid_explicit_reading_fails(
    reading: object,
) -> None:
    job = _job()
    job.line["reading"] = reading
    with pytest.raises(Supertonic3AdapterError, match="line.reading"):
        _prepare_input(job)


@pytest.mark.parametrize(
    "text",
    [
        "<laugh> ほっほ、若いのは元気でいい。",
        "これは <ja> タグです。",
        "1 < 2",
    ],
)
def test_expression_or_language_tag_injection_fails(text: str) -> None:
    with pytest.raises(
        Supertonic3AdapterError,
        match="expression/language tag",
    ):
        _prepare_input(_job(text=text))


def test_unknown_assignment_and_locale_fail_before_runtime_load(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_root = tmp_path / "model"
    model_root.mkdir()
    monkeypatch.setenv(MODEL_ROOT_ENV, str(model_root.resolve()))
    runtime = _FakeRuntime()
    adapter = Supertonic3Adapter(runtime=runtime)

    with pytest.raises(Supertonic3AdapterError, match="固定 voice assignment"):
        adapter.prepare(
            [_job(character="unknown", line_id="unknown-001")],
            tmp_path,
            tmp_path,
        )
    assert runtime.model_root is None

    with pytest.raises(Supertonic3AdapterError, match="Japanese 固定"):
        adapter.prepare([_job(locale="en")], tmp_path, tmp_path)
    assert runtime.model_root is None


def test_model_root_is_required_and_must_be_absolute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(MODEL_ROOT_ENV, raising=False)
    adapter = Supertonic3Adapter(runtime=_FakeRuntime())
    with pytest.raises(Supertonic3AdapterError, match=MODEL_ROOT_ENV):
        adapter.prepare([_job()], tmp_path, tmp_path)

    monkeypatch.setenv(MODEL_ROOT_ENV, "relative/model")
    with pytest.raises(Supertonic3AdapterError, match="絶対パス"):
        adapter.prepare([_job()], tmp_path, tmp_path)


def test_duplicate_unprepared_and_unknown_jobs_fail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = _job()
    adapter = Supertonic3Adapter(runtime=_FakeRuntime())
    with pytest.raises(Supertonic3AdapterError, match=r"prepare\(\)"):
        adapter.generation_input(job, TAKE_CONTEXT)

    model_root = tmp_path / "model"
    model_root.mkdir()
    monkeypatch.setenv(MODEL_ROOT_ENV, str(model_root.resolve()))
    with pytest.raises(Supertonic3AdapterError, match="重複"):
        adapter.prepare([job, job], tmp_path, tmp_path)

    adapter, _runtime = _prepare(monkeypatch, tmp_path / "other", [job])
    with pytest.raises(Supertonic3AdapterError, match="prepare 済み input"):
        adapter.generation_input(_job(line_id="barmaid-999"), TAKE_CONTEXT)


def test_generate_uses_exact_prepared_input_and_requires_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = _job(reading="ハイヨッ、エールフタツオマチ！")
    adapter, runtime = _prepare(monkeypatch, tmp_path, [job])
    output = tmp_path / "out" / "line.wav"
    realized = adapter.generate(job, TAKE_CONTEXT, output)
    assert runtime.calls == [
        {
            "text": "ハイヨッ、エールフタツオマチ！",
            "voice_style": "F2",
            "output_wav": output,
        },
    ]
    assert realized["reading_source"] == "line.reading"
    assert realized["voice_style"] == "F2"
    assert realized["voice_style_sha256"] == MODEL_FILES[
        "voice_styles/F2.json"
    ][1]

    missing_adapter, _missing_runtime = _prepare(
        monkeypatch,
        tmp_path / "missing",
        [job],
        runtime=_FakeRuntime(write_output=False),
    )
    with pytest.raises(Supertonic3AdapterError, match="adapter 出力"):
        missing_adapter.generate(job, TAKE_CONTEXT, tmp_path / "missing.wav")


def test_model_inventory_accepts_cache_and_rejects_drift(tmp_path: Path) -> None:
    root = tmp_path / "model"
    root.mkdir()
    payload = b"frozen"
    digest = __import__("hashlib").sha256(payload).hexdigest()
    expected = {"onnx/model.onnx": (len(payload), digest)}
    asset = root / "onnx" / "model.onnx"
    asset.parent.mkdir()
    asset.write_bytes(payload)
    cache_file = root / ".cache" / "huggingface" / "metadata"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("ignored", encoding="utf-8")
    _validate_model_root(root, expected_files=expected)

    extra = root / "unexpected.bin"
    extra.write_bytes(b"x")
    with pytest.raises(Supertonic3AdapterError, match="inventory"):
        _validate_model_root(root, expected_files=expected)
    extra.unlink()

    asset.write_bytes(b"changed")
    with pytest.raises(Supertonic3AdapterError, match="size"):
        _validate_model_root(root, expected_files=expected)


def test_model_inventory_rejects_hash_drift_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "model"
    root.mkdir()
    asset = root / "asset"
    asset.write_bytes(b"same-size")
    expected = {"asset": (9, "0" * 64)}
    with pytest.raises(Supertonic3AdapterError, match="SHA-256"):
        _validate_model_root(root, expected_files=expected)

    target = tmp_path / "target"
    target.write_bytes(b"x")
    link = root / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("この Windows 環境では symlink を作成できません。")
    with pytest.raises(Supertonic3AdapterError, match="symlink/reparse"):
        _validate_model_root(root, expected_files={"asset": expected["asset"]})


def test_pcm_inspection_rejects_wrong_native_format(tmp_path: Path) -> None:
    valid = tmp_path / "valid.wav"
    _write_pcm_wav(valid)
    assert _inspect_pcm_wav(valid) == {
        "channels": 1,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "sample_width_bytes": 2,
        "frame_count": 16,
    }

    invalid = tmp_path / "invalid.wav"
    _write_pcm_wav(invalid, sample_rate=24_000)
    with pytest.raises(Supertonic3AdapterError, match="44.1kHz"):
        _inspect_pcm_wav(invalid)


def test_local_runtime_locks_packages_provider_threads_seed_and_wav(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    numpy = pytest.importorskip("numpy")
    init_args: dict[str, Any] = {}
    synth_args: dict[str, Any] = {}

    class _Session:
        def get_providers(self) -> list[str]:
            return list(EXPECTED_PROVIDERS)

    class _TTS:
        def __init__(self, **kwargs: Any) -> None:
            init_args.update(kwargs)
            self.model_name = "supertonic-3"
            self.sample_rate = SAMPLE_RATE_HZ
            self.voice_style_names = list(EXPECTED_VOICE_STYLES)
            self.model = SimpleNamespace(
                cfgs={"tts_version": "v1.7.3"},
                dp_ort=_Session(),
                text_enc_ort=_Session(),
                vector_est_ort=_Session(),
                vocoder_ort=_Session(),
            )

        def get_voice_style(self, voice_style: str) -> str:
            return voice_style

        def synthesize(
            self,
            text: str,
            style: str,
            **kwargs: Any,
        ) -> tuple[Any, Any]:
            synth_args.update({"text": text, "style": style, **kwargs})
            return (
                numpy.asarray([[0.0, 0.25, -0.25, 0.0]], dtype=numpy.float32),
                numpy.asarray([4 / SAMPLE_RATE_HZ], dtype=numpy.float32),
            )

    class _SoundFile:
        @staticmethod
        def write(
            path: str,
            samples: Any,
            sample_rate: int,
            *,
            subtype: str,
            format: str,
        ) -> None:
            assert samples.shape == (4,)
            assert subtype == "PCM_16"
            assert format == "WAV"
            _write_pcm_wav(Path(path), sample_rate=sample_rate)

    modules = {
        "numpy": numpy,
        "onnxruntime": SimpleNamespace(
            get_available_providers=lambda: ["CPUExecutionProvider"],
        ),
        "soundfile": _SoundFile(),
        "supertonic": SimpleNamespace(TTS=_TTS),
    }
    versions = {
        "supertonic": SDK_VERSION,
        "onnxruntime": ONNXRUNTIME_VERSION,
        "numpy": NUMPY_VERSION,
        "soundfile": SOUNDFILE_VERSION,
    }
    monkeypatch.setattr(supertonic3_module, "_validate_windows_runtime", lambda: None)
    monkeypatch.setattr(supertonic3_module, "_validate_model_root", lambda _root: None)
    monkeypatch.setattr(
        supertonic3_module.importlib,
        "import_module",
        lambda name: modules[name],
    )
    monkeypatch.setattr(
        supertonic3_module.importlib.metadata,
        "version",
        lambda name: versions[name],
    )

    runtime = _LocalRuntime()
    model_root = tmp_path / "model"
    model_root.mkdir()
    runtime.prepare(model_root)
    output = tmp_path / "output.wav"
    realized = runtime.synthesize(
        text="テストです。",
        voice_style="F2",
        output_wav=output,
    )
    assert init_args == {
        "model": "supertonic-3",
        "model_dir": model_root,
        "auto_download": False,
        "intra_op_num_threads": INTRA_OP_THREADS,
        "inter_op_num_threads": INTER_OP_THREADS,
    }
    assert synth_args == {
        "text": "テストです。",
        "style": "F2",
        "total_steps": 8,
        "speed": 1.05,
        "max_chunk_length": 300,
        "silence_duration": 0.3,
        "lang": LANGUAGE_ID,
        "verbose": False,
    }
    assert realized["onnx_providers"] == ["CPUExecutionProvider"]
    assert realized["sample_rate_hz"] == SAMPLE_RATE_HZ
    assert output.is_file()


@pytest.mark.parametrize(
    "waveform",
    [
        lambda np: np.asarray([], dtype=np.float32).reshape(1, 0),
        lambda np: np.asarray([0.0], dtype=np.float64).reshape(1, 1),
        lambda np: np.asarray([float("nan")], dtype=np.float32).reshape(1, 1),
        lambda np: np.asarray([1.1], dtype=np.float32).reshape(1, 1),
        lambda np: np.asarray([0.0], dtype=np.float32),
    ],
)
def test_local_runtime_rejects_invalid_waveforms(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    waveform: Any,
) -> None:
    numpy = pytest.importorskip("numpy")

    class _TTS:
        def get_voice_style(self, _voice_style: str) -> object:
            return object()

        def synthesize(self, *_args: Any, **_kwargs: Any) -> tuple[Any, Any]:
            return (
                waveform(numpy),
                numpy.asarray([0.1], dtype=numpy.float32),
            )

    runtime = _LocalRuntime()
    runtime._tts = _TTS()
    runtime._numpy = numpy
    runtime._soundfile = SimpleNamespace(write=lambda *_args, **_kwargs: None)
    with pytest.raises(Supertonic3AdapterError):
        runtime.synthesize(
            text="テスト",
            voice_style="F2",
            output_wav=tmp_path / "invalid.wav",
        )
