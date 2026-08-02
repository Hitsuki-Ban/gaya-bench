from __future__ import annotations

import argparse
import dataclasses
import functools
import hashlib
import json
import statistics
import wave
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from gaya_pipeline.adapters.base import LineJob, TakeContext
from gaya_pipeline.audio import PostprocessProfile, find_audio_tools, normalize_wav
from gaya_pipeline.japanese_reading import (
    normalize_japanese_reading,
    resolve_japanese_reading,
)
from gaya_pipeline.qc import count_japanese_mora


FORMAT_VERSION = 1
PROTOCOL = "issue-190-ab-canary-v1"
RELEASE_CANDIDATE_SET_SHA256 = (
    "5287cee156f8212c8249f202931717dc4ef448410fc3c83371650b6d5ff28fdd"
)

IRODORI = "irodori-tts-600m-v3-voicedesign"
SUPERTONIC = "supertonic-3"
AIVIS = "aivisspeech-kohaku"
COSYVOICE = "cosyvoice3-0.5b-2512"

IRODORI_TARGETS = (
    ("festival-night", "yatai-obasan-003"),
    ("goblin-camp", "orc-brother-001"),
    ("spirit-forest", "elder-tree-002"),
    ("festival-night", "matsuri-kid-003"),
    ("village-morning", "teen-boy-001"),
    ("spirit-forest", "wisp-003"),
    ("market-day", "fruit-vendor-002"),
    ("dungeon-entrance", "old-guide-002"),
    ("chinatown-chat", "mahjong-inkyo-003"),
    ("castle-gate", "guard-onna-003"),
    ("goblin-camp", "goblin-lookout-003"),
    ("spirit-forest", "wisp-001"),
)


class CanaryError(RuntimeError):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the deterministic Issue #190 A/B canaries.",
    )
    parser.add_argument("--track", choices=("irodori", "supertonic", "aivis", "cosyvoice"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--takes-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--anchor-selection", type=Path)
    parser.add_argument("--voices", type=Path)
    parser.add_argument("--model-root", type=Path)
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists():
        raise CanaryError(f"output must be a new path: {output}")

    manifest = _load_json(args.manifest)
    if manifest.get("candidate_set_sha256") != RELEASE_CANDIDATE_SET_SHA256:
        raise CanaryError("manifest candidate set is not the frozen Issue #190 release")
    jobs = _load_jobs(args.scenarios)
    selected = _selected_candidates(manifest)
    source_wavs = _source_wavs(args.takes_root, {item["take_id"] for item in selected})
    output.mkdir(parents=True)

    if args.track == "irodori":
        if args.anchor_selection is None:
            raise CanaryError("irodori requires --anchor-selection")
        entries = _generate_irodori(
            selected=selected,
            jobs=jobs,
            source_wavs=source_wavs,
            selection_path=args.anchor_selection.resolve(),
            output=output,
        )
    elif args.track == "supertonic":
        if args.model_root is None:
            raise CanaryError("supertonic requires --model-root")
        entries = _generate_supertonic(
            selected=selected,
            jobs=jobs,
            source_wavs=source_wavs,
            model_root=args.model_root.resolve(),
            output=output,
        )
    elif args.track == "aivis":
        entries = _generate_aivis(
            selected=selected,
            jobs=jobs,
            source_wavs=source_wavs,
            output=output,
        )
    else:
        if args.voices is None:
            raise CanaryError("cosyvoice requires --voices")
        entries = _generate_cosyvoice(
            selected=selected,
            jobs=jobs,
            source_wavs=source_wavs,
            voices=args.voices.resolve(),
            output=output,
        )

    report = {
        "format_version": FORMAT_VERSION,
        "protocol": PROTOCOL,
        "track": args.track,
        "candidate_set_sha256": RELEASE_CANDIDATE_SET_SHA256,
        "manifest": _receipt(args.manifest),
        "entry_count": len(entries),
        "entries": entries,
    }
    (output / "index.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _generate_irodori(
    *,
    selected: Sequence[Mapping[str, Any]],
    jobs: Mapping[tuple[str, str], LineJob],
    source_wavs: Mapping[str, Path],
    selection_path: Path,
    output: Path,
) -> list[dict[str, Any]]:
    from gaya_pipeline.adapters import irodori_tts

    selection = _load_json(selection_path)
    groups = selection.get("groups")
    if not isinstance(groups, list):
        raise CanaryError("anchor selection groups are invalid")
    runtime = irodori_tts._NativeRuntime()
    runtime.prepare()
    entries: list[dict[str, Any]] = []
    for key in IRODORI_TARGETS:
        job = _required_job(jobs, key)
        candidate = _required_candidate(selected, IRODORI, key)
        realized = _realized(candidate)
        seed = _required_int(realized.get("seed"), "Irodori seed")
        current_caption = _required_text(realized.get("caption"), "Irodori caption")
        reference_sha = _required_text(
            realized.get("reference_sha256"),
            "Irodori reference_sha256",
        )
        anchor = _selected_anchor(groups, job, reference_sha)
        reference_wav = (selection_path.parent / anchor["audio_path"]).resolve()
        if _sha256(reference_wav) != reference_sha:
            raise CanaryError(f"anchor SHA mismatch: {key}")
        emoji = irodori_tts.EMOTION_EMOJI[str(job.line["emotion"])]
        spoken_text = f"{emoji}{job.line['text']}" if emoji is not None else str(job.line["text"])
        variants = _irodori_caption_variants(current_caption)
        for variant, caption in variants.items():
            path = output / key[0] / key[1] / f"{variant}.wav"
            source_path = _source_path(path)
            runtime.synthesize(
                text=spoken_text,
                caption=caption,
                reference_wav=reference_wav,
                output_wav=source_path,
                seed=seed,
            )
            _normalize_generated(source_path, path)
            entry = _audio_entry(
                model=IRODORI,
                key=key,
                job=job,
                variant=variant,
                path=path,
                seed=seed,
                source_take_id=str(candidate["take_id"]),
                variables={"caption": caption},
            )
            if variant == "current":
                _attach_source_reproduction(entry, source_wavs, candidate)
            entries.append(entry)
    return entries


def _irodori_caption_variants(current: str) -> dict[str, str]:
    lines = current.splitlines()
    scene_index = next((index for index, line in enumerate(lines) if line.startswith("場面:")), None)
    emotion_line = next((line for line in lines if line.startswith("感情:")), None)
    if scene_index is None or emotion_line is None:
        raise CanaryError("current Irodori caption does not match the production contract")
    role_lines = lines[:scene_index]
    reduced_emotion = emotion_line.split("（強度", 1)[0]
    return {
        "current": current,
        "role-emotion": "\n".join((*role_lines, reduced_emotion)),
        "role-only": "\n".join(role_lines),
    }


def _generate_supertonic(
    *,
    selected: Sequence[Mapping[str, Any]],
    jobs: Mapping[tuple[str, str], LineJob],
    source_wavs: Mapping[str, Path],
    model_root: Path,
    output: Path,
) -> list[dict[str, Any]]:
    from gaya_pipeline.adapters import supertonic3

    targets = _supertonic_style_targets(selected, jobs)
    runtime = supertonic3._LocalRuntime()
    runtime.prepare(model_root)
    entries: list[dict[str, Any]] = []
    for candidate in targets:
        key = (str(candidate["scenario"]), str(candidate["line"]))
        job = _required_job(jobs, key)
        realized = _realized(candidate)
        seed = _required_int(realized.get("seed"), "Supertonic seed")
        style = _required_text(realized.get("voice_style"), "Supertonic voice_style")
        text = str(job.line["text"])
        for variant, speed in (("speed-1.05", 1.05), ("speed-1.00", 1.0)):
            path = output / style / key[0] / key[1] / f"{variant}.wav"
            source_path = _source_path(path)
            if speed == supertonic3.SPEED:
                runtime.synthesize(
                    text=text,
                    voice_style=style,
                    output_wav=source_path,
                    seed=seed,
                )
            else:
                _supertonic_synthesize_speed(
                    runtime=runtime,
                    module=supertonic3,
                    text=text,
                    voice_style=style,
                    speed=speed,
                    output_wav=source_path,
                    seed=seed,
                )
            _normalize_generated(source_path, path)
            entry = _audio_entry(
                model=SUPERTONIC,
                key=key,
                job=job,
                variant=variant,
                path=path,
                seed=seed,
                source_take_id=str(candidate["take_id"]),
                variables={"speed": speed, "voice_style": style},
            )
            if variant == "speed-1.05":
                _attach_source_reproduction(entry, source_wavs, candidate)
            entries.append(entry)
    return entries


def _supertonic_synthesize_speed(
    *,
    runtime: Any,
    module: Any,
    text: str,
    voice_style: str,
    speed: float,
    output_wav: Path,
    seed: int,
) -> None:
    if runtime._tts is None or runtime._numpy is None or runtime._soundfile is None:
        raise CanaryError("Supertonic runtime is not prepared")
    style = runtime._tts.get_voice_style(voice_style)
    runtime._numpy.random.seed(seed)
    waveform, durations = runtime._tts.synthesize(
        text,
        style,
        total_steps=module.TOTAL_STEPS,
        speed=speed,
        max_chunk_length=module.MAX_CHUNK_LENGTH,
        silence_duration=module.SILENCE_DURATION_SEC,
        lang=module.LANGUAGE_ID,
        verbose=False,
    )
    samples = runtime._numpy.asarray(waveform)
    duration_values = runtime._numpy.asarray(durations)
    if samples.dtype != runtime._numpy.float32 or samples.ndim != 2 or samples.shape[0] != 1:
        raise CanaryError("Supertonic experimental waveform is invalid")
    if duration_values.shape != (1,) or float(duration_values[0]) <= 0:
        raise CanaryError("Supertonic experimental duration is invalid")
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    runtime._soundfile.write(
        str(output_wav),
        samples.reshape(-1),
        module.SAMPLE_RATE_HZ,
        subtype="PCM_16",
        format="WAV",
    )


def _supertonic_style_targets(
    selected: Sequence[Mapping[str, Any]],
    jobs: Mapping[tuple[str, str], LineJob],
) -> list[Mapping[str, Any]]:
    by_line: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for candidate in selected:
        by_line[(str(candidate["scenario"]), str(candidate["line"]))].append(candidate)
    best: dict[str, tuple[float, Mapping[str, Any]]] = {}
    for candidate in selected:
        if candidate["model"] != SUPERTONIC:
            continue
        key = (str(candidate["scenario"]), str(candidate["line"]))
        job = _required_job(jobs, key)
        mora = _mora_count(job)
        own_rate = mora / _positive_float(candidate.get("duration_sec"), "duration_sec")
        other_rates = [
            mora / _positive_float(item.get("duration_sec"), "duration_sec")
            for item in by_line[key]
            if item["model"] != SUPERTONIC
        ]
        if len(other_rates) != 7:
            raise CanaryError(f"same-line comparison is incomplete: {key}")
        ratio = own_rate / statistics.median(other_rates)
        style = _required_text(_realized(candidate).get("voice_style"), "voice_style")
        if style not in best or ratio > best[style][0]:
            best[style] = (ratio, candidate)
    if set(best) != {f"F{index}" for index in range(1, 6)} | {f"M{index}" for index in range(1, 6)}:
        raise CanaryError("Supertonic style coverage is incomplete")
    return [best[style][1] for style in sorted(best)]


def _generate_aivis(
    *,
    selected: Sequence[Mapping[str, Any]],
    jobs: Mapping[tuple[str, str], LineJob],
    source_wavs: Mapping[str, Path],
    output: Path,
) -> list[dict[str, Any]]:
    from gaya_pipeline.adapters.aivisspeech import AivisSpeechAdapter

    targets = _explicit_reading_jobs(jobs)
    adapter = AivisSpeechAdapter()
    adapter.prepare(targets, output, output)
    entries: list[dict[str, Any]] = []
    for job in targets:
        key = (job.scenario_id, job.line_id)
        candidate = _required_candidate(selected, AIVIS, key)
        prepared = adapter._prepared_input(job)
        variants = [
            (f"explicit-reading-r{repeat}", prepared.reading)
            for repeat in range(1, 4)
        ] + [
            (f"surface-g2p-r{repeat}", None)
            for repeat in range(1, 4)
        ]
        for variant, reading in variants:
            path = output / key[0] / key[1] / f"{variant}.wav"
            source_path = _source_path(path)
            adapter._runtime.synthesize(
                text=prepared.text,
                reading=reading,
                speaker_id=prepared.style_id,
                intonation_scale=prepared.intonation_scale,
                tempo_dynamics_scale=prepared.tempo_dynamics_scale,
                output_wav=source_path,
            )
            _normalize_generated(source_path, path)
            entry = _audio_entry(
                model=AIVIS,
                key=key,
                job=job,
                variant=variant,
                path=path,
                seed=None,
                source_take_id=str(candidate["take_id"]),
                variables={"reading": reading, "reading_transport": "accent_phrases" if reading else "engine_g2p_from_surface"},
            )
            if variant == "explicit-reading-r1":
                _attach_source_reproduction(
                    entry,
                    source_wavs,
                    candidate,
                    strict_duration=False,
                )
            entries.append(entry)
    return entries


def _generate_cosyvoice(
    *,
    selected: Sequence[Mapping[str, Any]],
    jobs: Mapping[tuple[str, str], LineJob],
    source_wavs: Mapping[str, Path],
    voices: Path,
    output: Path,
) -> list[dict[str, Any]]:
    from gaya_pipeline.adapters.cosyvoice3 import CosyVoice3Adapter

    targets = _explicit_reading_jobs(jobs)
    adapter = CosyVoice3Adapter()
    adapter.prepare(targets, output, voices)
    recipe = adapter.take_recipe()
    entries: list[dict[str, Any]] = []
    for job in targets:
        key = (job.scenario_id, job.line_id)
        candidate = _required_candidate(selected, COSYVOICE, key)
        realized = _realized(candidate)
        seed = _required_int(realized.get("seed"), "CosyVoice seed")
        context = TakeContext.create(
            index=_required_int(candidate.get("take_index"), "take_index"),
            seed=seed,
            recipe_version=recipe.version,
            sampling=dict(recipe.sampling),
        )
        original = adapter._prepared_input(job)
        for variant, tts_text, reading_source in (
            ("explicit-reading", original.tts_text, original.reading_source),
            ("surface-text", original.source_text, "research.surface_text"),
        ):
            prepared = dataclasses.replace(
                original,
                tts_text=tts_text,
                reading_source=reading_source,
            )
            adapter._prepared_inputs[key] = prepared
            path = output / key[0] / key[1] / f"{variant}.wav"
            source_path = _source_path(path)
            adapter.generate(job, context, source_path)
            _normalize_generated(source_path, path)
            entry = _audio_entry(
                model=COSYVOICE,
                key=key,
                job=job,
                variant=variant,
                path=path,
                seed=seed,
                source_take_id=str(candidate["take_id"]),
                variables={"tts_text": tts_text, "reading_source": reading_source},
            )
            if variant == "explicit-reading":
                _attach_source_reproduction(entry, source_wavs, candidate)
            entries.append(entry)
        adapter._prepared_inputs[key] = original
    return entries


def _load_jobs(scenarios: Path) -> dict[tuple[str, str], LineJob]:
    result: dict[tuple[str, str], LineJob] = {}
    for path in sorted(scenarios.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise CanaryError(f"scenario is invalid: {path}")
        characters = {item["id"]: item for item in document["characters"]}
        for line in document["lines"]:
            job = LineJob(
                scene=document,
                character=characters[line["character"]],
                line=line,
                locale="ja",
            )
            key = (job.scenario_id, job.line_id)
            if key in result:
                raise CanaryError(f"duplicate scenario line: {key}")
            result[key] = job
    if len(result) != 161:
        raise CanaryError(f"expected 161 lines, got {len(result)}")
    return result


def _selected_candidates(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates = manifest.get("candidates")
    curations = manifest.get("curations")
    if not isinstance(candidates, list) or not isinstance(curations, list):
        raise CanaryError("manifest candidates/curations are invalid")
    by_take = {item["take_id"]: item for item in candidates}
    selected = [by_take[item["take_id"]] for item in curations if item.get("decision") == "selected"]
    if len(selected) != 1288:
        raise CanaryError(f"expected 1288 selected candidates, got {len(selected)}")
    return selected


def _source_wavs(takes_root: Path, target_take_ids: set[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for ledger_path in takes_root.rglob("ledger.json"):
        ledger = _load_json(ledger_path)
        for attempt in ledger.get("attempts", []):
            take_id = attempt.get("take_id")
            if take_id not in target_take_ids or take_id in result:
                continue
            audio = attempt.get("audio")
            if not isinstance(audio, dict) or not isinstance(audio.get("wav_path"), str):
                continue
            path = (ledger_path.parent / audio["wav_path"]).resolve()
            expected = audio.get("wav_sha256")
            if not path.is_file() or not isinstance(expected, str) or _sha256(path) != expected:
                raise CanaryError(f"source WAV is invalid: {take_id}")
            result[take_id] = path
    return result


def _selected_anchor(
    groups: Sequence[Mapping[str, Any]],
    job: LineJob,
    expected_sha: str,
) -> Mapping[str, Any]:
    matches = [
        item
        for item in groups
        if (item.get("model"), item.get("scenario"), item.get("character"))
        == (IRODORI, job.scenario_id, job.character["id"])
    ]
    if len(matches) != 1 or matches[0].get("audio_sha256") != expected_sha:
        raise CanaryError(f"selected anchor does not match: {job.scenario_id}/{job.line_id}")
    return matches[0]


def _required_candidate(
    selected: Sequence[Mapping[str, Any]],
    model: str,
    key: tuple[str, str],
) -> Mapping[str, Any]:
    matches = [
        item
        for item in selected
        if (item.get("model"), item.get("scenario"), item.get("line"))
        == (model, key[0], key[1])
    ]
    if len(matches) != 1:
        raise CanaryError(f"selected candidate is not unique: {model}/{key[0]}/{key[1]}")
    return matches[0]


def _required_job(
    jobs: Mapping[tuple[str, str], LineJob],
    key: tuple[str, str],
) -> LineJob:
    try:
        return jobs[key]
    except KeyError as error:
        raise CanaryError(f"scenario line is missing: {key}") from error


def _explicit_reading_jobs(jobs: Mapping[tuple[str, str], LineJob]) -> list[LineJob]:
    result = [job for job in jobs.values() if isinstance(job.line.get("reading"), str)]
    if len(result) != 25:
        raise CanaryError(f"expected 25 explicit-reading lines, got {len(result)}")
    return sorted(result, key=lambda job: (job.scenario_id, job.line_id))


def _audio_entry(
    *,
    model: str,
    key: tuple[str, str],
    job: LineJob,
    variant: str,
    path: Path,
    seed: int | None,
    source_take_id: str,
    variables: Mapping[str, Any],
) -> dict[str, Any]:
    if not path.is_file():
        raise CanaryError(f"generated WAV is missing: {path}")
    with wave.open(str(path), "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()
        audio = {
            "sample_rate_hz": wav.getframerate(),
            "channels": wav.getnchannels(),
            "sample_width_bytes": wav.getsampwidth(),
            "duration_sec": round(duration, 6),
        }
    return {
        "model": model,
        "scenario": key[0],
        "line": key[1],
        "character": str(job.character["id"]),
        "character_name": str(job.character["name"]),
        "text": str(job.line["text"]),
        "reading": job.line.get("reading"),
        "emotion": str(job.line["emotion"]),
        "intensity": int(job.line["intensity"]),
        "delivery": str(job.line["delivery"]),
        "mora_count": _mora_count(job),
        "variant": variant,
        "seed": seed,
        "source_take_id": source_take_id,
        "variables": dict(variables),
        "wav_path": path.relative_to(path.parents[3] if model == SUPERTONIC else path.parents[2]).as_posix(),
        "wav_sha256": _sha256(path),
        "audio": audio,
    }


def _attach_source_reproduction(
    entry: dict[str, Any],
    source_wavs: Mapping[str, Path],
    candidate: Mapping[str, Any],
    *,
    strict_duration: bool = True,
) -> None:
    take_id = str(candidate["take_id"])
    source = source_wavs.get(take_id)
    if source is None:
        entry["source_reproduction"] = {
            "local_source_available": False,
            "reason": "The immutable published Opus exists, but the pre-encode WAV is not local.",
        }
        return
    source_sha = _sha256(source)
    with wave.open(str(source), "rb") as wav:
        source_duration = wav.getnframes() / wav.getframerate()
    duration_delta = abs(float(entry["audio"]["duration_sec"]) - source_duration)
    if strict_duration and duration_delta > 0.001:
        raise CanaryError(
            f"current branch duration does not reproduce source WAV: {take_id} "
            f"delta={duration_delta}",
        )
    entry["source_reproduction"] = {
        "local_source_available": True,
        "source_wav_sha256": source_sha,
        "generated_wav_sha256": entry["wav_sha256"],
        "byte_identical": entry["wav_sha256"] == source_sha,
        "source_duration_sec": round(source_duration, 6),
        "duration_delta_sec": round(duration_delta, 6),
        "duration_within_1ms": duration_delta <= 0.001,
        "comparison_note": (
            "The adapter output is normalized again with the current pinned "
            "postprocess contract; container/sample hashes may change with the "
            "installed FFmpeg patch release."
        ),
    }


def _source_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.adapter-source.wav")


@functools.lru_cache(maxsize=1)
def _audio_tools() -> Any:
    return find_audio_tools()


def _normalize_generated(source_path: Path, output_path: Path) -> None:
    try:
        normalize_wav(
            _audio_tools(),
            source_path,
            output_path,
            PostprocessProfile(),
        )
    finally:
        source_path.unlink(missing_ok=True)


def _mora_count(job: LineJob) -> int:
    resolved = resolve_japanese_reading(
        text=str(job.line["text"]),
        reading=job.line.get("reading"),
    )
    return count_japanese_mora(normalize_japanese_reading(resolved.text))


def _realized(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    value = candidate.get("gen_params", {}).get("realized")
    if not isinstance(value, dict):
        raise CanaryError(f"candidate realized parameters are invalid: {candidate.get('take_id')}")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CanaryError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise CanaryError(f"JSON root must be an object: {path}")
    return value


def _receipt(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "path": path.as_posix(),
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CanaryError(f"{field} must be a non-empty string")
    return value


def _required_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CanaryError(f"{field} must be an integer")
    return value


def _positive_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
        raise CanaryError(f"{field} must be a positive number")
    return float(value)


if __name__ == "__main__":
    main()
