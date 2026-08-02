from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from gaya_pasqa_ranking.ranking import (
    RankingError,
    prepare_model_dir,
    run_batch_ranking,
    run_ranking,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gaya-pasqa")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser(
        "prepare",
        help="固定 revision の PASQA model files を取得・検証する",
    )
    prepare_parser.add_argument(
        "--model-dir",
        required=True,
        type=Path,
        help="checkpoint/config/vocab の保存先",
    )

    rank_parser = subparsers.add_parser(
        "rank",
        help="同一行・同一モデルの N take を相対順位付けする",
    )
    rank_parser.add_argument(
        "--model-dir",
        required=True,
        type=Path,
        help="prepare 済み PASQA model directory",
    )
    rank_parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="明示 mora token と N take を含む ranking input v1 JSON",
    )
    rank_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="新規作成する ranking report JSON",
    )

    batch_parser = subparsers.add_parser(
        "rank-batch",
        help="Phase B の全 group を1回のmodel loadで順位付けする",
    )
    batch_parser.add_argument("--model-dir", required=True, type=Path)
    batch_parser.add_argument("--input", required=True, type=Path)
    batch_parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_console()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            files = prepare_model_dir(args.model_dir)
            print(f"Checkpoint: {files.checkpoint.as_posix()}")
            print(f"Config: {files.config.as_posix()}")
            print(f"Vocab: {files.vocab.as_posix()}")
            print("PASQA model files verified.")
            return 0
        if args.command == "rank":
            result = run_ranking(
                model_dir=args.model_dir,
                input_path=args.input,
                output_path=args.output,
            )
            print(f"Ranking report: {args.output.resolve().as_posix()}")
            print(
                f"完了: group {result['group']['scenario_id']}/"
                f"{result['group']['line_id']} / take {len(result['rankings'])}"
            )
            return 0
        if args.command == "rank-batch":
            result = run_batch_ranking(
                model_dir=args.model_dir,
                input_path=args.input,
                output_path=args.output,
            )
            print(f"Batch ranking report: {args.output.resolve().as_posix()}")
            print(f"完了: group {len(result['groups'])}")
            return 0
        raise AssertionError(f"unknown command: {args.command}")
    except RankingError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
