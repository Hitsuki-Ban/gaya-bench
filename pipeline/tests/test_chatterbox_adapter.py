from __future__ import annotations

import hashlib
import json
import math
import struct
import wave
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from gaya_pipeline.adapters import create_adapter
from gaya_pipeline.adapters.base import LineJob
from gaya_pipeline.adapters.chatterbox import (
    ARCHITECTURE,
    CFG_WEIGHT,
    DEVICE,
    DTYPE,
    EXAGGERATION_BY_INTENSITY,
    LANGUAGE_ID,
    MODEL_ID,
    MODEL_ROOT_ENV,
    PROFILE_VERSION,
    SAMPLE_RATE_HZ,
    SEED,
    UPSTREAM_REVISION,
    WEIGHTS_REVISION,
    ChatterboxAdapter,
    ChatterboxAdapterError,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REGISTERED_VOICES = {
    "amitaro-countdown",
    "hadou-emotion-11",
    "lux-emotion-76",
    "sayoko-emotion-75",
    "tsukuyomi-corpus-94",
}


class FakeOutOfMemoryError(RuntimeError):
    pass


class FakeRuntime:
    def __init__(self) -> None:
        self.load_calls: list[Path] = []
        self.synthesize_calls: list[dict[str, Any]] = []
        self.write_calls: list[dict[str, Any]] = []
        self.identity = {
            "architecture": ARCHITECTURE,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "device": DEVICE,
            "dtype": DTYPE,
            "watermarker": "PerthImplicitWatermarker",
        }
        self.waveform: Any = [[0.0, 0.25, -0.25, 0.0]]
        self.oom_on: str | None = None
        self._phase = 0

    def load_model(self, snapshot_path: Path) -> dict[str, str]:
        self.load_calls.append(snapshot_path)
        if self.oom_on == "load":
            raise FakeOutOfMemoryError("load")
        return {"model": "chatterbox-v3"}

    def model_identity(self, model: Any) -> Mapping[str, Any]:
        assert model == {"model": "chatterbox-v3"}
        return dict(self.identity)

    def synthesize(
        self,
        model: Any,
        *,
        text: str,
        reference_wav: Path,
        exaggeration: float,
    ) -> Any:
        assert model == {"model": "chatterbox-v3"}
        self.synthesize_calls.append(
            {
                "text": text,
                "reference_wav": reference_wav,
                "exaggeration": exaggeration,
            },
        )
        if self.oom_on == "generate":
            raise FakeOutOfMemoryError("generate")
        return self.waveform

    def write_pcm16(self, path: Path, waveform: Any, sample_rate: int) -> None:
        self.write_calls.append(
            {
                "path": path,
                "waveform": waveform,
                "sample_rate": sample_rate,
            },
        )
        values = waveform[0] if len(waveform) == 1 else waveform
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(
                b"".join(
                    struct.pack("<h", round(float(value) * 32_767))
                    for value in values
                ),
            )

    def reset_peak_memory_stats(self) -> None:
        self._phase += 1

    def peak_memory_mib(self) -> Mapping[str, float]:
        return {
            "allocated_mib": float(self._phase * 100),
            "reserved_mib": float(self._phase * 100 + 25),
        }

    def is_out_of_memory(self, error: BaseException) -> bool:
        return isinstance(error, FakeOutOfMemoryError)


def _job(
    *,
    scenario_id: str = "tavern-night",
    character_id: str = "barmaid",
    line_id: str | None = None,
    reference_voice: str | None = "amitaro-countdown",
    text: str = "乾杯しよう！",
    reading: str | None = "カンパイシヨウ！",
    emotion: str = "cheerful",
    intensity: Any = 2,
    locale: str = "ja",
) -> LineJob:
    return LineJob(
        scene={"id": scenario_id, "setting": "夜の酒場。"},
        character={
            "id": character_id,
            "name": "給仕",
            "gender": "female",
            "age": "young_adult",
            "voice": "明るい声。",
            "reference_voice": reference_voice,
        },
        line={
            "id": line_id or f"{character_id}-001",
            "text": text,
            "reading": reading,
            "emotion": emotion,
            "intensity": intensity,
            "delivery": "明るく話す。",
        },
        locale=locale,
    )


def _voices_dir(
    tmp_path: Path,
    *,
    materialize: set[str] | None = None,
) -> Path:
    voices_dir = tmp_path / "voices"
    voices_dir.mkdir(parents=True)
    metadata = yaml.safe_load(
        (REPOSITORY_ROOT / "assets" / "voices" / "metadata.yaml").read_text(
            encoding="utf-8",
        ),
    )
    (voices_dir / "metadata.schema.json").write_text(
        (REPOSITORY_ROOT / "assets" / "voices" / "metadata.schema.json").read_text(
            encoding="utf-8",
        ),
        encoding="utf-8",
    )
    selected = REGISTERED_VOICES if materialize is None else materialize
    for index, entry in enumerate(metadata["voices"]):
        voice_id = str(entry["id"])
        if voice_id not in selected:
            continue
        audio_path = voices_dir / voice_id / "reference.wav"
        audio_path.parent.mkdir(parents=True)
        with wave.open(str(audio_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(48_000)
            frames = bytearray(b"\x00\x00" * (48_000 * 10))
            frames[index * 2 : index * 2 + 2] = struct.pack("<h", index + 1)
            wav_file.writeframes(frames)
        entry["sha256"] = hashlib.sha256(audio_path.read_bytes()).hexdigest()
        entry["duration_sec"] = 10.0
    (voices_dir / "metadata.yaml").write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return voices_dir


def _model_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    import gaya_pipeline.adapters.chatterbox as module

    root = tmp_path / "weights"
    root.mkdir()
    contents = {
        "Cangjie5_TC.json": b"cangjie",
        "grapheme_mtl_merged_expanded_v1.json": b"vocab",
        "s3gen.pt": b"s3gen",
        "t3_mtl23ls_v3.safetensors": b"t3-v3",
        "ve.pt": b"voice-encoder",
    }
    specs: dict[str, tuple[int, str]] = {}
    for name, content in contents.items():
        path = root / name
        path.write_bytes(content)
        specs[name] = (len(content), hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(module, "MODEL_FILE_SPECS", specs)
    return root


def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_absolute_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_absolute_path(item) for item in value)
    return isinstance(value, str) and Path(value).is_absolute()


def test_profile_registry_and_generation_params_are_canonical() -> None:
    adapter = create_adapter(MODEL_ID)

    assert isinstance(adapter, ChatterboxAdapter)
    assert adapter.profile.version == PROFILE_VERSION
    assert UPSTREAM_REVISION in adapter.profile.version
    assert WEIGHTS_REVISION in adapter.profile.version
    assert "MIT" in adapter.profile.license_note
    assert "PerTh" in adapter.profile.license_note
    assert adapter.profile.capabilities.as_dict() == {
        "emotion": True,
        "voice_prompt": False,
        "clone": True,
        "nonverbal": False,
        "reading": False,
    }
    params = adapter.generation_params()
    assert params["model_root_environment"] == MODEL_ROOT_ENV
    assert params["architecture"] == ARCHITECTURE
    assert params["t3_model"] == "v3"
    assert params["language_id"] == LANGUAGE_ID
    assert params["device"] == DEVICE
    assert params["dtype"] == DTYPE
    assert params["sample_rate_hz"] == SAMPLE_RATE_HZ
    assert params["seed"] == SEED
    assert params["cfg_weight"] == CFG_WEIGHT
    assert params["emotion_control"] == "exaggeration_only"
    assert params["perth_watermark"] is True
    assert params["exaggeration_by_intensity"] == {
        str(key): value for key, value in EXAGGERATION_BY_INTENSITY.items()
    }
    assert json.loads(json.dumps(params)) == params


@pytest.mark.parametrize(
    ("intensity", "expected"),
    [(1, 0.3), (2, 0.5), (3, 0.8)],
)
def test_intensity_maps_to_exaggeration_and_reading_is_ignored(
    tmp_path: Path,
    intensity: int,
    expected: float,
) -> None:
    adapter = ChatterboxAdapter(runtime=FakeRuntime())
    job = _job(intensity=intensity, reading="ヨミハツカワナイ")
    adapter.prepare([job], tmp_path / "artifacts", _voices_dir(tmp_path))

    generation_input = adapter.generation_input(job)
    assert generation_input["text"] == "乾杯しよう！"
    assert generation_input["language_id"] == "ja"
    assert generation_input["intensity"] == intensity
    assert generation_input["exaggeration"] == expected
    assert "reading" not in generation_input
    assert "emotion" not in generation_input
    assert "delivery" not in generation_input
    assert not _contains_absolute_path(generation_input)


def test_missing_intensity_uses_schema_default(
    tmp_path: Path,
) -> None:
    adapter = ChatterboxAdapter(runtime=FakeRuntime())
    job = _job()
    line = dict(job.line)
    line.pop("intensity")
    job = LineJob(job.scene, job.character, line, job.locale)
    adapter.prepare([job], tmp_path / "artifacts", _voices_dir(tmp_path))

    assert adapter.generation_input(job)["exaggeration"] == 0.5


@pytest.mark.parametrize("intensity", [True, 0, 4, 1.5, "2"])
def test_invalid_intensity_fails_before_model_load(
    tmp_path: Path,
    intensity: Any,
) -> None:
    runtime = FakeRuntime()
    adapter = ChatterboxAdapter(runtime=runtime)
    with pytest.raises(ChatterboxAdapterError, match="intensity"):
        adapter.prepare(
            [_job(intensity=intensity)],
            tmp_path / "artifacts",
            _voices_dir(tmp_path),
        )
    assert runtime.load_calls == []


@pytest.mark.parametrize(
    ("scenario_id", "character_id", "voice_id"),
    [
        ("tavern-night", "drunkard", "hadou-emotion-11"),
        ("tavern-night", "old-regular", "hadou-emotion-11"),
        ("market-day", "fruit-vendor", "hadou-emotion-11"),
        ("market-day", "shopper", "lux-emotion-76"),
        ("market-day", "street-kid", "tsukuyomi-corpus-94"),
    ],
)
def test_null_reference_uses_exact_assignment(
    tmp_path: Path,
    scenario_id: str,
    character_id: str,
    voice_id: str,
) -> None:
    adapter = ChatterboxAdapter(runtime=FakeRuntime())
    job = _job(
        scenario_id=scenario_id,
        character_id=character_id,
        reference_voice=None,
    )
    adapter.prepare([job], tmp_path / "artifacts", _voices_dir(tmp_path))

    generation_input = adapter.generation_input(job)
    assert generation_input["reference_voice"] == voice_id
    assert generation_input["reference_selection_source"] == (
        f"adapter.assignment:{scenario_id}/{character_id}"
    )


def test_explicit_reference_wins_and_unknown_null_never_falls_back(
    tmp_path: Path,
) -> None:
    voices_dir = _voices_dir(tmp_path)
    explicit = _job(
        scenario_id="market-day",
        character_id="shopper",
        reference_voice="sayoko-emotion-75",
    )
    adapter = ChatterboxAdapter(runtime=FakeRuntime())
    adapter.prepare([explicit], tmp_path / "artifacts", voices_dir)
    assert adapter.generation_input(explicit)["reference_voice"] == (
        "sayoko-emotion-75"
    )

    runtime = FakeRuntime()
    adapter = ChatterboxAdapter(runtime=runtime)
    with pytest.raises(ChatterboxAdapterError, match="固定 assignment"):
        adapter.prepare(
            [
                _job(
                    scenario_id="unknown",
                    character_id="unknown",
                    reference_voice=None,
                ),
            ],
            tmp_path / "artifacts",
            voices_dir,
        )
    assert runtime.load_calls == []


def test_reference_missing_hash_and_format_fail_without_fallback(
    tmp_path: Path,
) -> None:
    voices_dir = _voices_dir(
        tmp_path,
        materialize=REGISTERED_VOICES - {"amitaro-countdown"},
    )
    with pytest.raises(ChatterboxAdapterError, match="がありません"):
        ChatterboxAdapter(runtime=FakeRuntime()).prepare(
            [_job()],
            tmp_path / "artifacts",
            voices_dir,
        )

    voices_dir = _voices_dir(tmp_path / "hash")
    reference = voices_dir / "amitaro-countdown" / "reference.wav"
    with reference.open("ab") as stream:
        stream.write(b"broken")
    with pytest.raises(ChatterboxAdapterError, match="SHA-256"):
        ChatterboxAdapter(runtime=FakeRuntime()).prepare(
            [_job()],
            tmp_path / "artifacts",
            voices_dir,
        )

    voices_dir = _voices_dir(tmp_path / "format")
    reference = voices_dir / "amitaro-countdown" / "reference.wav"
    with wave.open(str(reference), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(48_000)
        wav_file.writeframes(b"\x00\x00\x00\x00" * 48_000 * 10)
    document = yaml.safe_load(
        (voices_dir / "metadata.yaml").read_text(encoding="utf-8"),
    )
    document["voices"][0]["sha256"] = hashlib.sha256(reference.read_bytes()).hexdigest()
    (voices_dir / "metadata.yaml").write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ChatterboxAdapterError, match="PCM16/48kHz/mono"):
        ChatterboxAdapter(runtime=FakeRuntime()).prepare(
            [_job()],
            tmp_path / "artifacts",
            voices_dir,
        )


def test_generate_is_lazy_clones_and_writes_native_pcm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _model_root(tmp_path, monkeypatch)
    runtime = FakeRuntime()
    adapter = ChatterboxAdapter(runtime=runtime, model_root=root)
    job = _job(intensity=3)
    voices_dir = _voices_dir(tmp_path)
    adapter.prepare([job], tmp_path / "artifacts", voices_dir)
    assert runtime.load_calls == []

    output = tmp_path / "output.wav"
    realized = adapter.generate(job, output)

    assert runtime.load_calls == [root]
    assert runtime.synthesize_calls == [
        {
            "text": "乾杯しよう！",
            "reference_wav": (
                voices_dir.resolve() / "amitaro-countdown" / "reference.wav"
            ),
            "exaggeration": 0.8,
        },
    ]
    with wave.open(str(output), "rb") as wav_file:
        assert wav_file.getframerate() == 24_000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
    assert realized["phase_peak_vram_mib"] == {
        "runtime_load": {"allocated_mib": 100.0, "reserved_mib": 125.0},
        "generation": {"allocated_mib": 200.0, "reserved_mib": 225.0},
    }
    assert realized["line_emotion_audit"] == "cheerful"
    assert realized["perth_watermark_stage_executed"] is True


@pytest.mark.parametrize(
    ("phase", "message"),
    [
        ("load", "runtime load で CUDA out of memory"),
        ("generate", "generation .* CUDA out of memory"),
    ],
)
def test_oom_is_phase_specific(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    message: str,
) -> None:
    root = _model_root(tmp_path, monkeypatch)
    runtime = FakeRuntime()
    runtime.oom_on = phase
    adapter = ChatterboxAdapter(runtime=runtime, model_root=root)
    job = _job()
    adapter.prepare([job], tmp_path / "artifacts", _voices_dir(tmp_path))

    with pytest.raises(ChatterboxAdapterError, match=message):
        adapter.generate(job, tmp_path / "output.wav")


@pytest.mark.parametrize(
    "waveform",
    [[], [[math.nan]], [[math.inf]], [["not-a-number"]]],
)
def test_invalid_waveform_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    waveform: Any,
) -> None:
    root = _model_root(tmp_path, monkeypatch)
    runtime = FakeRuntime()
    runtime.waveform = waveform
    adapter = ChatterboxAdapter(runtime=runtime, model_root=root)
    job = _job()
    adapter.prepare([job], tmp_path / "artifacts", _voices_dir(tmp_path))

    with pytest.raises(ChatterboxAdapterError, match="waveform"):
        adapter.generate(job, tmp_path / "output.wav")


def test_prepare_gate_duplicate_locale_and_unknown_job(
    tmp_path: Path,
) -> None:
    voices_dir = _voices_dir(tmp_path)
    adapter = ChatterboxAdapter(runtime=FakeRuntime())
    job = _job()
    with pytest.raises(ChatterboxAdapterError, match=r"prepare\(\)"):
        adapter.generation_input(job)
    with pytest.raises(ChatterboxAdapterError, match="重複"):
        adapter.prepare([job, job], tmp_path / "artifacts", voices_dir)
    with pytest.raises(ChatterboxAdapterError, match="Japanese 固定"):
        adapter.prepare(
            [_job(locale="en")],
            tmp_path / "artifacts",
            voices_dir,
        )
    adapter.prepare([job], tmp_path / "artifacts", voices_dir)
    with pytest.raises(ChatterboxAdapterError, match="prepare 済み"):
        adapter.generation_input(_job(line_id="other"))


def test_model_inventory_requires_exact_local_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _model_root(tmp_path, monkeypatch)
    (root / "unexpected.bin").write_bytes(b"unexpected")
    adapter = ChatterboxAdapter(runtime=FakeRuntime(), model_root=root)
    job = _job()
    adapter.prepare([job], tmp_path / "artifacts", _voices_dir(tmp_path))

    with pytest.raises(ChatterboxAdapterError, match="inventory"):
        adapter.generate(job, tmp_path / "output.wav")


def test_native_runtime_cangjie_download_is_local_and_restored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gaya_pipeline.adapters.chatterbox as module

    snapshot = tmp_path.resolve()
    cangjie = snapshot / "Cangjie5_TC.json"
    cangjie.write_text("mapping", encoding="utf-8")
    original = object()

    class FakeConverter:
        def _init_segmenter(self) -> None:
            raise AssertionError("online segmenter initialization must not run")

    original_segmenter_init = FakeConverter._init_segmenter
    tokenizer_module = SimpleNamespace(
        hf_hub_download=original,
        ChineseCangjieConverter=FakeConverter,
    )

    class FakeModelClass:
        @classmethod
        def from_local(
            cls,
            root: Path,
            *,
            device: str,
            t3_model: str,
        ) -> Any:
            assert root == snapshot
            assert device == DEVICE
            assert t3_model == "v3"
            path = tokenizer_module.hf_hub_download(
                repo_id="ResembleAI/chatterbox",
                filename="Cangjie5_TC.json",
                cache_dir=root,
            )
            assert path == str(cangjie)
            converter = FakeConverter()
            converter._init_segmenter()
            converter.word2cj = {"字": "code"}
            return SimpleNamespace(
                tokenizer=SimpleNamespace(
                    cangjie_converter=converter,
                ),
            )

    runtime = module._NativeRuntime()
    runtime._torch = SimpleNamespace()
    runtime._model_class = FakeModelClass
    runtime._tokenizer_module = tokenizer_module

    model = runtime.load_model(snapshot)
    assert model.tokenizer.cangjie_converter.word2cj
    assert tokenizer_module.hf_hub_download is original
    assert FakeConverter._init_segmenter is original_segmenter_init


def test_native_runtime_rejects_non_python_312(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gaya_pipeline.adapters.chatterbox as module

    monkeypatch.setattr(module.sys, "version_info", (3, 13, 0))

    with pytest.raises(ChatterboxAdapterError, match="Python 3.12"):
        module._NativeRuntime()._load_dependencies()


def test_perth_bundle_requires_exact_inventory_and_hparams_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gaya_pipeline.adapters.chatterbox as module

    contents = {
        "hparams.yaml": b"sample_rate: 32000\n",
        "id.txt": b"fixed-id\n",
        "perth_net_250000.pth.tar": b"checkpoint",
    }
    specs = {
        name: (len(content), hashlib.sha256(content).hexdigest())
        for name, content in contents.items()
    }
    monkeypatch.setattr(module, "PERTH_BUNDLE_FILE_SPECS", specs)

    package_root = tmp_path / "perth"
    pretrained_dir = package_root / module.PERTH_PRETRAINED_RELATIVE_DIR
    pretrained_dir.mkdir(parents=True)
    for name, content in contents.items():
        (pretrained_dir / name).write_bytes(content)

    module._validate_perth_bundle(package_root)

    extra_checkpoint = pretrained_dir / "perth_net_999999.pth.tar"
    extra_checkpoint.write_bytes(b"unverified")
    with pytest.raises(ChatterboxAdapterError, match="inventory"):
        module._validate_perth_bundle(package_root)

    extra_checkpoint.unlink()
    (pretrained_dir / "hparams.yaml").write_bytes(
        b"x" * len(contents["hparams.yaml"]),
    )
    with pytest.raises(ChatterboxAdapterError, match="hparams.yaml.*SHA-256"):
        module._validate_perth_bundle(package_root)


def test_model_root_environment_is_required_and_absolute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gaya_pipeline.adapters.chatterbox as module

    monkeypatch.delenv(MODEL_ROOT_ENV, raising=False)
    with pytest.raises(ChatterboxAdapterError, match=MODEL_ROOT_ENV):
        module._model_root_from_environment()
    monkeypatch.setenv(MODEL_ROOT_ENV, "relative")
    with pytest.raises(ChatterboxAdapterError, match="絶対パス"):
        module._model_root_from_environment()
