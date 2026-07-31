import { Link } from "react-router";

import type { PublishedCandidate } from "@/data";
import { referenceVoiceById } from "@/data";

type ReferenceConditioning = PublishedCandidate["reference_conditioning"];

export function ReferenceConditioningBadge({
  conditioning,
  tabIndex,
}: {
  conditioning: ReferenceConditioning;
  tabIndex?: 0 | -1;
}) {
  if (conditioning.kind === "none") {
    return (
      <span className="block min-w-0 text-[10px] leading-4 text-muted-foreground">
        参照音声：なし
      </span>
    );
  }

  if (conditioning.kind === "model_generated_reference") {
    return (
      <span className="block min-w-0 text-[10px] leading-4 text-muted-foreground">
        参照: モデル生成
      </span>
    );
  }

  const voice = referenceVoiceById.get(conditioning.voice_id);
  if (!voice) {
    throw new Error(`参照音声の表示情報がありません: ${conditioning.voice_id}`);
  }

  return (
    <Link
      className="block min-w-0 text-[10px] leading-4 text-primary underline-offset-2 [overflow-wrap:anywhere] hover:underline"
      tabIndex={tabIndex}
      to={`/reference-voices#${conditioning.voice_id}`}
    >
      参照: 収録音声（{voice.source.speaker}）
    </Link>
  );
}
