from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import shutil
import sys
import tempfile
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

PASQA_CODE_COMMIT = "bdbd3f84049b1ff3925e27888949831fc1977413"
PASQA_HF_REPO = "ly-corporation/PASQA"
PASQA_WEIGHTS_REVISION = "7fe0bfc7dff16991599043bcafb886c7d597419a"
PASQA_CHECKPOINT_NAME = "checkpoint-100000steps.pkl"
PASQA_CONFIG_NAME = "config.yml"
PASQA_VOCAB_NAME = "vocab.txt"
PASQA_PACKAGE_VERSION = "0.1.0"
TORCH_VERSION = "2.8.0"
TORCHAUDIO_VERSION = "2.8.0"
TARGET_SAMPLE_RATE = 16_000
MIN_SAMPLES = 1_040
MAX_SAMPLES = TARGET_SAMPLE_RATE * 10
TAKE_ID_LENGTH = 64

MODEL_FILE_SHA256 = {
    PASQA_CHECKPOINT_NAME: (
        "03c9e8880a28f65fd9b8611f3fe3e179020b067d892cd6f6a4c311572b8a8bc7"
    ),
    PASQA_CONFIG_NAME: (
        "492540b39f77f42ad3f20fc2bac0d604cd885151931c6726539ca8b6d2d1393b"
    ),
    PASQA_VOCAB_NAME: (
        "0f816a4669c0cb22d9af80b7cb20414995971039f4a6db4330098c26218d6841"
    ),
}
PASQA_VOCAB_URL = (
    "https://raw.githubusercontent.com/lycorp-jp/PASQA/"
    f"{PASQA_CODE_COMMIT}/src/pasqa/vocab.txt"
)


class RankingError(RuntimeError):
    """入力契約または PASQA 実行境界の違反。"""


class Predictor(Protocol):
    mora_vocab: Mapping[str, int]

    def predict(self, *, mora: list[str], wav_path: Path) -> Mapping[str, Any]:
        """PASQA 推論を1回実行する。"""


PredictorFactory = Callable[[Path, Path], Predictor]


@dataclass(frozen=True)
class ModelFiles:
    checkpoint: Path
    config: Path
    vocab: Path


@dataclass(frozen=True)
class AudioInput:
    take_id: str
    audio_path_text: str
    audio_path: Path
    audio_sha256: str
    frames: int
    duration_seconds: float


def prepare_model_dir(model_dir: Path) -> ModelFiles:
    _require_python_310()
    model_dir = model_dir.resolve()
    model_dir.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import hf_hub_download
    except ImportError as error:
        raise RankingError(
            "huggingface-hub がありません。pasqa-ranking を uv sync してください。"
        ) from error

    for filename in (PASQA_CHECKPOINT_NAME, PASQA_CONFIG_NAME):
        target = model_dir / filename
        if target.exists():
            _verify_file_hash(target, MODEL_FILE_SHA256[filename])
            continue
        source = Path(
            hf_hub_download(
                repo_id=PASQA_HF_REPO,
                filename=filename,
                revision=PASQA_WEIGHTS_REVISION,
            )
        )
        _verify_file_hash(source, MODEL_FILE_SHA256[filename])
        _copy_new_file(source, target)

    vocab_target = model_dir / PASQA_VOCAB_NAME
    if vocab_target.exists():
        _verify_file_hash(vocab_target, MODEL_FILE_SHA256[PASQA_VOCAB_NAME])
    else:
        try:
            with urllib.request.urlopen(PASQA_VOCAB_URL, timeout=30) as response:
                vocab_bytes = response.read()
        except OSError as error:
            raise RankingError(f"PASQA vocab の取得に失敗しました: {error}") from error
        actual = hashlib.sha256(vocab_bytes).hexdigest()
        expected = MODEL_FILE_SHA256[PASQA_VOCAB_NAME]
        if actual != expected:
            raise RankingError(
                f"PASQA vocab SHA-256 が不一致です: expected={expected} actual={actual}"
            )
        _write_new_bytes(vocab_target, vocab_bytes)

    return validate_model_dir(model_dir)


def validate_model_dir(model_dir: Path) -> ModelFiles:
    model_dir = model_dir.resolve()
    files = ModelFiles(
        checkpoint=model_dir / PASQA_CHECKPOINT_NAME,
        config=model_dir / PASQA_CONFIG_NAME,
        vocab=model_dir / PASQA_VOCAB_NAME,
    )
    for path in (files.checkpoint, files.config, files.vocab):
        if not path.is_file():
            raise RankingError(
                f"PASQA model file がありません: {path}。"
                "明示的に gaya-pasqa prepare を実行してください。"
            )
        _verify_file_hash(path, MODEL_FILE_SHA256[path.name])
    return files


def run_ranking(
    *,
    model_dir: Path,
    input_path: Path,
    output_path: Path,
    predictor_factory: PredictorFactory | None = None,
) -> dict[str, Any]:
    _require_python_310()
    _require_runtime_versions()
    model_files = validate_model_dir(model_dir)
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if not input_path.is_file():
        raise RankingError(f"ranking input がありません: {input_path}")
    if output_path.exists():
        raise RankingError(f"ranking output は既に存在します: {output_path}")

    raw_input = input_path.read_bytes()
    try:
        document = json.loads(raw_input)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RankingError(f"ranking input JSON が不正です: {error}") from error
    group, mora_tokens, audio_inputs = _validate_input_document(
        document,
        input_dir=input_path.parent,
    )

    expected_vocab = _load_vocab(model_files.vocab)
    unknown_tokens = sorted(set(mora_tokens) - set(expected_vocab))
    if unknown_tokens:
        rendered = ", ".join(repr(token) for token in unknown_tokens)
        raise RankingError(f"固定 PASQA vocab にない mora token です: {rendered}")

    factory = predictor_factory or _create_predictor
    predictor = factory(model_files.checkpoint, model_files.config)
    if dict(predictor.mora_vocab) != expected_vocab:
        raise RankingError(
            "PASQA が使用した mora vocab が明示 vocab と一致しません。"
            "tokenizer fallback の可能性があるため停止します。"
        )

    scored: list[dict[str, Any]] = []
    for index, audio in enumerate(audio_inputs):
        result = predictor.predict(mora=mora_tokens, wav_path=audio.audio_path)
        current_audio_sha256 = _sha256_file(audio.audio_path)
        if current_audio_sha256 != audio.audio_sha256:
            raise RankingError(
                f"{audio.take_id}: 音声が検査後に変更されました。"
                "provenance を確定できないため停止します。"
            )
        score = result.get("mos")
        frame_length = result.get("frame_lengths")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise RankingError(f"{audio.take_id}: PASQA mos が数値ではありません。")
        score = float(score)
        if not math.isfinite(score):
            raise RankingError(f"{audio.take_id}: PASQA mos が有限値ではありません。")
        if isinstance(frame_length, bool) or not isinstance(frame_length, int):
            raise RankingError(
                f"{audio.take_id}: PASQA frame_lengths が整数ではありません。"
            )
        if frame_length < 1:
            raise RankingError(
                f"{audio.take_id}: PASQA frame_lengths は1以上が必要です。"
            )
        scored.append(
            {
                "input_index": index,
                "take_id": audio.take_id,
                "audio_path": audio.audio_path_text,
                "audio_sha256": audio.audio_sha256,
                "frames": audio.frames,
                "duration_seconds": audio.duration_seconds,
                "score": score,
                "pasqa_frame_length": frame_length,
            }
        )

    scored.sort(key=lambda item: (-item["score"], item["take_id"]))
    rankings = [{"rank": rank, **item} for rank, item in enumerate(scored, start=1)]
    output = {
        "format_version": 1,
        "kind": "pasqa_same_line_take_ranking",
        "usage": "ranking_only_no_absolute_threshold",
        "ranking_policy": "score_descending_then_take_id_ascending",
        "group": group,
        "mora_tokens": mora_tokens,
        "rankings": rankings,
        "provenance": {
            "input_sha256": hashlib.sha256(raw_input).hexdigest(),
            "pasqa": {
                "code_commit": PASQA_CODE_COMMIT,
                "package_version": PASQA_PACKAGE_VERSION,
                "weights_repo": PASQA_HF_REPO,
                "weights_revision": PASQA_WEIGHTS_REVISION,
                "checkpoint_sha256": MODEL_FILE_SHA256[PASQA_CHECKPOINT_NAME],
                "config_sha256": MODEL_FILE_SHA256[PASQA_CONFIG_NAME],
                "vocab_sha256": MODEL_FILE_SHA256[PASQA_VOCAB_NAME],
            },
            "runtime": {
                "python": _python_version(),
                "torch": importlib.metadata.version("torch"),
                "torchaudio": importlib.metadata.version("torchaudio"),
                "device": "cpu",
            },
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_new_bytes(
        output_path,
        (json.dumps(output, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return output


def _validate_input_document(
    document: Any,
    *,
    input_dir: Path,
) -> tuple[dict[str, str], list[str], list[AudioInput]]:
    if not isinstance(document, dict):
        raise RankingError("ranking input は JSON object が必要です。")
    if set(document) != {"format_version", "group", "mora_tokens", "takes"}:
        raise RankingError("ranking input の項目が v1 契約と一致しません。")
    if document["format_version"] != 1:
        raise RankingError("ranking input format_version は 1 が必要です。")

    group = document["group"]
    expected_group_keys = {"scenario_id", "line_id", "model_id", "variant"}
    if not isinstance(group, dict) or set(group) != expected_group_keys:
        raise RankingError("ranking input group の項目が v1 契約と一致しません。")
    normalized_group: dict[str, str] = {}
    for key in sorted(expected_group_keys):
        value = group[key]
        if not isinstance(value, str) or not value.strip():
            raise RankingError(
                f"ranking input group.{key} は空でない文字列が必要です。"
            )
        normalized_group[key] = value

    mora_tokens = document["mora_tokens"]
    if (
        not isinstance(mora_tokens, list)
        or not mora_tokens
        or len(mora_tokens) > 128
        or any(not isinstance(token, str) or not token for token in mora_tokens)
    ):
        raise RankingError("mora_tokens は1〜128個の空でない文字列配列が必要です。")

    takes = document["takes"]
    if not isinstance(takes, list) or len(takes) < 2:
        raise RankingError("同一 group の take が2件以上必要です。")
    take_ids: set[str] = set()
    audio_paths: set[Path] = set()
    audio_inputs: list[AudioInput] = []
    for index, take in enumerate(takes):
        if not isinstance(take, dict) or set(take) != {"take_id", "audio_path"}:
            raise RankingError(f"takes[{index}] の項目が v1 契約と一致しません。")
        take_id = take["take_id"]
        if (
            not isinstance(take_id, str)
            or len(take_id) != TAKE_ID_LENGTH
            or any(character not in "0123456789abcdef" for character in take_id)
        ):
            raise RankingError(f"takes[{index}].take_id は小文字64桁hexが必要です。")
        if take_id in take_ids:
            raise RankingError(f"take_id が重複しています: {take_id}")
        take_ids.add(take_id)

        audio_path_text = take["audio_path"]
        if not isinstance(audio_path_text, str) or not audio_path_text.strip():
            raise RankingError(
                f"takes[{index}].audio_path は空でない文字列が必要です。"
            )
        raw_path = Path(audio_path_text)
        audio_path = (
            raw_path if raw_path.is_absolute() else input_dir / raw_path
        ).resolve()
        if audio_path in audio_paths:
            raise RankingError(f"audio_path が重複しています: {audio_path_text}")
        audio_paths.add(audio_path)
        audio_inputs.append(
            _inspect_audio(
                take_id=take_id,
                audio_path_text=audio_path_text,
                audio_path=audio_path,
            )
        )
    return normalized_group, list(mora_tokens), audio_inputs


def _inspect_audio(
    *,
    take_id: str,
    audio_path_text: str,
    audio_path: Path,
) -> AudioInput:
    if not audio_path.is_file():
        raise RankingError(f"{take_id}: 音声ファイルがありません: {audio_path}")
    try:
        import soundfile

        info = soundfile.info(audio_path)
    except Exception as error:
        raise RankingError(f"{take_id}: 音声を検査できません: {error}") from error
    if info.channels != 1:
        raise RankingError(f"{take_id}: mono 音声が必要です: channels={info.channels}")
    if info.samplerate != TARGET_SAMPLE_RATE:
        raise RankingError(
            f"{take_id}: 16 kHz 音声が必要です: samplerate={info.samplerate}"
        )
    if info.frames < MIN_SAMPLES or info.frames > MAX_SAMPLES:
        raise RankingError(
            f"{take_id}: sample数は {MIN_SAMPLES}〜{MAX_SAMPLES} が必要です: "
            f"frames={info.frames}"
        )
    return AudioInput(
        take_id=take_id,
        audio_path_text=audio_path_text,
        audio_path=audio_path,
        audio_sha256=_sha256_file(audio_path),
        frames=info.frames,
        duration_seconds=info.frames / TARGET_SAMPLE_RATE,
    )


def _create_predictor(checkpoint: Path, config: Path) -> Predictor:
    try:
        from pasqa import PasqaPredictor
    except ImportError as error:
        raise RankingError(
            "PASQA がありません。pasqa-ranking を uv sync してください。"
        ) from error
    return PasqaPredictor(
        checkpoint=checkpoint,
        config=config,
        device="cpu",
    )


def _load_vocab(path: Path) -> dict[str, int]:
    tokens = [
        line.rstrip("\n")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if not tokens or len(tokens) != len(set(tokens)):
        raise RankingError("PASQA vocab が空、または token が重複しています。")
    return {token: index for index, token in enumerate(tokens)}


def _require_python_310() -> None:
    if sys.version_info[:2] != (3, 10):
        raise RankingError(
            f"pasqa-ranking は Python 3.10 専用です: current={_python_version()}"
        )


def _require_runtime_versions() -> None:
    expected = {
        "pasqa": PASQA_PACKAGE_VERSION,
        "torch": TORCH_VERSION,
        "torchaudio": TORCHAUDIO_VERSION,
    }
    for package, version in expected.items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as error:
            raise RankingError(
                f"{package} がありません。pasqa-ranking を uv sync してください。"
            ) from error
        if actual.split("+", 1)[0] != version:
            raise RankingError(
                f"{package} version が固定値と一致しません: "
                f"expected={version} actual={actual}"
            )


def _python_version() -> str:
    return ".".join(str(part) for part in sys.version_info[:3])


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file_hash(path: Path, expected: str) -> None:
    actual = _sha256_file(path)
    if actual != expected:
        raise RankingError(
            f"SHA-256 が不一致です: {path} expected={expected} actual={actual}"
        )


def _copy_new_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RankingError(f"既存ファイルを上書きしません: {target}")
    temporary_path: Path | None = None
    with tempfile.NamedTemporaryFile(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".partial",
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with (
            source.open("rb") as source_stream,
            temporary_path.open("wb") as temporary_stream,
        ):
            shutil.copyfileobj(source_stream, temporary_stream)
            temporary_stream.flush()
            os.fsync(temporary_stream.fileno())
        _commit_new_file(temporary_path, target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _write_new_bytes(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RankingError(f"既存ファイルを上書きしません: {target}")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".partial",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        _commit_new_file(temporary_path, target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _commit_new_file(temporary_path: Path, target: Path) -> None:
    try:
        os.link(temporary_path, target)
    except FileExistsError as error:
        raise RankingError(f"既存ファイルを上書きしません: {target}") from error
    except OSError as error:
        raise RankingError(
            f"新規ファイルを確定できません: {target}: {error}"
        ) from error
