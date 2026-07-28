from __future__ import annotations

import importlib
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass


class JapaneseReadingError(ValueError):
    """Raised when a Japanese reading cannot be resolved."""


@dataclass(frozen=True)
class JapaneseReading:
    text: str
    source: str

    def as_dict(self) -> dict[str, str]:
        return {
            "text": self.text,
            "source": self.source,
        }


ReadingConverter = Callable[[str], str]

EXPLICIT_READING_SOURCE = "line.reading"
PYOPENJTALK_READING_SOURCE = "pyopenjtalk.g2p(kana=True)"

AMBIGUOUS_JAPANESE_READINGS: dict[str, tuple[str, ...]] = {
    "辛い": ("カライ", "ツライ"),
    "行った": ("イッタ", "オコナッタ"),
    "人気": ("ニンキ", "ヒトケ"),
    "大分": ("ダイブン", "ダイブ", "オオイタ"),
}


@dataclass(frozen=True)
class AmbiguousJapaneseReading:
    surface: str
    candidates: tuple[str, ...]


def resolve_japanese_reading(
    *,
    text: object,
    reading: object = None,
    converter: ReadingConverter | None = None,
) -> JapaneseReading:
    """Resolve the exact Japanese text sent to a kana-oriented TTS model."""

    if not isinstance(text, str) or not text.strip():
        raise JapaneseReadingError("line.text は空でない文字列である必要があります。")

    if reading is not None:
        if not isinstance(reading, str):
            raise JapaneseReadingError(
                "line.reading は null または文字列である必要があります。",
            )
        if not reading.strip():
            raise JapaneseReadingError(
                "line.reading が指定された場合は空にできません。",
            )
        return JapaneseReading(
            text=reading,
            source=EXPLICIT_READING_SOURCE,
        )

    selected_converter = converter
    if selected_converter is None:
        selected_converter = _pyopenjtalk_kana

    try:
        converted = selected_converter(text)
    except JapaneseReadingError:
        raise
    except Exception as error:
        raise JapaneseReadingError(
            f"line.text の仮名変換に失敗しました: {error}",
        ) from error

    if not isinstance(converted, str) or not converted.strip():
        raise JapaneseReadingError(
            "仮名変換結果は空でない文字列である必要があります。",
        )
    return JapaneseReading(
        text=converted,
        source=PYOPENJTALK_READING_SOURCE,
    )


def find_ambiguous_japanese_readings(
    text: object,
) -> tuple[AmbiguousJapaneseReading, ...]:
    if not isinstance(text, str):
        return ()
    return tuple(
        AmbiguousJapaneseReading(surface=surface, candidates=candidates)
        for surface, candidates in AMBIGUOUS_JAPANESE_READINGS.items()
        if surface in text
    )


def contains_japanese_ideograph(text: str) -> bool:
    return any(
        "\u3400" <= character <= "\u4dbf"
        or "\u4e00" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        for character in text
    )


def normalize_japanese_reading(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    output: list[str] = []
    for character in normalized:
        codepoint = ord(character)
        if 0x3041 <= codepoint <= 0x3096:
            character = chr(codepoint + 0x60)
        if (
            "\u30a1" <= character <= "\u30fa"
            or character in {"ー", "ヽ", "ヾ"}
            or character.isascii()
            and character.isalnum()
        ):
            output.append(character.upper())
    return "".join(output)


def _pyopenjtalk_kana(text: str) -> str:
    try:
        pyopenjtalk = importlib.import_module("pyopenjtalk")
    except ImportError as error:
        raise JapaneseReadingError(
            "仮名変換には pyopenjtalk が必要です。",
        ) from error
    return pyopenjtalk.g2p(text, kana=True)
