from __future__ import annotations

import hashlib
import json
import subprocess
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from gaya_pipeline import cli
from gaya_pipeline.public_audio import (
    PublicAudioError,
    PublicAudioSummary,
    verify_public_audio,
)


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "audio/ogg",
    ) -> None:
        self.body = body
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _write_manifest(
    path: Path,
    *,
    selected_body: bytes = b"selected opus",
    include_skipped: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    def candidate(take_id: str, body: bytes, line: str) -> dict[str, Any]:
        return {
            "take_id": take_id,
            "model": "model-a",
            "scenario": "scenario-a",
            "line": line,
            "variant": "dry",
            "path": f"audio/takes/model-a/scenario-a/{line}/dry/take.opus",
            "sha256": hashlib.sha256(body).hexdigest(),
        }

    selected = candidate("1" * 64, selected_body, "line-a")
    candidates = [selected]
    curations = [
        {
            "take_id": selected["take_id"],
            "model": selected["model"],
            "scenario": selected["scenario"],
            "line": selected["line"],
            "variant": selected["variant"],
            "decision": "selected",
        },
    ]
    skipped = candidate("2" * 64, b"skipped opus", "line-b")
    if include_skipped:
        candidates.append(skipped)
        curations.append(
            {
                "take_id": skipped["take_id"],
                "model": skipped["model"],
                "scenario": skipped["scenario"],
                "line": skipped["line"],
                "variant": skipped["variant"],
                "decision": "skipped",
            },
        )
    path.write_text(
        json.dumps(
            {
                "format_version": 4,
                "candidates": candidates,
                "curations": curations,
            },
        ),
        encoding="utf-8",
    )
    return selected, skipped


def test_selectedだけを取得しshaと完全decodeを検証する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"selected opus"
    manifest = tmp_path / "manifest.json"
    selected, skipped = _write_manifest(manifest, selected_body=body)
    requests: list[tuple[str, float]] = []
    calls: list[dict[str, Any]] = []

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        requests.append((request.full_url, timeout))
        return FakeResponse(body)

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, stderr=b"")

    monkeypatch.setattr(
        "gaya_pipeline.public_audio.shutil.which",
        lambda _name: "C:/tools/ffmpeg.exe",
    )
    monkeypatch.setattr("gaya_pipeline.public_audio.urlopen", fake_urlopen)
    monkeypatch.setattr("gaya_pipeline.public_audio.subprocess.run", fake_run)

    summary = verify_public_audio(
        manifest_path=manifest,
        base_url="https://audio.example/",
        workers=1,
        timeout_seconds=12.0,
    )

    assert summary.verified_count == 1
    assert summary.total_bytes == len(body)
    assert requests == [
        (f"https://audio.example/{selected['path']}", 12.0),
    ]
    assert skipped["path"] not in requests[0][0]
    assert calls[0]["command"] == [
        "C:/tools/ffmpeg.exe",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-xerror",
        "-abort_on",
        "empty_output",
        "-i",
        "pipe:0",
        "-map",
        "0:a:0",
        "-f",
        "null",
        "-",
    ]
    assert calls[0]["input"] == body
    assert calls[0]["timeout"] == 12.0


@pytest.mark.parametrize(
    ("response", "decode_code", "message"),
    [
        (FakeResponse(b"selected opus", status=206), 0, "HTTP status"),
        (
            FakeResponse(b"selected opus", content_type="application/octet-stream"),
            0,
            "Content-Type",
        ),
        (FakeResponse(b"changed"), 0, "SHA-256"),
        (FakeResponse(b"selected opus"), 1, "FFmpeg decode"),
    ],
)
def test_public_audioの不整合を失敗にする(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: FakeResponse,
    decode_code: int,
    message: str,
) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    monkeypatch.setattr(
        "gaya_pipeline.public_audio.shutil.which",
        lambda _name: "ffmpeg",
    )
    monkeypatch.setattr(
        "gaya_pipeline.public_audio.urlopen",
        lambda *_args, **_kwargs: response,
    )
    monkeypatch.setattr(
        "gaya_pipeline.public_audio.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            decode_code,
            stderr=b"decode error",
        ),
    )

    with pytest.raises(PublicAudioError, match=message):
        verify_public_audio(
            manifest_path=manifest,
            base_url="https://audio.example/",
            workers=1,
            timeout_seconds=10.0,
        )


@pytest.mark.parametrize("mutation", ["unknown", "group-mismatch"])
def test_selected_curation不整合はnetwork前に拒否する(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if mutation == "unknown":
        document["curations"][0]["take_id"] = "f" * 64
    else:
        document["curations"][0]["line"] = "different"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(
        "gaya_pipeline.public_audio.shutil.which",
        lambda _name: "ffmpeg",
    )
    monkeypatch.setattr(
        "gaya_pipeline.public_audio.urlopen",
        lambda *_args, **_kwargs: pytest.fail("network must not be called"),
    )

    with pytest.raises(PublicAudioError):
        verify_public_audio(
            manifest_path=manifest,
            base_url="https://audio.example/",
            workers=1,
            timeout_seconds=10.0,
        )


@pytest.mark.parametrize(
    "argv",
    [
        ["launch", "verify-audio"],
        ["launch", "verify-audio", "--manifest", "manifest.json"],
    ],
)
def test_launch_verify_audio_cliはmanifestとbase_urlを必須にする(
    argv: list[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(argv)
    assert error.value.code == 2


def test_launch_verify_audio_cliは明示引数を渡す(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.json"
    captured: dict[str, Any] = {}

    def fake_verify(**kwargs: Any) -> PublicAudioSummary:
        captured.update(kwargs)
        return PublicAudioSummary(
            verified_count=3,
            total_bytes=123,
            elapsed_seconds=1.5,
        )

    monkeypatch.setattr(cli, "verify_public_audio", fake_verify)
    result = cli.main(
        [
            "launch",
            "verify-audio",
            "--manifest",
            str(manifest),
            "--base-url",
            "https://audio.example/",
            "--workers",
            "4",
            "--timeout-seconds",
            "15",
        ],
    )

    assert result == 0
    assert captured == {
        "manifest_path": manifest,
        "base_url": "https://audio.example/",
        "workers": 4,
        "timeout_seconds": 15.0,
    }
