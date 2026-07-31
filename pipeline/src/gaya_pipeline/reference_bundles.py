from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

SCHEMA_FILES = {
    "reference-bundle-v1": "reference-bundle-v1.schema.json",
    "assignments-v1": "assignments-v1.schema.json",
    "recording-request-v1": "recording-request-v1.schema.json",
    "derivative-receipt-v1": "derivative-receipt-v1.schema.json",
    "synthetic-sources-v1": "synthetic-sources-v1.schema.json",
}
ROOT_ENTRIES = frozenset(
    {"schema", "bundles", "assignments.yaml", "synthetic-sources.yaml"},
)


class ReferenceBundleCatalogError(ValueError):
    """参考バンドル catalog が契約を満たさないことを示す。"""


@dataclass(frozen=True)
class ReferenceBundleCatalogSummary:
    bundle_count: int
    assignment_count: int
    synthetic_policy_count: int


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


_UniqueKeyLoader.yaml_implicit_resolvers = {
    first_character: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag != "tag:yaml.org,2002:timestamp"
    ]
    for first_character, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.constructor.ConstructorError(
                "mapping",
                node.start_mark,
                f"キー '{key}' が重複しています。",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def validate_reference_bundle_catalog(
    catalog_dir: Path,
    *,
    as_of: date,
) -> ReferenceBundleCatalogSummary:
    """公開 catalog のメタデータだけを厳密検証する。"""

    if not catalog_dir.is_absolute():
        raise ReferenceBundleCatalogError(
            "--catalog には絶対パスを指定してください。",
        )
    if not catalog_dir.is_dir() or catalog_dir.is_symlink():
        raise ReferenceBundleCatalogError(
            f"catalog ディレクトリが存在しません: {catalog_dir}",
        )

    _validate_exact_entries(catalog_dir, ROOT_ENTRIES)
    schema_dir = catalog_dir / "schema"
    bundles_dir = catalog_dir / "bundles"
    if not schema_dir.is_dir() or schema_dir.is_symlink():
        raise ReferenceBundleCatalogError(
            f"schema は通常のディレクトリである必要があります: {schema_dir}",
        )
    if not bundles_dir.is_dir() or bundles_dir.is_symlink():
        raise ReferenceBundleCatalogError(
            f"bundles は通常のディレクトリである必要があります: {bundles_dir}",
        )

    _validate_exact_entries(schema_dir, frozenset(SCHEMA_FILES.values()))
    schemas = {
        schema_name: _load_schema(schema_dir / filename)
        for schema_name, filename in SCHEMA_FILES.items()
    }

    synthetic_path = catalog_dir / "synthetic-sources.yaml"
    synthetic_sources = _load_and_validate_yaml(
        synthetic_path,
        schemas["synthetic-sources-v1"],
    )
    policies = synthetic_sources["policies"]
    policy_ids = _unique_values(
        policies,
        field="policy_id",
        file=synthetic_path,
        label="synthetic policy id",
    )
    policies_by_id = {
        str(policy["policy_id"]): policy for policy in policies
    }
    if frozenset(policies_by_id) != policy_ids:
        raise AssertionError("synthetic policy index mismatch")
    _validate_synthetic_policies(synthetic_path, policies)

    bundle_paths = _bundle_paths(bundles_dir)
    bundles: list[tuple[Path, Mapping[str, Any]]] = []
    bundle_ids: dict[str, Path] = {}
    for bundle_path in bundle_paths:
        bundle = _load_and_validate_yaml(
            bundle_path,
            schemas["reference-bundle-v1"],
        )
        bundle_id = str(bundle["bundle_id"])
        if bundle_path.stem != bundle_id:
            raise ReferenceBundleCatalogError(
                f"{bundle_path}: ファイル名 stem は bundle id "
                f"'{bundle_id}' と一致する必要があります。",
            )
        _register_unique(bundle_ids, bundle_id, bundle_path, "bundle id")
        _validate_bundle_semantics(
            bundle_path,
            bundle,
            policies_by_id=policies_by_id,
        )
        bundles.append((bundle_path, bundle))

    assignments_path = catalog_dir / "assignments.yaml"
    assignments_document = _load_and_validate_yaml(
        assignments_path,
        schemas["assignments-v1"],
    )
    assignments = assignments_document["assignments"]
    _validate_assignments(
        assignments_path,
        assignments,
        bundles,
        policies_by_id=policies_by_id,
        as_of=as_of,
    )

    return ReferenceBundleCatalogSummary(
        bundle_count=len(bundles),
        assignment_count=len(assignments),
        synthetic_policy_count=len(policies),
    )


def _validate_exact_entries(directory: Path, expected: frozenset[str]) -> None:
    try:
        actual = {entry.name for entry in directory.iterdir()}
    except OSError as error:
        raise ReferenceBundleCatalogError(
            f"ディレクトリを読み取れません: {directory}: {error}",
        ) from error
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        raise ReferenceBundleCatalogError(
            f"{directory}: 必須エントリがありません: {', '.join(missing)}",
        )
    if extra:
        raise ReferenceBundleCatalogError(
            f"{directory}: 未許可のエントリがあります: {', '.join(extra)}",
        )


def _load_schema(path: Path) -> Mapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ReferenceBundleCatalogError(
            f"schema は通常のファイルである必要があります: {path}",
        )
    try:
        schema = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_unique_object,
        )
        Draft202012Validator.check_schema(schema)
    except (OSError, UnicodeError, json.JSONDecodeError, SchemaError) as error:
        raise ReferenceBundleCatalogError(
            f"schema を読み取れません: {path}: {error}",
        ) from error
    if not isinstance(schema, Mapping):
        raise ReferenceBundleCatalogError(
            f"schema のルートは object である必要があります: {path}",
        )
    return schema


def _json_unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise json.JSONDecodeError(
                f"キー '{key}' が重複しています。",
                key,
                0,
            )
        result[key] = value
    return result


def _load_and_validate_yaml(
    path: Path,
    schema: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ReferenceBundleCatalogError(
            f"YAML は通常のファイルである必要があります: {path}",
        )
    try:
        document = yaml.load(
            path.read_text(encoding="utf-8"),
            Loader=_UniqueKeyLoader,
        )
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ReferenceBundleCatalogError(
            f"YAML を読み取れません: {path}: {error}",
        ) from error

    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )
    errors = sorted(
        validator.iter_errors(document),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    if errors:
        error = errors[0]
        raise ReferenceBundleCatalogError(
            f"{path} [{_json_path(error.absolute_path)}] "
            f"schema 違反: {error.message}",
        )
    if not isinstance(document, Mapping):
        raise ReferenceBundleCatalogError(
            f"YAML のルートは object である必要があります: {path}",
        )
    return document


def _bundle_paths(bundles_dir: Path) -> tuple[Path, ...]:
    try:
        entries = tuple(sorted(bundles_dir.iterdir(), key=lambda path: path.name))
    except OSError as error:
        raise ReferenceBundleCatalogError(
            f"bundles を読み取れません: {bundles_dir}: {error}",
        ) from error
    if not entries:
        raise ReferenceBundleCatalogError(
            f"bundle YAML がありません: {bundles_dir}",
        )
    invalid = [
        entry.name
        for entry in entries
        if entry.suffix != ".yaml" or not entry.is_file() or entry.is_symlink()
    ]
    if invalid:
        raise ReferenceBundleCatalogError(
            f"{bundles_dir}: 未許可のエントリがあります: {', '.join(invalid)}",
        )
    return entries


def _unique_values(
    entries: Sequence[Mapping[str, Any]],
    *,
    field: str,
    file: Path,
    label: str,
) -> frozenset[str]:
    values: set[str] = set()
    for index, entry in enumerate(entries):
        value = str(entry[field])
        if value in values:
            raise ReferenceBundleCatalogError(
                f"{file} [$.{field}[{index}]] {label} "
                f"'{value}' が重複しています。",
            )
        values.add(value)
    return frozenset(values)


def _register_unique(
    values: dict[str, Path],
    value: str,
    file: Path,
    label: str,
) -> None:
    existing = values.get(value)
    if existing is not None:
        raise ReferenceBundleCatalogError(
            f"{file}: {label} '{value}' が重複しています "
            f"（既出: {existing}）。",
        )
    values[value] = file


def _validate_synthetic_policies(
    path: Path,
    policies: Sequence[Mapping[str, Any]],
) -> None:
    for index, policy in enumerate(policies):
        voice_design_id = str(
            policy["voice_design_model"]["repository_id"],
        )
        base_clone_id = str(policy["base_clone_model"]["repository_id"])
        if voice_design_id.removesuffix("-VoiceDesign") != (
            base_clone_id.removesuffix("-Base")
        ):
            raise ReferenceBundleCatalogError(
                f"{path} [$.policies[{index}]] VoiceDesign と Base は "
                "同一の公式 Qwen model line である必要があります。",
            )


def _validate_bundle_semantics(
    path: Path,
    bundle: Mapping[str, Any],
    policies_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    origin = bundle["origin"]
    origin_type = origin["type"]
    evidence = bundle["rights"]["evidence"]
    expected_evidence = {
        "public_corpus": "public_license",
        "commissioned_recording": "contract",
        "synthetic": "model_terms",
    }[origin_type]
    if evidence["type"] != expected_evidence:
        raise ReferenceBundleCatalogError(
            f"{path} [$.rights.evidence.type] origin '{origin_type}' には "
            f"'{expected_evidence}' が必要です。",
        )
    if origin_type == "commissioned_recording" and (
        origin["contract_reference_id"] != evidence["contract_reference_id"]
    ):
        raise ReferenceBundleCatalogError(
            f"{path} [$.rights.evidence.contract_reference_id] "
            "origin の契約参照 id と一致しません。",
        )
    if origin_type == "synthetic":
        policy_id = str(origin["synthetic_policy_id"])
        policy = policies_by_id.get(policy_id)
        if policy is None:
            raise ReferenceBundleCatalogError(
                f"{path} [$.origin.synthetic_policy_id] 未知の synthetic "
                f"policy '{policy_id}' です。",
            )

    clips = bundle["clips"]
    all_clips: list[tuple[str, Mapping[str, Any]]] = [
        ("$.clips.general", clips["general"]),
        ("$.clips.short_clone", clips["short_clone"]),
    ]
    all_clips.extend(
        (f"$.clips.emotions[{index}]", clip)
        for index, clip in enumerate(clips.get("emotions", []))
    )
    clip_ids: set[str] = set()
    emotions: set[str] = set()
    expected_storage_type = (
        "public_object"
        if bundle["publication"]["audio_access"] == "public"
        else "private_object"
    )
    for target, clip in all_clips:
        clip_id = str(clip["clip_id"])
        if clip_id in clip_ids:
            raise ReferenceBundleCatalogError(
                f"{path}: clip id '{clip_id}' が重複しています。",
        )
        clip_ids.add(clip_id)
        _validate_transcript(
            path,
            f"{target}.transcript",
            clip["transcript"],
        )
        emotion = clip.get("emotion")
        if emotion is not None:
            if emotion in emotions:
                raise ReferenceBundleCatalogError(
                    f"{path}: emotion clip '{emotion}' が重複しています。",
                )
            emotions.add(str(emotion))
        if clip["storage"]["type"] != expected_storage_type:
            raise ReferenceBundleCatalogError(
                f"{path}: publication.audio_access と clip "
                f"'{clip_id}' の storage.type が一致しません。",
            )

    permissions = bundle["rights"]["permissions"]
    if (
        bundle["publication"]["audio_access"] == "public"
        and permissions["audio_redistribution"] != "permitted"
    ):
        raise ReferenceBundleCatalogError(
            f"{path} [$.rights.permissions.audio_redistribution] "
            "public audio には permitted が必要です。",
        )

    term = bundle["rights"]["term"]
    if term["type"] == "fixed":
        starts_on = date.fromisoformat(term["starts_on"])
        expires_on = date.fromisoformat(term["expires_on"])
        renewal_review_on = date.fromisoformat(term["renewal_review_on"])
        if not starts_on <= renewal_review_on <= expires_on:
            raise ReferenceBundleCatalogError(
                f"{path} [$.rights.term] fixed term は "
                "starts_on <= renewal_review_on <= expires_on "
                "である必要があります。",
            )


def _validate_transcript(
    path: Path,
    target: str,
    transcript: Mapping[str, Any],
) -> None:
    actual = hashlib.sha256(
        str(transcript["text"]).encode("utf-8"),
    ).hexdigest()
    if transcript["utf8_sha256"] != actual:
        raise ReferenceBundleCatalogError(
            f"{path} [{target}.utf8_sha256] text の UTF-8 SHA-256 "
            f"と一致しません（実値: {actual}）。",
        )
    if transcript["rights"]["redistribution"] != "permitted":
        raise ReferenceBundleCatalogError(
            f"{path} [{target}.rights.redistribution] 公開 catalog に "
            "収録する transcript は permitted である必要があります。",
        )


def _validate_assignments(
    path: Path,
    assignments: Sequence[Mapping[str, Any]],
    bundles: Sequence[tuple[Path, Mapping[str, Any]]],
    *,
    policies_by_id: Mapping[str, Mapping[str, Any]],
    as_of: date,
) -> None:
    bundles_by_id = {
        str(bundle["bundle_id"]): (bundle_path, bundle)
        for bundle_path, bundle in bundles
    }
    subject_ids: set[str] = set()
    for index, assignment in enumerate(assignments):
        subject_id = str(assignment["subject_id"])
        if subject_id in subject_ids:
            raise ReferenceBundleCatalogError(
                f"{path} [$.assignments[{index}].subject_id] "
                f"subject id '{subject_id}' が重複しています。",
            )
        subject_ids.add(subject_id)

        bundle_id = str(assignment["bundle_id"])
        bundle_entry = bundles_by_id.get(bundle_id)
        if bundle_entry is None:
            raise ReferenceBundleCatalogError(
                f"{path} [$.assignments[{index}].bundle_id] "
                f"未知の bundle '{bundle_id}' です。",
            )
        if assignment["usage"] != "production":
            continue

        bundle_path, bundle = bundle_entry
        origin = bundle["origin"]
        if origin["type"] == "synthetic":
            policy_id = str(origin["synthetic_policy_id"])
            policy = policies_by_id[policy_id]
            if policy["status"] != "approved":
                raise ReferenceBundleCatalogError(
                    f"{path} [$.assignments[{index}]] production 割当に使う "
                    f"synthetic policy '{policy_id}' は approved "
                    "である必要があります。",
                )
        permissions = bundle["rights"]["permissions"]
        for permission in (
            "tts_reference_inference",
            "commercial_generated_output",
        ):
            if permissions[permission] != "permitted":
                raise ReferenceBundleCatalogError(
                    f"{path} [$.assignments[{index}]] production 割当に使う "
                    f"bundle '{bundle_id}' は {permission} が permitted "
                    f"である必要があります（定義: {bundle_path}）。",
                )

        term = bundle["rights"]["term"]
        if term["type"] == "fixed":
            starts_on = date.fromisoformat(term["starts_on"])
            expires_on = date.fromisoformat(term["expires_on"])
            if as_of < starts_on:
                raise ReferenceBundleCatalogError(
                    f"{path} [$.assignments[{index}]] production 割当に使う "
                    f"bundle '{bundle_id}' の許諾は未発効です。",
                )
            if as_of > expires_on:
                raise ReferenceBundleCatalogError(
                    f"{path} [$.assignments[{index}]] production 割当に使う "
                    f"bundle '{bundle_id}' の許諾は {expires_on} "
                    "に失効しています。",
                )


def _json_path(parts: Sequence[object]) -> str:
    result = "$"
    for part in parts:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}"
    return result
