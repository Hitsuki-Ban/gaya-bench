from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from gaya_pipeline.completion_anchor import (
    AnchorTopupDraftSummary,
    AnchorTopupGenerationSummary,
    AnchorTopupMergeSummary,
    AnchorTopupPlanSummary,
    CompletionAnchorError,
    RoleAnchorSelectionSummary,
    RoleReviewBundleSummary,
    build_role_review_bundle_v2,
    build_role_anchor_topup_draft,
    build_role_anchor_topup_plan,
    finalize_role_anchor_selection,
    load_anchor_review_plan,
    load_anchor_topup_plan,
    merge_role_anchor_topup,
    run_role_anchor_topup_generation,
)
from gaya_pipeline.completion_listen import (
    CompletionListeningError,
    CompletionListeningSummary,
    build_completion_listening_bundle,
    phase_b_generation_binding,
)
from gaya_pipeline.completion_plan import (
    CompletionPlanError,
    load_completion_plan,
)
from gaya_pipeline.completion_publish import (
    CompletionPublishError,
    CompletionPublishSummary,
    run_completion_publish,
)
from gaya_pipeline.completion_release import (
    CompletionReleaseError,
    CompletionReleaseSummary,
    finalize_completion_release,
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
from gaya_pipeline.public_audio import (
    PublicAudioError,
    PublicAudioSummary,
    verify_public_audio,
)
from gaya_pipeline.publish import (
    PublishError,
    PublishSummary,
    create_r2_client,
    run_publish,
)
from gaya_pipeline.qc import QCError, QCSummary, run_qc
from gaya_pipeline.qc_runtime import KanaWhisperQCRuntime
from gaya_pipeline.reference_bundles import (
    ReferenceBundleCatalogError,
    validate_reference_bundle_catalog,
)
from gaya_pipeline.release import (
    ReleaseError,
    ReleaseFinalizeSummary,
    finalize_release,
)
from gaya_pipeline.selection import AUTOMATIC_SELECTION_POLICY
from gaya_pipeline.validation import default_scenarios_dir, validate_scenarios
from gaya_pipeline.voice_assets import (
    default_voices_dir,
    validate_local_voice_assets,
)


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "--as-of は YYYY-MM-DD 形式で指定してください。",
        ) from error


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

    reference_bundles_parser = subparsers.add_parser(
        "reference-bundles",
        help="参考バンドル catalog を操作する",
    )
    reference_bundles_subparsers = reference_bundles_parser.add_subparsers(
        dest="reference_bundles_command",
        required=True,
    )
    reference_bundles_validate_parser = (
        reference_bundles_subparsers.add_parser(
            "catalog-validate",
            help="参考バンドル catalog の公開メタデータを厳密検証する",
        )
    )
    reference_bundles_validate_parser.add_argument(
        "--catalog",
        required=True,
        type=Path,
        help="catalog の絶対パス",
    )
    reference_bundles_validate_parser.add_argument(
        "--as-of",
        required=True,
        type=_parse_iso_date,
        help="production 権利を判定する基準日（YYYY-MM-DD）",
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

    completion_parser = subparsers.add_parser(
        "completion",
        help="frozen planの597 replacementと691 inheritedを確定する",
    )
    completion_subparsers = completion_parser.add_subparsers(
        dest="completion_command",
        required=True,
    )
    completion_generate_parser = completion_subparsers.add_parser(
        "generate",
        help="canonical plan の対象をmodel policyどおり生成する",
    )
    completion_generate_parser.add_argument("--plan", required=True, type=Path)
    completion_generate_parser.add_argument(
        "--base-manifest",
        required=True,
        type=Path,
    )
    completion_generate_parser.add_argument("--model", required=True)
    completion_generate_parser.add_argument(
        "--artifacts",
        required=True,
        type=Path,
    )
    completion_generate_parser.add_argument(
        "--scenarios",
        required=True,
        type=Path,
    )
    completion_generate_parser.add_argument(
        "--voices",
        required=True,
        type=Path,
    )
    completion_generate_parser.add_argument(
        "--anchor-selection",
        required=True,
        type=Path,
        help="role epochを固定する確定role anchor selection",
    )
    completion_generate_parser.add_argument(
        "--run-kind",
        required=True,
        choices=("primary", "topup"),
    )
    completion_generate_parser.add_argument(
        "--supersedes-run-id",
        help="topupが整組取代する明示run ID",
    )
    completion_generate_parser.add_argument(
        "--seed-base",
        type=int,
        help="seedを使うtopupの派生基準。primaryはplan policyとexact一致が必要",
    )
    completion_generate_parser.add_argument(
        "--target",
        action="append",
        dest="targets",
        default=[],
        metavar="SCENARIO/LINE",
        help="topupで整組取代するplan内group（繰返し指定）",
    )

    completion_anchor_topup_plan_parser = completion_subparsers.add_parser(
        "anchor-topup-plan",
        help="final decisionからPhase Aの再生成対象とattempt 5..8を固定する",
    )
    for name in ("plan", "candidate-set", "decision", "output"):
        completion_anchor_topup_plan_parser.add_argument(
            f"--{name}",
            required=True,
            type=Path,
        )

    completion_anchor_topup_generate_parser = completion_subparsers.add_parser(
        "anchor-topup-generate",
        help="固定topup planを単一modelの独立runとして生成する",
    )
    for name in ("plan", "candidate-set", "decision", "topup-plan", "artifacts"):
        completion_anchor_topup_generate_parser.add_argument(
            f"--{name}",
            required=True,
            type=Path,
        )
    completion_anchor_topup_generate_parser.add_argument("--model", required=True)
    completion_anchor_topup_generate_parser.add_argument("--run-id", required=True)

    completion_anchor_topup_merge_parser = completion_subparsers.add_parser(
        "anchor-topup-merge",
        help="topup対象groupをN4で整組置換して106x4 candidate setを作る",
    )
    for name in (
        "plan",
        "candidate-set",
        "decision",
        "topup-plan",
        "artifacts",
        "output",
    ):
        completion_anchor_topup_merge_parser.add_argument(
            f"--{name}",
            required=True,
            type=Path,
        )
    completion_anchor_topup_merge_parser.add_argument(
        "--run-id",
        action="append",
        dest="run_ids",
        required=True,
    )

    completion_anchor_topup_draft_parser = completion_subparsers.add_parser(
        "anchor-topup-draft",
        help="非対象判断を継承しtopup対象だけを未確認へ戻すdraftを作る",
    )
    for name in (
        "plan",
        "source-candidate-set",
        "decision",
        "topup-plan",
        "merged-candidate-set",
        "merged-bundle",
        "output",
    ):
        completion_anchor_topup_draft_parser.add_argument(
            f"--{name}",
            required=True,
            type=Path,
        )

    completion_anchor_review_build_parser = completion_subparsers.add_parser(
        "anchor-review-build",
        help="固定N4候補からrole-review-v2聴取directoryを構築する",
    )
    for name in ("plan", "candidate-set", "artifacts", "output"):
        completion_anchor_review_build_parser.add_argument(
            f"--{name}",
            required=True,
            type=Path,
        )

    completion_anchor_review_finalize_parser = completion_subparsers.add_parser(
        "anchor-review-finalize",
        help=(
            "daemonのrole-review-anchor-decision-v2.jsonを"
            "Phase B用anchor selectionへ回収する。no-usable groupがあれば停止する"
        ),
    )
    for name in ("plan", "candidate-set", "bundle"):
        completion_anchor_review_finalize_parser.add_argument(
            f"--{name}",
            required=True,
            type=Path,
        )
    completion_anchor_review_finalize_parser.add_argument(
        "--decision",
        required=True,
        type=Path,
        help=(
            "daemonが保存したrole-review-anchor-decision-v2.json。"
            "no_usable_candidate=trueは再生成根拠として保持され、selection化しない"
        ),
    )
    completion_anchor_review_finalize_parser.add_argument(
        "--output",
        required=True,
        type=Path,
    )

    completion_qc_parser = completion_subparsers.add_parser(
        "qc",
        help="Phase B runへQCを実行する",
    )
    completion_qc_parser.add_argument("--run-id", required=True)
    completion_qc_parser.add_argument(
        "--artifacts",
        required=True,
        type=Path,
    )
    completion_qc_parser.add_argument(
        "--scenarios",
        required=True,
        type=Path,
    )
    completion_qc_parser.add_argument(
        "--voices",
        required=True,
        type=Path,
    )
    completion_qc_parser.add_argument(
        "--qc-model-root",
        required=True,
        type=Path,
    )

    completion_listen_parser = completion_subparsers.add_parser(
        "listen",
        help="全Phase B sourceから専用listening bundleを構築する",
    )
    completion_listen_parser.add_argument("--plan", required=True, type=Path)
    completion_listen_parser.add_argument(
        "--base-manifest",
        required=True,
        type=Path,
    )
    completion_listen_parser.add_argument(
        "--primary-run-id",
        action="append",
        dest="primary_run_ids",
        required=True,
    )
    completion_listen_parser.add_argument(
        "--topup-run-id",
        action="append",
        dest="topup_run_ids",
        default=[],
    )
    completion_listen_parser.add_argument(
        "--anchor-selection",
        required=True,
        type=Path,
    )
    completion_listen_parser.add_argument(
        "--artifacts",
        required=True,
        type=Path,
    )
    completion_listen_parser.add_argument(
        "--scenarios",
        required=True,
        type=Path,
    )
    completion_listen_parser.add_argument(
        "--voices",
        required=True,
        type=Path,
    )
    completion_listen_parser.add_argument(
        "--output",
        required=True,
        type=Path,
    )

    completion_finalize_parser = completion_subparsers.add_parser(
        "finalize",
        help="公開済みbaseと597 replacement decisionからreleaseを確定する",
    )
    completion_finalize_parser.add_argument(
        "--base-manifest",
        required=True,
        type=Path,
    )
    completion_finalize_parser.add_argument(
        "--qwen-curation",
        required=True,
        type=Path,
    )
    completion_finalize_parser.add_argument("--plan", required=True, type=Path)
    completion_finalize_parser.add_argument(
        "--decision",
        required=True,
        type=Path,
    )
    completion_finalize_parser.add_argument(
        "--source-audit",
        required=True,
        type=Path,
    )
    completion_finalize_parser.add_argument(
        "--primary-run-id",
        action="append",
        dest="primary_run_ids",
        required=True,
    )
    completion_finalize_parser.add_argument(
        "--topup-run-id",
        action="append",
        dest="topup_run_ids",
        default=[],
    )
    completion_finalize_parser.add_argument(
        "--anchor-selection",
        required=True,
        type=Path,
    )
    completion_finalize_parser.add_argument(
        "--artifacts",
        required=True,
        type=Path,
    )
    completion_finalize_parser.add_argument(
        "--scenarios",
        required=True,
        type=Path,
    )
    completion_finalize_parser.add_argument(
        "--voices",
        required=True,
        type=Path,
    )
    completion_finalize_parser.add_argument(
        "--output",
        required=True,
        type=Path,
    )

    completion_publish_parser = completion_subparsers.add_parser(
        "publish",
        help="immutable音声を検証後にmanifestをactivateする",
    )
    completion_publish_parser.add_argument(
        "--release",
        required=True,
        type=Path,
    )
    completion_publish_parser.add_argument(
        "--artifacts",
        required=True,
        type=Path,
    )
    completion_publish_parser.add_argument(
        "--source-audit",
        required=True,
        type=Path,
    )
    completion_publish_parser.add_argument(
        "--env-file",
        required=True,
        type=Path,
    )
    completion_publish_parser.add_argument(
        "--manifest-activation",
        required=True,
        type=Path,
    )
    completion_publish_parser.add_argument(
        "--publish-receipt",
        required=True,
        type=Path,
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
        help="N3 pilot bundle を構築して解析する",
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

    takes_parser = subparsers.add_parser(
        "takes",
        help="terminal run を固定 release に確定する",
    )
    takes_subparsers = takes_parser.add_subparsers(
        dest="takes_command",
        required=True,
    )
    takes_finalize_parser = takes_subparsers.add_parser(
        "finalize",
        help="複数runを明示selection policyでproduction releaseへ集約する",
    )
    takes_finalize_parser.add_argument(
        "--run-id",
        required=True,
        action="append",
        dest="run_ids",
        help="artifacts/takes 配下のterminal run（複数回指定可）",
    )
    takes_finalize_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="新規作成するrelease directory",
    )
    takes_finalize_parser.add_argument(
        "--projection-plan",
        type=Path,
        help="保持済みreleaseの単一modelを現行targetへ明示投影するcanonical plan",
    )
    takes_finalize_parser.add_argument(
        "--selection-policy",
        choices=(AUTOMATIC_SELECTION_POLICY,),
        help=(
            "未策展N=1 candidateを自動gate metadataで明示選定する"
            "（省略時はfully-human-curatedのみ）"
        ),
    )

    publish_parser = subparsers.add_parser(
        "publish",
        help="固定 release manifest v4 の candidate を R2 へ immutable publish する",
    )
    publish_parser.add_argument(
        "--release",
        required=True,
        type=Path,
        help="finalized release directory",
    )
    publish_parser.add_argument(
        "--takes-root",
        required=True,
        type=Path,
        help="provenance source run を格納する明示的 takes root",
    )
    publish_parser.add_argument(
        "--env-file",
        required=True,
        type=Path,
        help="R2 credential を格納する明示的 env file",
    )

    launch_parser = subparsers.add_parser(
        "launch",
        help="公開前 QA を実行する",
    )
    launch_subparsers = launch_parser.add_subparsers(
        dest="launch_command",
        required=True,
    )
    launch_audio_parser = launch_subparsers.add_parser(
        "verify-audio",
        help="selected 公開音声を全件取得して完全 decode する",
    )
    launch_audio_parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="公開済み release manifest v4",
    )
    launch_audio_parser.add_argument(
        "--base-url",
        required=True,
        help="末尾が / の公開音声 HTTPS base URL",
    )
    launch_audio_parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="並列検証数 (default: 8)",
    )
    launch_audio_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="各 GET / decode の timeout 秒 (default: 30)",
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

    if args.command == "reference-bundles":
        if args.reference_bundles_command != "catalog-validate":
            raise AssertionError(
                "unknown reference-bundles command: "
                f"{args.reference_bundles_command}",
            )
        try:
            summary = validate_reference_bundle_catalog(
                args.catalog,
                as_of=args.as_of,
            )
        except ReferenceBundleCatalogError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print(
            f"検証成功: {summary.bundle_count} バンドル / "
            f"{summary.assignment_count} 割当 / "
            f"{summary.synthetic_policy_count} 合成ポリシー",
        )
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

    if args.command == "completion":
        path_names = {
            "generate": (
                "plan",
                "base_manifest",
                "artifacts",
                "scenarios",
                "voices",
                "anchor_selection",
            ),
            "anchor-topup-plan": (
                "plan",
                "candidate_set",
                "decision",
                "output",
            ),
            "anchor-topup-generate": (
                "plan",
                "candidate_set",
                "decision",
                "topup_plan",
                "artifacts",
            ),
            "anchor-topup-merge": (
                "plan",
                "candidate_set",
                "decision",
                "topup_plan",
                "artifacts",
                "output",
            ),
            "anchor-topup-draft": (
                "plan",
                "source_candidate_set",
                "decision",
                "topup_plan",
                "merged_candidate_set",
                "merged_bundle",
                "output",
            ),
            "anchor-review-build": (
                "plan",
                "candidate_set",
                "artifacts",
                "output",
            ),
            "anchor-review-finalize": (
                "plan",
                "candidate_set",
                "bundle",
                "decision",
                "output",
            ),
            "qc": (
                "artifacts",
                "scenarios",
                "voices",
                "qc_model_root",
            ),
            "listen": (
                "plan",
                "base_manifest",
                "anchor_selection",
                "artifacts",
                "scenarios",
                "voices",
                "output",
            ),
            "finalize": (
                "base_manifest",
                "qwen_curation",
                "source_audit",
                "plan",
                "decision",
                "anchor_selection",
                "artifacts",
                "scenarios",
                "voices",
                "output",
            ),
            "publish": (
                "release",
                "artifacts",
                "source_audit",
                "env_file",
                "manifest_activation",
                "publish_receipt",
            ),
        }[args.completion_command]
        relative_paths = [
            f"--{name.replace('_', '-')}"
            for name in path_names
            if getattr(args, name) is not None
            and not getattr(args, name).is_absolute()
        ]
        if relative_paths:
            print(
                "ERROR: completion path は絶対pathが必要です: "
                + ", ".join(relative_paths),
                file=sys.stderr,
            )
            return 1

        if args.completion_command.startswith("anchor-topup-"):
            source_candidate_set = (
                args.source_candidate_set
                if args.completion_command == "anchor-topup-draft"
                else args.candidate_set
            )
            try:
                plan = load_anchor_topup_plan(
                    plan_path=args.plan,
                    source_candidate_set_path=source_candidate_set,
                )
                if args.completion_command == "anchor-topup-plan":
                    summary = build_role_anchor_topup_plan(
                        plan=plan,
                        candidate_set_path=args.candidate_set,
                        decision_path=args.decision,
                        output_path=args.output,
                    )
                    _print_anchor_topup_plan_summary(summary)
                    return 0
                if args.completion_command == "anchor-topup-generate":
                    generation_summary = run_role_anchor_topup_generation(
                        plan=plan,
                        candidate_set_path=args.candidate_set,
                        decision_path=args.decision,
                        topup_plan_path=args.topup_plan,
                        model_id=args.model,
                        run_id=args.run_id,
                        artifacts_dir=args.artifacts,
                    )
                    _print_anchor_topup_generation_summary(generation_summary)
                    return (
                        1
                        if generation_summary.rejected_count
                        or generation_summary.failed_count
                        else 0
                    )
                if args.completion_command == "anchor-topup-merge":
                    merge_summary = merge_role_anchor_topup(
                        plan=plan,
                        candidate_set_path=args.candidate_set,
                        decision_path=args.decision,
                        topup_plan_path=args.topup_plan,
                        run_ids=args.run_ids,
                        artifacts_dir=args.artifacts,
                        output_path=args.output,
                    )
                    _print_anchor_topup_merge_summary(merge_summary)
                    return 0
                draft_summary = build_role_anchor_topup_draft(
                    plan=plan,
                    source_candidate_set_path=args.source_candidate_set,
                    decision_path=args.decision,
                    topup_plan_path=args.topup_plan,
                    merged_candidate_set_path=args.merged_candidate_set,
                    merged_bundle_dir=args.merged_bundle,
                    output_path=args.output,
                )
                _print_anchor_topup_draft_summary(draft_summary)
                return 0
            except CompletionAnchorError as error:
                print(f"ERROR: {error}", file=sys.stderr)
                return 1

        if args.completion_command == "anchor-review-build":
            try:
                plan = load_anchor_review_plan(
                    plan_path=args.plan,
                    candidate_set_path=args.candidate_set,
                )
                summary = build_role_review_bundle_v2(
                    plan=plan,
                    candidate_set_path=args.candidate_set,
                    artifacts_dir=args.artifacts,
                    output_dir=args.output,
                )
            except CompletionAnchorError as error:
                print(f"ERROR: {error}", file=sys.stderr)
                return 1
            _print_role_review_bundle_summary(summary)
            return 0

        if args.completion_command == "anchor-review-finalize":
            try:
                plan = load_anchor_review_plan(
                    plan_path=args.plan,
                    candidate_set_path=args.candidate_set,
                )
                summary = finalize_role_anchor_selection(
                    plan=plan,
                    candidate_set_path=args.candidate_set,
                    bundle_dir=args.bundle,
                    decision_path=args.decision,
                    output_dir=args.output,
                )
            except CompletionAnchorError as error:
                print(f"ERROR: {error}", file=sys.stderr)
                return 1
            _print_role_anchor_selection_summary(summary)
            return 0

        if args.completion_command == "generate":
            try:
                plan = load_completion_plan(
                    args.plan,
                    base_manifest_path=args.base_manifest,
                    scenarios_dir=args.scenarios,
                    voices_dir=args.voices,
                )
                target_lines = plan.target_lines_for_model(args.model)
                if not target_lines:
                    raise GenerationError(
                        f"completion plan 対象外 model です: {args.model}",
                    )
                policy = plan.policy_for_model(args.model)
                anchor_selection_sha256, role_epochs = phase_b_generation_binding(
                    plan=plan,
                    model=args.model,
                    scenarios_dir=args.scenarios,
                    anchor_selection_path=args.anchor_selection,
                )
                if args.run_kind == "primary":
                    if args.targets:
                        raise GenerationError(
                            "primary runに--targetは指定できません。",
                        )
                    if args.supersedes_run_id is not None:
                        raise GenerationError(
                            "primary runに--supersedes-run-idは指定できません。",
                        )
                    if args.seed_base != policy.primary_seed_base:
                        raise GenerationError(
                            "primary runの--seed-baseはmodel policyと"
                            f"exact一致が必要です: {policy.primary_seed_base!r}",
                        )
                else:
                    if policy.seed_policy == "none":
                        raise GenerationError(
                            f"seedを持たないmodelはtopupできません: {args.model}",
                        )
                    if args.seed_base is None:
                        raise GenerationError(
                            "topup runには--seed-baseが必要です。",
                        )
                    if args.supersedes_run_id is None:
                        raise GenerationError(
                            "topup runに--supersedes-run-idが必要です。",
                        )
                    target_lines = _parse_completion_targets(
                        args.targets,
                        allowed=set(target_lines),
                    )
                    role_epochs = {
                        identity: role_epochs[identity]
                        for identity in target_lines
                    }
                summary = run_generation(
                    model_id=args.model,
                    scenarios_dir=args.scenarios,
                    artifacts_dir=args.artifacts,
                    voices_dir=args.voices,
                    target_lines=target_lines,
                    takes=policy.takes,
                    seed_base=args.seed_base,
                    completion_plan_sha256=plan.plan_id,
                    role_epochs=role_epochs,
                    run_kind=args.run_kind,
                    supersedes_run_id=args.supersedes_run_id,
                    role_anchor_selection_path=(
                        args.anchor_selection
                        if args.model
                        in {
                            "qwen3-tts-12hz-1.7b",
                            "irodori-tts-600m-v3-voicedesign",
                        }
                        else None
                    ),
                    role_anchor_plan_sha256=(
                        plan.anchor_source_plan_sha256
                        if args.model
                        in {
                            "qwen3-tts-12hz-1.7b",
                            "irodori-tts-600m-v3-voicedesign",
                        }
                        else None
                    ),
                    role_anchor_selection_sha256=(
                        anchor_selection_sha256
                        if args.model
                        in {
                            "qwen3-tts-12hz-1.7b",
                            "irodori-tts-600m-v3-voicedesign",
                        }
                        else None
                    ),
                )
            except (
                CompletionPlanError,
                CompletionListeningError,
                GenerationError,
            ) as error:
                print(f"ERROR: {error}", file=sys.stderr)
                return 1
            _print_generation_summary(summary)
            return 1 if summary.failed_count else 0

        if args.completion_command == "qc":
            try:
                summary = run_qc(
                    run_id=args.run_id,
                    scenarios_dir=args.scenarios,
                    artifacts_dir=args.artifacts,
                    voices_dir=args.voices,
                    runtime=KanaWhisperQCRuntime(args.qc_model_root),
                )
            except QCError as error:
                print(f"ERROR: {error}", file=sys.stderr)
                return 1
            _print_qc_summary(summary)
            return 1 if summary.blocked_count or summary.pending_count else 0

        if args.completion_command == "listen":
            try:
                plan = load_completion_plan(
                    args.plan,
                    base_manifest_path=args.base_manifest,
                    scenarios_dir=args.scenarios,
                    voices_dir=args.voices,
                )
                summary = build_completion_listening_bundle(
                    plan=plan,
                    primary_run_ids=args.primary_run_ids,
                    topup_run_ids=args.topup_run_ids,
                    anchor_selection_path=args.anchor_selection,
                    artifacts_dir=args.artifacts,
                    scenarios_dir=args.scenarios,
                    voices_dir=args.voices,
                    output_dir=args.output,
                )
            except (CompletionPlanError, CompletionListeningError) as error:
                print(f"ERROR: {error}", file=sys.stderr)
                return 1
            _print_completion_listening_summary(summary)
            return 0

        if args.completion_command == "finalize":
            try:
                plan = load_completion_plan(
                    args.plan,
                    base_manifest_path=args.base_manifest,
                    scenarios_dir=args.scenarios,
                    voices_dir=args.voices,
                )
                summary = finalize_completion_release(
                    plan=plan,
                    base_manifest_path=args.base_manifest,
                    qwen_curation_path=args.qwen_curation,
                    source_audit_path=args.source_audit,
                    decision_path=args.decision,
                    primary_run_ids=args.primary_run_ids,
                    topup_run_ids=args.topup_run_ids,
                    anchor_selection_path=args.anchor_selection,
                    artifacts_dir=args.artifacts,
                    scenarios_dir=args.scenarios,
                    voices_dir=args.voices,
                    output_dir=args.output,
                )
            except (CompletionPlanError, CompletionReleaseError) as error:
                print(f"ERROR: {error}", file=sys.stderr)
                return 1
            _print_completion_release_summary(summary)
            return 0

        if args.completion_command == "publish":
            try:
                summary = run_completion_publish(
                    release_dir=args.release,
                    artifacts_dir=args.artifacts,
                    source_audit_path=args.source_audit,
                    client=create_r2_client(args.env_file),
                    manifest_activation_path=args.manifest_activation,
                    publish_receipt_path=args.publish_receipt,
                )
            except (CompletionPublishError, PublishError) as error:
                print(f"ERROR: {error}", file=sys.stderr)
                return 1
            _print_completion_publish_summary(summary)
            return 0

        raise AssertionError(
            f"unknown completion command: {args.completion_command}",
        )

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

    if args.command == "takes":
        if args.takes_command != "finalize":
            raise AssertionError(f"unknown takes command: {args.takes_command}")
        repository_root = default_scenarios_dir().parent
        try:
            summary = finalize_release(
                run_ids=args.run_ids,
                artifacts_dir=repository_root / "artifacts",
                data_dir=repository_root / "data",
                scenarios_dir=repository_root / "scenarios",
                output_dir=args.output,
                projection_plan_path=args.projection_plan,
                selection_policy=args.selection_policy,
            )
        except ReleaseError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        _print_release_finalize_summary(summary)
        return 0

    if args.command == "publish":
        try:
            summary = run_publish(
                release_dir=args.release,
                takes_root=args.takes_root,
                client=create_r2_client(args.env_file),
            )
        except PublishError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        _print_publish_summary(summary)
        return 0

    if args.command == "launch":
        if args.launch_command != "verify-audio":
            raise AssertionError(
                f"unknown launch command: {args.launch_command}",
            )
        try:
            summary = verify_public_audio(
                manifest_path=args.manifest,
                base_url=args.base_url,
                workers=args.workers,
                timeout_seconds=args.timeout_seconds,
            )
        except PublicAudioError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        _print_public_audio_summary(summary)
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


def _parse_completion_targets(
    values: Sequence[str],
    *,
    allowed: set[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    if not values:
        raise GenerationError("topup runは--targetを1件以上必要です。")
    targets: list[tuple[str, str]] = []
    for value in values:
        parts = value.split("/")
        if len(parts) != 2 or not all(parts):
            raise GenerationError("--targetはSCENARIO/LINE形式が必要です。")
        identity = (parts[0], parts[1])
        if identity not in allowed:
            raise GenerationError(
                f"--targetはfrozen planのmodel対象外です: {value}",
            )
        targets.append(identity)
    if len(targets) != len(set(targets)):
        raise GenerationError("--targetが重複しています。")
    return tuple(sorted(targets))


def _print_completion_listening_summary(
    summary: CompletionListeningSummary,
) -> None:
    print(f"Listening bundle: {summary.output_dir.as_posix()}")
    print(f"Candidate set SHA-256: {summary.candidate_set_sha256}")
    print(f"Source map SHA-256: {summary.source_map_sha256}")
    print(
        f"完了: model {summary.model_count} / group {summary.group_count} / "
        f"candidate {summary.candidate_count}",
    )


def _print_role_review_bundle_summary(summary: RoleReviewBundleSummary) -> None:
    print(f"Role review bundle: {summary.output_dir.as_posix()}")
    print(f"role-review-v2.json SHA-256: {summary.review_sha256}")
    print(
        f"完了: group {summary.group_count} / candidate {summary.candidate_count}",
    )


def _print_anchor_topup_plan_summary(summary: AnchorTopupPlanSummary) -> None:
    print(f"Anchor topup plan: {summary.path.as_posix()}")
    print(
        f"完了: target {summary.target_count} / attempt {summary.attempt_count}",
    )


def _print_anchor_topup_generation_summary(
    summary: AnchorTopupGenerationSummary,
) -> None:
    print(f"Anchor topup ledger: {summary.ledger_path.as_posix()}")
    print(
        f"完了: eligible {summary.eligible_count} / "
        f"rejected {summary.rejected_count} / failed {summary.failed_count}",
    )


def _print_anchor_topup_merge_summary(summary: AnchorTopupMergeSummary) -> None:
    print(f"Merged anchor candidate set: {summary.path.as_posix()}")
    print(f"Candidate set SHA-256: {summary.candidate_set_sha256}")
    print(
        f"完了: replaced group {summary.replaced_group_count} / "
        f"candidate {summary.candidate_count}",
    )


def _print_anchor_topup_draft_summary(summary: AnchorTopupDraftSummary) -> None:
    print(f"Inherited role review draft: {summary.path.as_posix()}")
    print(
        f"完了: inherited {summary.inherited_count} / reset {summary.reset_count}",
    )


def _print_role_anchor_selection_summary(
    summary: RoleAnchorSelectionSummary,
) -> None:
    print(f"Role anchor selection: {summary.output_dir.as_posix()}")
    print(f"Selection SHA-256: {summary.selection_sha256}")
    print(f"完了: selected {summary.selected_count}")


def _print_completion_release_summary(
    summary: CompletionReleaseSummary,
) -> None:
    print(f"Release: {summary.output_dir.as_posix()}")
    print(f"Manifest SHA-256: {summary.manifest_sha256}")
    print(f"Candidate set SHA-256: {summary.candidate_set_sha256}")
    print(f"Selection SHA-256: {summary.selection_sha256}")
    print(
        f"完了: candidate {summary.candidate_count} / "
        f"selected {summary.selected_count} / "
        f"replacement candidate {summary.replacement_candidate_count}",
    )


def _print_completion_publish_summary(
    summary: CompletionPublishSummary,
) -> None:
    print(
        f"完了: inherited検証 {summary.inherited_count} / "
        f"新規upload {summary.uploaded_count} / "
        f"既存replacement {summary.skipped_count}",
    )
    print(f"Manifest activation: {summary.manifest_activation_path}")
    print(f"Publish receipt: {summary.publish_receipt_path}")


def _print_publish_summary(summary: PublishSummary) -> None:
    for record in summary.records:
        action = "アップロード" if record.status == "uploaded" else "スキップ"
        print(f"{action}: {record.key} ({record.size_bytes} bytes)")
    print(
        f"完了: アップロード {summary.uploaded_count} / "
        f"スキップ {summary.skipped_count}",
    )


def _print_public_audio_summary(summary: PublicAudioSummary) -> None:
    print(
        f"検証成功: {summary.verified_count} clips / "
        f"{summary.total_bytes} bytes / "
        f"{summary.elapsed_seconds:.3f}s",
    )


def _print_release_finalize_summary(
    summary: ReleaseFinalizeSummary,
) -> None:
    print(f"Release: {summary.output_dir.as_posix()}")
    print(f"Manifest SHA-256: {summary.manifest_sha256}")
    print(f"Candidate set SHA-256: {summary.candidate_set_sha256}")
    print(f"Curation SHA-256: {summary.curation_sha256}")
    print(
        f"完了: model {summary.model_count} / "
        f"candidate {summary.candidate_count} / "
        f"selected {summary.selected_count} / "
        f"skipped {summary.skipped_count} / "
        f"failure {summary.failure_count}",
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
