from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from gaya_pipeline.generation import (
    GenerationError,
    GenerationSummary,
    run_generation,
)
from gaya_pipeline.publish import (
    PublishError,
    PublishSummary,
    create_r2_client,
    run_publish,
)
from gaya_pipeline.qc import QCError, QCSummary, run_qc
from gaya_pipeline.qc_runtime import KanaWhisperQCRuntime
from gaya_pipeline.validation import default_scenarios_dir, validate_scenarios
from gaya_pipeline.voice_assets import (
    default_voices_dir,
    validate_local_voice_assets,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gaya")
    subparsers = parser.add_subparsers(dest="command", required=True)
    repository_root = default_scenarios_dir().parent

    validate_parser = subparsers.add_parser(
        "validate",
        help="シナリオ YAML を検証する",
    )
    validate_parser.add_argument(
        "--scenarios",
        type=Path,
        default=default_scenarios_dir(),
        help="シナリオディレクトリ",
    )

    voices_parser = subparsers.add_parser(
        "voices",
        help="参照音声キットを操作する",
    )
    voices_subparsers = voices_parser.add_subparsers(
        dest="voices_command",
        required=True,
    )
    voices_validate_parser = voices_subparsers.add_parser(
        "validate-local",
        help="ローカル参照 WAV とメタデータを検証する",
    )
    voices_validate_parser.add_argument(
        "--voices",
        type=Path,
        default=default_voices_dir(),
        help="参照音声キットのディレクトリ",
    )

    gen_parser = subparsers.add_parser(
        "gen",
        help="モデルアダプタで音声を生成する",
    )
    gen_parser.add_argument("--model", required=True, help="model id")
    gen_parser.add_argument("--scenario", help="scenario id")
    gen_parser.add_argument("--line", help="scenario 内の line id")
    gen_parser.add_argument(
        "--force",
        action="store_true",
        help="hash 一致時も再生成する",
    )

    qc_parser = subparsers.add_parser(
        "qc",
        help="manifest の生成音声に読み・韻律 QA を実行する",
    )
    qc_parser.add_argument(
        "--manifest",
        type=Path,
        default=repository_root / "data" / "manifest.json",
        help="検査対象 manifest",
    )
    qc_parser.add_argument(
        "--scenarios",
        type=Path,
        default=repository_root / "scenarios",
        help="期待読みを取得するシナリオディレクトリ",
    )
    qc_parser.add_argument(
        "--artifacts",
        type=Path,
        default=repository_root / "artifacts",
        help="manifest path の基準となる artifact ディレクトリ",
    )
    qc_parser.add_argument(
        "--output",
        type=Path,
        default=repository_root / "artifacts" / "qc" / "report.json",
        help="QC report 出力先",
    )
    qc_parser.add_argument("--model", help="model id で対象を絞る")
    qc_parser.add_argument("--scenario", help="scenario id で対象を絞る")
    qc_parser.add_argument(
        "--line",
        help="scenario 内の line id で対象を絞る",
    )

    subparsers.add_parser(
        "publish",
        help="manifest と一致する Opus を R2 へ差分アップロードする",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "validate":
        result = validate_scenarios(args.scenarios)
        for warning in result.warnings:
            print(f"WARNING: {warning}", file=sys.stderr)
        if result.problems:
            for problem in result.problems:
                print(f"ERROR: {problem}", file=sys.stderr)
            print(
                f"検証失敗: {len(result.problems)} 件の問題があります。",
                file=sys.stderr,
            )
            return 1

        print(
            f"検証成功: {result.file_count} シナリオ / "
            f"警告 {len(result.warnings)} 件",
        )
        return 0

    if args.command == "voices":
        if args.voices_command != "validate-local":
            raise AssertionError(
                f"unknown voices command: {args.voices_command}",
            )
        result = validate_local_voice_assets(args.voices)
        if result.problems:
            for problem in result.problems:
                print(f"ERROR: {problem}", file=sys.stderr)
            print(
                f"検証失敗: {len(result.problems)} 件の問題があります。",
                file=sys.stderr,
            )
            return 1

        print(f"検証成功: {len(result.voice_ids)} 参照音声")
        return 0

    if args.command == "gen":
        repository_root = default_scenarios_dir().parent
        try:
            summary = run_generation(
                model_id=args.model,
                scenarios_dir=repository_root / "scenarios",
                artifacts_dir=repository_root / "artifacts",
                manifest_path=repository_root / "data" / "manifest.json",
                scenario_id=args.scenario,
                line_id=args.line,
                force=args.force,
            )
        except GenerationError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        _print_generation_summary(summary)
        return 1 if summary.failed_count else 0

    if args.command == "qc":
        runtime = KanaWhisperQCRuntime(
            args.artifacts / "models" / "qc" / "sbintuitions--kana-whisper",
        )
        try:
            summary = run_qc(
                manifest_path=args.manifest,
                scenarios_dir=args.scenarios,
                artifacts_dir=args.artifacts,
                output_path=args.output,
                runtime=runtime,
                model_id=args.model,
                scenario_id=args.scenario,
                line_id=args.line,
            )
        except QCError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        _print_qc_summary(summary)
        return 1 if summary.analysis_error_count or summary.mismatch_count else 0

    if args.command == "publish":
        repository_root = default_scenarios_dir().parent
        try:
            summary = run_publish(
                manifest_path=repository_root / "data" / "manifest.json",
                artifacts_dir=repository_root / "artifacts",
                client=create_r2_client(repository_root),
            )
        except PublishError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        _print_publish_summary(summary)
        return 0

    raise AssertionError(f"unknown command: {args.command}")


def _print_generation_summary(summary: GenerationSummary) -> None:
    for record in summary.records:
        action = "生成" if record.status == "generated" else "スキップ"
        print(
            f"{action}: {record.scenario_id}/{record.line_id} "
            f"生成={record.generation_seconds:.3f}s RTF={record.rtf:.3f}",
        )
    if summary.failures:
        print("失敗サマリ:")
        for failure in summary.failures:
            print(
                f"  - {failure.scenario_id}/{failure.line_id}: "
                f"{failure.message}",
            )
    print(
        f"完了: 生成 {summary.generated_count} / "
        f"スキップ {summary.skipped_count} / "
        f"失敗 {summary.failed_count} / "
        f"所要時間 {summary.elapsed_seconds:.3f}s",
    )


def _print_publish_summary(summary: PublishSummary) -> None:
    for record in summary.records:
        action = "アップロード" if record.status == "uploaded" else "スキップ"
        print(f"{action}: {record.key} ({record.size_bytes} bytes)")
    print(
        f"完了: アップロード {summary.uploaded_count} / "
        f"スキップ {summary.skipped_count}",
    )


def _print_qc_summary(summary: QCSummary) -> None:
    print(f"QC report: {summary.output_path.as_posix()}")
    print(
        f"完了: clip {summary.clip_count} / "
        f"合格 {summary.pass_count} / "
        f"読み不一致 {summary.mismatch_count} / "
        f"reading要確認 {summary.needs_reading_count} / "
        f"目視確認 {summary.review_required_count} / "
        f"解析失敗 {summary.analysis_error_count}",
    )
