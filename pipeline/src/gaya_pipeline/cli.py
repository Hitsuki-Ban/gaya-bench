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
        "--takes",
        required=True,
        type=int,
        help="各 line で生成する take 数",
    )
    gen_parser.add_argument(
        "--seed-base",
        required=True,
        type=int,
        help="take seed 導出の基準整数",
    )
    gen_parser.add_argument(
        "--force",
        action="store_true",
        help="whole-run cache を使わず新しい run を生成する",
    )

    qc_parser = subparsers.add_parser(
        "qc",
        help="run ledger の take に品質 gate を実行する",
    )
    qc_parser.add_argument(
        "--run-id",
        required=True,
        help="artifacts/takes 配下の generation run id",
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
                scenario_id=args.scenario,
                line_id=args.line,
                takes=args.takes,
                seed_base=args.seed_base,
                force=args.force,
            )
        except GenerationError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        _print_generation_summary(summary)
        return 1 if summary.failed_count else 0

    if args.command == "qc":
        repository_root = default_scenarios_dir().parent
        artifacts_dir = repository_root / "artifacts"
        try:
            runtime = KanaWhisperQCRuntime(
                artifacts_dir / "models" / "qc" / "sbintuitions--kana-whisper",
            )
            summary = run_qc(
                run_id=args.run_id,
                scenarios_dir=repository_root / "scenarios",
                artifacts_dir=artifacts_dir,
                runtime=runtime,
            )
        except QCError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        _print_qc_summary(summary)
        return 1 if summary.blocked_count or summary.pending_count else 0

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
    print(f"Run ID: {summary.run_id}")
    print(f"Ledger: {summary.ledger_path.as_posix()}")
    for record in summary.records:
        action = "生成" if record.status == "generated" else "スキップ"
        print(
            f"{action}: {record.scenario_id}/{record.line_id}/"
            f"take-{record.take_index:04d} "
            f"生成={record.generation_seconds:.3f}s RTF={record.rtf:.3f}",
        )
    if summary.failures:
        print("失敗サマリ:")
        for failure in summary.failures:
            print(
                f"  - {failure.scenario_id}/{failure.line_id}/"
                f"take-{failure.take_index:04d}: "
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
    print(f"Ledger: {summary.ledger_path.as_posix()}")
    print(f"QC report: {summary.report_path.as_posix()}")
    if summary.snapshot_path is not None:
        print(f"v4 snapshot: {summary.snapshot_path.as_posix()}")
    print(
        f"完了: take {summary.attempt_count} / "
        f"eligible {summary.eligible_count} / "
        f"hard reject {summary.hard_rejected_count} / "
        f"blocked {summary.blocked_count} / "
        f"generation failure {summary.generation_failed_count} / "
        f"未完了 {summary.pending_count}",
    )
