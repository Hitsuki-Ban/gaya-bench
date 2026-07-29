from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from gaya_pipeline.baseline import (
    BaselineAssembleSummary,
    BaselineError,
    BaselineFinalizeSummary,
    BaselinePlanSummary,
    assemble_baseline,
    finalize_baseline,
    plan_baseline,
)
from gaya_pipeline.curation import (
    CurationError,
    CurationSummary,
    apply_curation,
)
from gaya_pipeline.generation import (
    GenerationError,
    GenerationSummary,
    run_generation,
)
from gaya_pipeline.intonation_report import (
    IntonationReportError,
    IntonationReportSummary,
    build_intonation_report,
)
from gaya_pipeline.pilot import (
    PilotAnalysisSummary,
    PilotBuildSummary,
    PilotError,
    analyze_pilot_bundle,
    build_pilot_bundle,
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
        "--selection",
        type=Path,
        help="baseline-plan-v1 による model group selection",
    )
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

    baseline_parser = subparsers.add_parser(
        "baseline",
        help="公開 baseline v4 bundle を準備する",
    )
    baseline_subparsers = baseline_parser.add_subparsers(
        dest="baseline_command",
        required=True,
    )
    baseline_plan_parser = baseline_subparsers.add_parser(
        "plan",
        help="公開 manifest v3 から canonical baseline plan を作る",
    )
    baseline_plan_parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="source manifest v3",
    )
    baseline_plan_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="新規作成する baseline plan JSON",
    )
    baseline_assemble_parser = baseline_subparsers.add_parser(
        "assemble",
        help="7 model の terminal run と legacy 音声を bundle 化する",
    )
    baseline_assemble_parser.add_argument(
        "--plan",
        required=True,
        type=Path,
        help="canonical baseline-plan-v1 JSON",
    )
    baseline_assemble_parser.add_argument(
        "--run-id",
        required=True,
        action="append",
        dest="run_ids",
        help="model ごとの terminal run id（7 回指定）",
    )
    baseline_assemble_parser.add_argument(
        "--legacy-root",
        required=True,
        type=Path,
        help="v3 public_path を格納する明示的 artifacts root",
    )
    baseline_assemble_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="新規作成する baseline bundle directory",
    )
    baseline_finalize_parser = baseline_subparsers.add_parser(
        "finalize",
        help="baseline bundle と全 decision から release v4 を確定する",
    )
    baseline_finalize_parser.add_argument(
        "--bundle",
        required=True,
        type=Path,
        help="assemble 済み baseline bundle",
    )
    baseline_finalize_parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="baseline-curation-v1 JSON",
    )
    baseline_finalize_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="新規作成する finalized baseline directory",
    )

    intonation_parser = subparsers.add_parser(
        "intonation",
        help="語尾イントネーションの分布を解析する",
    )
    intonation_subparsers = intonation_parser.add_subparsers(
        dest="intonation_command",
        required=True,
    )
    intonation_report_parser = intonation_subparsers.add_parser(
        "report",
        help="eligible take の model×gender 分布レポートを作成する",
    )
    intonation_report_parser.add_argument(
        "--run-id",
        required=True,
        action="append",
        dest="run_ids",
        help="artifacts/takes 配下の terminal run id（複数回指定可）",
    )
    intonation_report_parser.add_argument(
        "--artifacts",
        required=True,
        type=Path,
        help="artifacts directory",
    )
    intonation_report_parser.add_argument(
        "--scenarios",
        required=True,
        type=Path,
        help="current scenarios directory",
    )
    intonation_report_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="新規作成する report directory",
    )

    curate_parser = subparsers.add_parser(
        "curate",
        help="local v4 snapshot に人評 decision artifact を適用する",
    )
    curate_subparsers = curate_parser.add_subparsers(
        dest="curate_command",
        required=True,
    )
    curate_apply_parser = curate_subparsers.add_parser(
        "apply",
        help="curation format v1 artifact を検証して適用する",
    )
    curate_apply_parser.add_argument(
        "--run-id",
        required=True,
        help="artifacts/takes 配下の terminal generation run id",
    )
    curate_apply_parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="curation format v1 JSON artifact",
    )

    pilot_parser = subparsers.add_parser(
        "pilot",
        help="N3 pilot bundle を構築・解析する",
    )
    pilot_subparsers = pilot_parser.add_subparsers(
        dest="pilot_command",
        required=True,
    )
    pilot_build_parser = pilot_subparsers.add_parser(
        "build",
        help="6 個の terminal run から blind pilot bundle を構築する",
    )
    pilot_build_parser.add_argument(
        "--run-id",
        required=True,
        action="append",
        dest="run_ids",
        help="3 model × 2 scenario の run id（6 回指定）",
    )
    pilot_build_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="新規作成する pilot bundle directory",
    )
    pilot_analyze_parser = pilot_subparsers.add_parser(
        "analyze",
        help="pilot bundle と decision v1 を厳密検証して解析する",
    )
    pilot_analyze_parser.add_argument(
        "--bundle",
        required=True,
        type=Path,
        help="pilot-set.json と blind audio を含む bundle directory",
    )
    pilot_analyze_parser.add_argument(
        "--decision",
        required=True,
        type=Path,
        help="pilot decision v1 JSON",
    )
    pilot_analyze_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="新規作成する report directory",
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
                selection_path=args.selection,
                takes=args.takes,
                seed_base=args.seed_base,
                force=args.force,
            )
        except GenerationError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        _print_generation_summary(summary)
        return 1 if summary.failed_count else 0

    if args.command == "baseline":
        try:
            if args.baseline_command == "plan":
                summary = plan_baseline(
                    manifest_path=args.manifest,
                    output_path=args.output,
                )
                _print_baseline_plan_summary(summary)
                return 0
            repository_root = default_scenarios_dir().parent
            if args.baseline_command == "assemble":
                assembled = assemble_baseline(
                    plan_path=args.plan,
                    run_ids=args.run_ids,
                    output_dir=args.output,
                    artifacts_dir=repository_root / "artifacts",
                    legacy_root=args.legacy_root,
                    scenarios_dir=repository_root / "scenarios",
                )
                _print_baseline_assemble_summary(assembled)
                return 0
            if args.baseline_command == "finalize":
                finalized = finalize_baseline(
                    bundle_dir=args.bundle,
                    input_path=args.input,
                    output_dir=args.output,
                    scenarios_dir=repository_root / "scenarios",
                )
                _print_baseline_finalize_summary(finalized)
                return 0
            raise AssertionError(
                f"unknown baseline command: {args.baseline_command}",
            )
        except BaselineError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1

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

    if args.command == "intonation":
        if args.intonation_command != "report":
            raise AssertionError(
                f"unknown intonation command: {args.intonation_command}",
            )
        try:
            summary = build_intonation_report(
                run_ids=args.run_ids,
                artifacts_dir=args.artifacts,
                scenarios_dir=args.scenarios,
                output_dir=args.output,
            )
        except IntonationReportError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        _print_intonation_report_summary(summary)
        return 0

    if args.command == "curate":
        if args.curate_command != "apply":
            raise AssertionError(f"unknown curate command: {args.curate_command}")
        repository_root = default_scenarios_dir().parent
        try:
            summary = apply_curation(
                run_id=args.run_id,
                input_path=args.input,
                artifacts_dir=repository_root / "artifacts",
                data_dir=repository_root / "data",
                scenarios_dir=repository_root / "scenarios",
            )
        except CurationError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        _print_curation_summary(summary)
        return 0

    if args.command == "pilot":
        repository_root = default_scenarios_dir().parent
        try:
            if args.pilot_command == "build":
                summary = build_pilot_bundle(
                    run_ids=args.run_ids,
                    output_dir=args.output,
                    artifacts_dir=repository_root / "artifacts",
                    scenarios_dir=repository_root / "scenarios",
                )
                _print_pilot_build_summary(summary)
                return 0
            if args.pilot_command == "analyze":
                analysis = analyze_pilot_bundle(
                    bundle_dir=args.bundle,
                    decision_path=args.decision,
                    output_dir=args.output,
                )
                _print_pilot_analysis_summary(analysis)
                return 0
            raise AssertionError(f"unknown pilot command: {args.pilot_command}")
        except PilotError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1

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


def _print_baseline_plan_summary(summary: BaselinePlanSummary) -> None:
    print(f"Baseline plan: {summary.plan_path.as_posix()}")
    print(f"SHA-256: {summary.plan_sha256}")
    print(
        f"groups {summary.group_count} / models {summary.model_count} / "
        f"excluded failures {summary.excluded_failure_count}",
    )


def _print_baseline_assemble_summary(summary: BaselineAssembleSummary) -> None:
    print(f"Baseline bundle: {summary.bundle_dir.as_posix()}")
    print(f"Candidate set SHA-256: {summary.candidate_set_sha256}")
    print(f"Reference SHA-256: {summary.baseline_reference_sha256}")
    print(
        f"groups {summary.group_count} / candidates {summary.candidate_count} / "
        f"candidate zero {summary.failure_count}",
    )


def _print_baseline_finalize_summary(summary: BaselineFinalizeSummary) -> None:
    print(f"Baseline release: {summary.output_dir.as_posix()}")
    print(f"Decision SHA-256: {summary.decision_sha256}")
    print(f"Manifest SHA-256: {summary.release_manifest_sha256}")
    print(f"Audit SHA-256: {summary.audit_sha256}")
    print(
        f"candidate zero {summary.candidate_zero_count} / "
        f"selected {summary.selected_count} / skipped {summary.skipped_count}",
    )


def _print_publish_summary(summary: PublishSummary) -> None:
    for record in summary.records:
        action = "アップロード" if record.status == "uploaded" else "スキップ"
        print(f"{action}: {record.key} ({record.size_bytes} bytes)")
    print(
        f"完了: アップロード {summary.uploaded_count} / "
        f"スキップ {summary.skipped_count}",
    )


def _print_curation_summary(summary: CurationSummary) -> None:
    print(f"Candidate set: {summary.candidate_set_path.as_posix()}")
    print(
        f"Candidate set SHA marker: "
        f"{summary.candidate_set_marker_path.as_posix()}",
    )
    print(f"v4 snapshot: {summary.snapshot_path.as_posix()}")
    print(f"Curation artifact: {summary.artifact_path.as_posix()}")
    print(f"Curation SHA-256: {summary.curation_sha256}")
    print(
        f"完了: group {summary.group_count} / "
        f"新規 projection {summary.added_projection_count}",
    )


def _print_qc_summary(summary: QCSummary) -> None:
    print(f"Ledger: {summary.ledger_path.as_posix()}")
    print(f"QC report: {summary.report_path.as_posix()}")
    if summary.snapshot_path is not None:
        print(f"v4 snapshot: {summary.snapshot_path.as_posix()}")
    if summary.candidate_set_path is not None:
        print(f"Candidate set: {summary.candidate_set_path.as_posix()}")
    if summary.candidate_set_marker_path is not None:
        print(
            "Candidate set SHA marker: "
            f"{summary.candidate_set_marker_path.as_posix()}",
        )
    print(
        f"完了: take {summary.attempt_count} / "
        f"eligible {summary.eligible_count} / "
        f"hard reject {summary.hard_rejected_count} / "
        f"blocked {summary.blocked_count} / "
        f"generation failure {summary.generation_failed_count} / "
        f"未完了 {summary.pending_count} / "
        f"content review required {summary.content_review_required_count}",
    )


def _print_intonation_report_summary(
    summary: IntonationReportSummary,
) -> None:
    print(f"Intonation report JSON: {summary.json_path.as_posix()}")
    print(f"Intonation report Markdown: {summary.markdown_path.as_posix()}")
    print(
        f"完了: run {summary.run_count} / "
        f"eligible attempt {summary.eligible_attempt_count}",
    )


def _print_pilot_build_summary(summary: PilotBuildSummary) -> None:
    print(f"Pilot bundle: {summary.bundle_dir.as_posix()}")
    print(f"pilot-set.json: {summary.pilot_set_path.as_posix()}")
    print(f"pilot-set SHA-256: {summary.pilot_set_sha256}")
    print(f"完了: group {summary.group_count} / candidate {summary.candidate_count}")


def _print_pilot_analysis_summary(summary: PilotAnalysisSummary) -> None:
    print(f"Pilot report JSON: {summary.report_json_path.as_posix()}")
    print(f"Pilot report Markdown: {summary.report_markdown_path.as_posix()}")
    print(f"pilot-set SHA-256: {summary.pilot_set_sha256}")
    print(f"decision SHA-256: {summary.decision_sha256}")
