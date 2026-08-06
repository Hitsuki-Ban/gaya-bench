from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from gaya_pipeline.adapters import irodori_tts as _v3
from gaya_pipeline.adapters.base import Capabilities, LineJob, ModelProfile
from gaya_pipeline.adapters.conditioning import effective_reference_voice
from gaya_pipeline.conditioning_variants import ROLE_SCOPE_NO_REFERENCE
from gaya_pipeline.completion_anchor import CompletionAnchorError
from gaya_pipeline.completion_plan import RoleSnapshot

MODEL_ID = "irodori-tts-v4-small"
IRODORI_TTS_VERSION = "0.1.0"
UPSTREAM_REVISION = "8ca3acb58ab4e19ad6d594aaed6bafe3e88f7f71"
CHECKPOINT_ID = "Aratako/Irodori-TTS-v4-Small"
CHECKPOINT_REVISION = "e4aaac4df355ff560dcd35e0dae272c3a759317b"
CHECKPOINT_PARAMETERS = 766_052_385
CHECKPOINT_SHA256 = (
    "5863c986345d9f6d20b7d8748fee1af02079c5161cf0c9e52557da0a0c378593"
)
CODEC_ID = _v3.CODEC_ID
CODEC_REVISION = _v3.CODEC_REVISION
DACVAE_REVISION = _v3.DACVAE_REVISION
SILENTCIPHER_MODEL_ID = _v3.SILENTCIPHER_MODEL_ID
SILENTCIPHER_MODEL_REVISION = _v3.SILENTCIPHER_MODEL_REVISION
SILENTCIPHER_VERSION = _v3.SILENTCIPHER_VERSION
TORCH_VERSION = "2.10.0"
TORCHAUDIO_VERSION = "2.10.0"
TORCHCODEC_VERSION = "0.10.0"
CUDA_WHEEL_VERSION = "12.8"
DEVICE = "cuda:0"
MODEL_PRECISION = "bf16"
CODEC_PRECISION = "fp32"
SAMPLE_RATE_HZ = 48_000
# v4 checkpoint は連結 reference を 120 秒まで受け取る (v3 は 30 秒)。
MAX_REF_SECONDS = 120.0
WATERMARK_PAYLOAD = _v3.WATERMARK_PAYLOAD
# 同じ conditioning 契約を使うため anchor text と reference control tag は共有する。
# cache identity は model id と PROFILE_VERSION が generation_input_sha256 に
# 入ることで v3 と分離される。
ROLE_ANCHOR_TEXT = _v3.ROLE_ANCHOR_TEXT
REFERENCE_CONTROL = _v3.REFERENCE_CONTROL
ANCHOR_CAPTION_POLICY = "short-gender-age-voice-acoustics-v2"

PROFILE_VERSION = (
    f"Irodori-TTS {IRODORI_TTS_VERSION}@{UPSTREAM_REVISION}; "
    f"{CHECKPOINT_ID}@{CHECKPOINT_REVISION}; "
    f"{CODEC_ID}@{CODEC_REVISION}; "
    f"DACVAE@{DACVAE_REVISION}; bundled-tokenizer@{CHECKPOINT_REVISION}; "
    f"{SILENTCIPHER_MODEL_ID}@{SILENTCIPHER_MODEL_REVISION}"
)

IrodoriTTSV4AdapterError = _v3.IrodoriTTSAdapterError


def build_role_anchor_caption(identity: Mapping[str, str]) -> str:
    """Build the short, acoustic-only VoiceDesign prompt used for role anchors."""
    gender = identity["gender"]
    age = identity["age"]
    voice = identity["voice"].strip()
    if not voice:
        raise IrodoriTTSV4AdapterError("anchor roleのvoiceが空です。")
    try:
        age_label = _v3._AGE_LABEL[age]
        gender_label = _v3._GENDER_LABEL[gender]
    except KeyError as error:
        raise IrodoriTTSV4AdapterError(
            f"anchor roleの性別または年齢が未対応です: {gender}/{age}",
        ) from error
    subject = (
        f"{age_label}の中性的な声"
        if gender == "neutral"
        else f"{age_label}の{gender_label}"
    )
    return f"{subject}。{voice}"


def _role_anchor_sampling() -> dict[str, Any]:
    sampling = dict(_v3._role_anchor_sampling())
    sampling["max_ref_seconds"] = MAX_REF_SECONDS
    return sampling


class _NativeRuntime(_v3._NativeRuntime):
    def release_cached_vram(self) -> None:
        """caching allocator の未使用 block を driver へ返す。

        Windows(WDDM) では PyTorch caching allocator の reserved pool が
        そのまま process の system commit charge を消費する。take ごとに
        text 長・reference 長が変わる長時間 run では pool が単調に肥大し、
        host 側の commit を圧迫して ffmpeg 子プロセスの ENOMEM や
        `json.dumps` の MemoryError を誘発する (#194 実測)。生成結果には
        影響しない (kernel も RNG も不変) ので、take 境界で pool を畳んで
        commit 上限を bound する。v3 の凍結 source には手を入れないため、
        v4 subclass 側に定義する。
        """
        import gc

        gc.collect()
        self.torch.cuda.synchronize(0)
        self.torch.cuda.empty_cache()

    def prepare(self) -> Mapping[str, float]:
        if self._runtime is not None:
            raise IrodoriTTSV4AdapterError("runtime はすでに prepare 済みです。")

        checkpoint_dir = Path(
            self.huggingface_hub.snapshot_download(
                repo_id=CHECKPOINT_ID,
                revision=CHECKPOINT_REVISION,
                allow_patterns=(
                    "model.safetensors",
                    "tokenizer/tokenizer.json",
                    "tokenizer/tokenizer_config.json",
                ),
            ),
        )
        model_path = checkpoint_dir / "model.safetensors"
        tokenizer_dir = checkpoint_dir / "tokenizer"
        codec_path = Path(
            self.huggingface_hub.hf_hub_download(
                repo_id=CODEC_ID,
                filename="weights.pth",
                revision=CODEC_REVISION,
            ),
        )
        silentcipher_path = Path(
            self.huggingface_hub.snapshot_download(
                repo_id=SILENTCIPHER_MODEL_ID,
                revision=SILENTCIPHER_MODEL_REVISION,
                allow_patterns=("44_1_khz/73999_iteration/*",),
            ),
        )
        silentcipher_checkpoint = silentcipher_path / "44_1_khz" / "73999_iteration"
        silentcipher_config = silentcipher_checkpoint / "hparams.yaml"
        for path, label in (
            (model_path, "model.safetensors"),
            (tokenizer_dir / "tokenizer.json", "bundled tokenizer.json"),
            (tokenizer_dir / "tokenizer_config.json", "bundled tokenizer_config.json"),
            (codec_path, "weights.pth"),
            (silentcipher_checkpoint, "SilentCipher checkpoint"),
            (silentcipher_config, "SilentCipher config"),
        ):
            if not path.exists():
                raise IrodoriTTSV4AdapterError(
                    f"固定 revision の {label} がありません: {path}",
                )
        actual_sha256 = _v3._sha256_file(model_path)
        if actual_sha256 != CHECKPOINT_SHA256:
            raise IrodoriTTSV4AdapterError(
                "model.safetensors の SHA-256 が一致しません: "
                f"expected={CHECKPOINT_SHA256}, actual={actual_sha256}",
            )

        original_silentcipher_get_model = self.silentcipher.get_model
        self.reset_peak_memory_stats()
        self.silentcipher.get_model = _v3._pinned_silentcipher_loader(
            original_silentcipher_get_model,
            checkpoint_path=silentcipher_checkpoint,
            config_path=silentcipher_config,
        )
        try:
            runtime_key = self.inference_runtime.RuntimeKey(
                checkpoint=str(model_path),
                model_device=DEVICE,
                codec_repo=str(codec_path),
                model_precision=MODEL_PRECISION,
                codec_device=DEVICE,
                codec_precision=CODEC_PRECISION,
                codec_deterministic_encode=True,
                codec_deterministic_decode=True,
                compile_model=False,
                compile_dynamic=False,
            )
            runtime = self.inference_runtime.InferenceRuntime.from_key(runtime_key)
        finally:
            self.silentcipher.get_model = original_silentcipher_get_model

        config = runtime.model_cfg
        if not config.use_caption_condition:
            raise IrodoriTTSV4AdapterError(
                "checkpoint は caption conditioning をサポートしていません。",
            )
        if not config.use_speaker_condition_resolved:
            raise IrodoriTTSV4AdapterError(
                "checkpoint は speaker conditioning をサポートしていません。",
            )
        if not config.use_duration_predictor:
            raise IrodoriTTSV4AdapterError(
                "checkpoint は duration predictor をサポートしていません。",
            )
        if float(runtime.default_max_ref_seconds) != MAX_REF_SECONDS:
            raise IrodoriTTSV4AdapterError(
                "checkpoint の reference 上限が一致しません: "
                f"expected={MAX_REF_SECONDS}, "
                f"actual={runtime.default_max_ref_seconds}",
            )
        if not runtime.watermarker.ready:
            raise IrodoriTTSV4AdapterError(
                "SilentCipher watermark model を読み込めません。",
            )

        self._runtime = runtime
        return self.peak_memory_mib()

    def synthesize(
        self,
        *,
        text: str,
        caption: str,
        reference_wav: Path | None,
        output_wav: Path,
        seed: int,
    ) -> Mapping[str, Any]:
        runtime = self._runtime
        if runtime is None:
            raise IrodoriTTSV4AdapterError("runtime が prepare されていません。")
        if not runtime.watermarker.ready:
            raise IrodoriTTSV4AdapterError(
                "SilentCipher watermark model が利用できません。",
            )

        # take 境界で caching allocator の reserved pool を畳む。
        # v4 は checkpoint (766M) と reference 上限 (120s) が v3 より大きく、
        # 161 line x N take の run では shape 差で pool が単調増加する。
        # WDDM ではその reserved 分がそのまま host の commit charge になるため、
        # 畳まないと host 側が先に枯渇する (#194)。
        # peak 計測前に呼ぶので receipt の reserved_mib は
        # 「その take が実際に必要とした量」になり、run 履歴に依存しなくなる。
        self.release_cached_vram()
        self.reset_peak_memory_stats()
        request = self.inference_runtime.SamplingRequest(
            text=text,
            caption=caption,
            ref_wav=None if reference_wav is None else str(reference_wav),
            no_ref=reference_wav is None,
            ref_normalize_db=_v3.REF_NORMALIZE_DB,
            ref_ensure_max=True,
            num_candidates=1,
            decode_mode="sequential",
            seconds=None,
            duration_scale=_v3.DURATION_SCALE,
            min_seconds=_v3.MIN_SECONDS,
            max_seconds=_v3.MAX_SECONDS,
            max_ref_seconds=MAX_REF_SECONDS,
            num_steps=_v3.NUM_STEPS,
            cfg_scale_text=_v3.CFG_SCALE_TEXT,
            cfg_scale_caption=_v3.CFG_SCALE_CAPTION,
            cfg_scale_speaker=_v3.CFG_SCALE_SPEAKER,
            cfg_guidance_mode=_v3.CFG_GUIDANCE_MODE,
            cfg_min_t=_v3.CFG_MIN_T,
            cfg_max_t=_v3.CFG_MAX_T,
            context_kv_cache=True,
            speaker_uncond_mode="mask",
            seed=seed,
            t_schedule_mode="linear",
            sway_coeff=-1.0,
            trim_tail=True,
            tail_window_size=20,
            tail_std_threshold=0.05,
            tail_mean_threshold=0.1,
        )
        result = runtime.synthesize(
            request,
            log_fn=lambda message: print(
                f"[irodori-v4] {message}",
                file=sys.stderr,
                flush=True,
            ),
        )
        generation_peak = self.peak_memory_mib()
        if result.used_seed != seed:
            raise IrodoriTTSV4AdapterError(
                f"seed が一致しません: expected={seed}, actual={result.used_seed}",
            )
        if result.sample_rate != SAMPLE_RATE_HZ:
            raise IrodoriTTSV4AdapterError(
                "sample rate が一致しません: "
                f"expected={SAMPLE_RATE_HZ}, actual={result.sample_rate}",
            )
        audio = result.audio.detach().float().cpu().squeeze()
        if audio.ndim != 1 or audio.numel() == 0:
            raise IrodoriTTSV4AdapterError(
                f"runtime 出力の waveform shape が不正です: {tuple(audio.shape)}",
            )
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        self.soundfile.write(
            str(output_wav),
            audio.numpy(),
            samplerate=result.sample_rate,
            format="WAV",
            subtype="PCM_16",
        )
        if not output_wav.is_file():
            raise IrodoriTTSV4AdapterError(
                f"PCM WAV が書き込まれませんでした: {output_wav}",
            )
        timings = {
            str(name): round(float(seconds), 6)
            for name, seconds in result.stage_timings
        }
        if "silentcipher_watermark" not in timings:
            raise IrodoriTTSV4AdapterError(
                "SilentCipher watermark stage が実行されませんでした。",
            )
        return {
            "phase_peak_vram_mib": {"generation": generation_peak},
            "seed": result.used_seed,
            "sample_rate_hz": result.sample_rate,
            "silentcipher_watermark_stage_executed": True,
            "stage_timings_sec": timings,
            "total_to_decode_sec": round(float(result.total_to_decode), 6),
        }


class IrodoriTTSV4Adapter(_v3.IrodoriTTSAdapter):
    """Irodori-TTS v4-Small.

    conditioning 契約は v3 と同一 (明示 reference 優先、その他は役ごと単一の
    frozen anchor + 全 role caption) で、checkpoint / tokenizer / reference 上限
    だけが異なる。v3 の cache・anchor selection とは互換ではなく、
    generation_input_sha256 が model id と PROFILE_VERSION を含むことで分離される。
    """

    profile = ModelProfile(
        id=MODEL_ID,
        name="Irodori-TTS v4-Small",
        version=PROFILE_VERSION,
        license_note=(
            "コード・重み・codec は MIT。SilentCipher payload IRDTS の埋め込み"
            "処理を必須実行するが、後処理後の検出可能性は保証しない。"
            "作者は倫理規約 (無断のなりすまし・誤情報生成の禁止) を守る限り"
            "生成音声の商用利用に追加制限を設けていない。"
            "無断の声真似・誤認を招く deepfake を禁止。"
        ),
        capabilities=Capabilities(
            emotion=True,
            voice_prompt=True,
            clone=True,
            nonverbal=True,
            # v4 も reading 入力を受け取らない。#177 の reading 契約どおり
            # 原文 (line.text) をそのまま渡す。
            reading=False,
        ),
    )

    def __init__(
        self,
        *,
        runtime: _v3._Runtime | None = None,
        role_anchor_selection_path: Path | None = None,
        role_anchor_plan_sha256: str | None = None,
        conditioning_mode: str | None = None,
    ) -> None:
        super().__init__(
            runtime=runtime if runtime is not None else _NativeRuntime(),
            role_anchor_selection_path=role_anchor_selection_path,
            role_anchor_plan_sha256=role_anchor_plan_sha256,
            conditioning_mode=conditioning_mode,
        )

    def prepare(
        self,
        jobs: Sequence[LineJob],
        artifacts_dir: Path,
        voices_dir: Path,
    ) -> None:
        self._prepared = False
        self._prepared_inputs.clear()
        jobs_by_character: dict[tuple[str, str], list[LineJob]] = {}
        role_identities: dict[tuple[str, str], dict[str, str]] = {}
        role_snapshots: dict[tuple[str, str], RoleSnapshot] = {}
        reference_voice_by_character: dict[tuple[str, str], str | None] = {}
        line_keys: set[tuple[str, str]] = set()
        for job in jobs:
            line_key = _v3._job_key(job)
            if line_key in line_keys:
                raise IrodoriTTSV4AdapterError(
                    f"同じ line job が重複しています: {line_key[0]}/{line_key[1]}",
                )
            line_keys.add(line_key)
            character_key = _v3._character_key(job)
            identity = _v3._role_identity(job)
            if (
                character_key in role_identities
                and role_identities[character_key] != identity
            ):
                raise IrodoriTTSV4AdapterError(
                    "同じ scenario/character に異なる役柄情報があります: "
                    f"{character_key[0]}/{character_key[1]}",
                )
            role_identities[character_key] = identity
            snapshot = _v3._role_snapshot(job)
            if (
                character_key in role_snapshots
                and role_snapshots[character_key] != snapshot
            ):
                raise IrodoriTTSV4AdapterError(
                    "同じscenario/characterに異なるrole snapshotがあります: "
                    f"{character_key[0]}/{character_key[1]}",
                )
            role_snapshots[character_key] = snapshot
            reference_voice = _v3._reference_voice_value(job)
            if (
                character_key in reference_voice_by_character
                and reference_voice_by_character[character_key] != reference_voice
            ):
                raise IrodoriTTSV4AdapterError(
                    "同じ scenario/character に異なる reference_voice があります: "
                    f"{character_key[0]}/{character_key[1]}",
                )
            reference_voice_by_character[character_key] = reference_voice
            jobs_by_character.setdefault(character_key, []).append(job)

        effective_voices = {
            character_key: effective_reference_voice(
                mode=self._conditioning_mode,
                scenario=character_key[0],
                character=character_key[1],
                explicit=explicit,
            )
            for character_key, explicit in reference_voice_by_character.items()
        }
        reference_entries = (
            _v3._load_reference_entries(voices_dir)
            if any(value is not None for value in effective_voices.values())
            else {}
        )
        references: dict[tuple[str, str], _v3._RoleReference] = {}
        for character_key in sorted(jobs_by_character):
            voice_id = effective_voices[character_key]
            # 明示 reference が最優先 (条件バリアントではmodeが優先する)。
            if voice_id is not None:
                try:
                    entry = reference_entries[voice_id]
                except KeyError as error:
                    raise IrodoriTTSV4AdapterError(
                        f"未登録の reference_voice です: {voice_id}",
                    ) from error
                wav_path, sha256 = _v3._resolve_reference_wav(voices_dir, entry)
                references[character_key] = _v3._RoleReference(
                    source="voice-asset",
                    voice_id=voice_id,
                    wav_path=wav_path,
                    sha256=sha256,
                    caption=None,
                    text=None,
                    phase_peak_vram_mib={},
                    selected_anchor_receipt=None,
                )
                continue

            # それ以外は役ごと単一の frozen anchor。未選定・stale は fail fast。
            if (
                self._role_anchor_selection_path is None
                or self._role_anchor_plan_sha256 is None
            ):
                raise IrodoriTTSV4AdapterError(
                    "reference_voice=nullのPhase B準備にはrole anchor selectionの"
                    "絶対pathとfrozen plan SHAが必要です。",
                )
            # 人手選抜 (role-anchor-selection-v1) と増分の機械選抜
            # (role-anchor-machine-selection-v1) の両方を受理する。
            # protocol分岐はresolverが行い、どちらも同じreceiptを返す。
            from gaya_pipeline.increment_anchor import (
                IncrementAnchorError,
                resolve_increment_anchor,
            )

            try:
                selected = resolve_increment_anchor(
                    selection_path=self._role_anchor_selection_path,
                    plan_sha256=self._role_anchor_plan_sha256,
                    model=self.profile.id,
                    model_revision=PROFILE_VERSION,
                    role=role_snapshots[character_key],
                )
            except (CompletionAnchorError, IncrementAnchorError) as error:
                raise IrodoriTTSV4AdapterError(
                    f"selected role anchorが不正です: {error}",
                ) from error
            if selected.anchor_text != ROLE_ANCHOR_TEXT:
                raise IrodoriTTSV4AdapterError(
                    "selected role anchor textがIrodori v4固定値と一致しません。",
                )
            identity = role_identities[character_key]
            references[character_key] = _v3._RoleReference(
                source="selected-role-anchor",
                voice_id=None,
                wav_path=selected.audio_path,
                sha256=selected.audio_sha256,
                caption=build_role_anchor_caption(identity),
                text=selected.anchor_text,
                phase_peak_vram_mib={},
                selected_anchor_receipt=selected.receipt(),
            )
        del artifacts_dir
        for character_key, character_jobs in jobs_by_character.items():
            reference = references[character_key]
            for job in character_jobs:
                self._prepared_inputs[_v3._job_key(job)] = _v3._prepare_input(
                    job,
                    reference=reference,
                )
        self._prepared = True

    def role_anchor_generation_input(
        self,
        role: RoleSnapshot,
        *,
        role_scope: str = ROLE_SCOPE_NO_REFERENCE,
    ) -> Mapping[str, Any]:
        identity = _v3._identity_from_role(role, role_scope=role_scope)
        return {
            "model": MODEL_ID,
            "model_revision": PROFILE_VERSION,
            "text": ROLE_ANCHOR_TEXT,
            "caption": build_role_anchor_caption(identity),
            "reference_wav": None,
            "sampling": _role_anchor_sampling(),
            "watermark_payload": WATERMARK_PAYLOAD,
        }

    def generate_role_anchor(
        self,
        role: RoleSnapshot,
        *,
        seed: int,
        output_wav: Path,
        role_scope: str = ROLE_SCOPE_NO_REFERENCE,
    ) -> Mapping[str, Any]:
        generation_input = self.role_anchor_generation_input(
            role,
            role_scope=role_scope,
        )
        self._ensure_runtime_loaded()
        realized = self._run_phase(
            f"Irodori v4 role anchor generation ({role.scenario}/{role.character})",
            lambda: self._runtime.synthesize(
                text=ROLE_ANCHOR_TEXT,
                caption=str(generation_input["caption"]),
                reference_wav=None,
                output_wav=output_wav,
                seed=seed,
            ),
        )
        if not output_wav.is_file():
            raise IrodoriTTSV4AdapterError(
                f"role anchor WAVが書き込まれませんでした: {output_wav}",
            )
        _v3._validate_pcm16_wav(output_wav)
        peaks = _v3._validate_role_anchor_receipt(realized, expected_seed=seed)
        if self._load_peak is None:
            raise IrodoriTTSV4AdapterError(
                "role anchor runtime load profileがありません。",
            )
        result = dict(realized)
        # anchor は caption だけを条件にする。実際に消費した caption と
        # model identity を receipt へ残す。
        result["model"] = MODEL_ID
        result["model_revision"] = PROFILE_VERSION
        result["caption"] = generation_input["caption"]
        result["phase_peak_vram_mib"] = {
            "role_anchor_runtime_load": _v3._copy_peak(self._load_peak),
            "role_anchor_generate": _v3._copy_peak(peaks["generation"]),
        }
        result["role_identity_sha256"] = role.role_identity_sha256
        return result

    def generation_params(self) -> Mapping[str, Any]:
        params = dict(super().generation_params())
        params.update(
            {
                "irodori_tts_version": IRODORI_TTS_VERSION,
                "upstream_revision": UPSTREAM_REVISION,
                "checkpoint": CHECKPOINT_ID,
                "checkpoint_revision": CHECKPOINT_REVISION,
                "checkpoint_parameters": CHECKPOINT_PARAMETERS,
                "checkpoint_sha256": CHECKPOINT_SHA256,
                "tokenizer": "bundled",
                "tokenizer_revision": CHECKPOINT_REVISION,
                "max_reference_seconds": MAX_REF_SECONDS,
            },
        )
        params["reference"] = {
            "normalize_db": _v3.REF_NORMALIZE_DB,
            "ensure_max": True,
            "max_seconds": MAX_REF_SECONDS,
        }
        params["role_reference"] = {
            **dict(params["role_reference"]),
            "anchor_caption_policy": ANCHOR_CAPTION_POLICY,
            "anchor_sampling": _role_anchor_sampling(),
            "explicit_reference_precedence": True,
        }
        return params
