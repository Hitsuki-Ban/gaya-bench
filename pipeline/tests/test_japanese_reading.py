from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from gaya_pipeline.japanese_reading import (
    AMBIGUOUS_JAPANESE_READINGS,
    EXPLICIT_READING_SOURCE,
    PYOPENJTALK_READING_SOURCE,
    contains_japanese_ideograph,
    find_ambiguous_japanese_readings,
    JapaneseReading,
    JapaneseReadingError,
    normalize_japanese_reading,
    resolve_japanese_reading,
)


def test_explicit_reading_has_priority_and_is_preserved_verbatim() -> None:
    def converter(_text: str) -> str:
        raise AssertionError("explicit reading must not invoke converter")

    result = resolve_japanese_reading(
        text="珈琲、飲む？😊",
        reading=" コーヒー、ノム？😊 ",
        converter=converter,
    )

    assert result == JapaneseReading(
        text=" コーヒー、ノム？😊 ",
        source=EXPLICIT_READING_SOURCE,
    )
    assert result.as_dict() == {
        "text": " コーヒー、ノム？😊 ",
        "source": "line.reading",
    }
    assert isinstance(hash(result), int)


@pytest.mark.parametrize("reading", ["", " ", "\t\r\n"])
def test_explicit_empty_reading_fails_fast(reading: str) -> None:
    with pytest.raises(JapaneseReadingError, match="空にできません"):
        resolve_japanese_reading(
            text="こんにちは。",
            reading=reading,
            converter=lambda text: text,
        )


def test_explicit_non_string_reading_fails_fast() -> None:
    with pytest.raises(JapaneseReadingError, match="null または文字列"):
        resolve_japanese_reading(
            text="こんにちは。",
            reading=42,
            converter=lambda text: text,
        )


@pytest.mark.parametrize("include_null", [False, True])
def test_missing_or_null_reading_uses_injected_converter(
    include_null: bool,
) -> None:
    received: list[str] = []

    def converter(text: str) -> str:
        received.append(text)
        return "コーヒー、ノム？😊"

    if include_null:
        result = resolve_japanese_reading(
            text="珈琲、飲む？😊",
            reading=None,
            converter=converter,
        )
    else:
        result = resolve_japanese_reading(
            text="珈琲、飲む？😊",
            converter=converter,
        )

    assert received == ["珈琲、飲む？😊"]
    assert result == JapaneseReading(
        text="コーヒー、ノム？😊",
        source=PYOPENJTALK_READING_SOURCE,
    )


def test_default_converter_lazily_calls_pyopenjtalk_with_kana_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    def g2p(text: str, *, kana: bool) -> str:
        calls.append((text, kana))
        return "セール、カイサイチュー！😊"

    monkeypatch.setitem(
        sys.modules,
        "pyopenjtalk",
        SimpleNamespace(g2p=g2p),
    )

    result = resolve_japanese_reading(
        text="セール、開催中！😊",
        reading=None,
    )

    assert calls == [("セール、開催中！😊", True)]
    assert result.as_dict() == {
        "text": "セール、カイサイチュー！😊",
        "source": "pyopenjtalk.g2p(kana=True)",
    }


@pytest.mark.parametrize(
    "text",
    [
        None,
        1,
        "",
        " \t",
    ],
)
def test_invalid_text_fails_fast(text: object) -> None:
    with pytest.raises(JapaneseReadingError, match="line.text"):
        resolve_japanese_reading(text=text, converter=lambda value: value)


def test_invalid_text_fails_even_when_explicit_reading_is_present() -> None:
    with pytest.raises(JapaneseReadingError, match="line.text"):
        resolve_japanese_reading(text="", reading="ヨミ")


@pytest.mark.parametrize("converted", [None, 1, "", " \r\n"])
def test_invalid_converter_result_fails_fast(converted: object) -> None:
    with pytest.raises(JapaneseReadingError, match="仮名変換結果"):
        resolve_japanese_reading(
            text="こんにちは。",
            converter=lambda _text: converted,  # type: ignore[return-value]
        )


def test_converter_failure_is_reported_without_an_alternate_path() -> None:
    def broken_converter(_text: str) -> str:
        raise RuntimeError("dictionary unavailable")

    with pytest.raises(
        JapaneseReadingError,
        match="仮名変換に失敗.*dictionary unavailable",
    ):
        resolve_japanese_reading(
            text="こんにちは。",
            converter=broken_converter,
        )


def test_ambiguous_reading_catalog_finds_only_present_surfaces() -> None:
    result = find_ambiguous_japanese_readings(
        "麻婆は辛いが、大分県の人気店だ。",
    )

    assert [(item.surface, item.candidates) for item in result] == [
        ("辛い", AMBIGUOUS_JAPANESE_READINGS["辛い"]),
        ("人気", AMBIGUOUS_JAPANESE_READINGS["人気"]),
        ("大分", AMBIGUOUS_JAPANESE_READINGS["大分"]),
    ]
    assert find_ambiguous_japanese_readings(None) == ()


def test_reading_normalization_preserves_pronunciation_not_punctuation() -> None:
    assert (
        normalize_japanese_reading(" うちのマーボーわ、からいよ！ ")
        == "ウチノマーボーワカライヨ"
    )
    assert contains_japanese_ideograph("麻婆は辛い")
    assert not contains_japanese_ideograph("マーボーワカライ")
