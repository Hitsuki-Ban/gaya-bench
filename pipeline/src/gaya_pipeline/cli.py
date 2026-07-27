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
from gaya_pipeline.validation import default_scenarios_dir, validate_scenarios


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "validate":
        result = validate_scenarios(args.scenarios)
        if result.problems:
            for problem in result.problems:
                print(f"ERROR: {problem}", file=sys.stderr)
            print(
                f"検証失敗: {len(result.problems)} 件の問題があります。",
                file=sys.stderr,
            )
            return 1

        print(f"検証成功: {result.file_count} シナリオ")
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
        return 0

    raise AssertionError(f"unknown command: {args.command}")


def _print_generation_summary(summary: GenerationSummary) -> None:
    for record in summary.records:
        action = "生成" if record.status == "generated" else "スキップ"
        print(
            f"{action}: {record.scenario_id}/{record.line_id} "
            f"生成={record.generation_seconds:.3f}s RTF={record.rtf:.3f}",
        )
    print(
        f"完了: 生成 {summary.generated_count} / "
        f"スキップ {summary.skipped_count} / "
        f"所要時間 {summary.elapsed_seconds:.3f}s",
    )
