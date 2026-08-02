import { Badge } from "@/components/ui/badge";
import type { ModelCapabilities } from "@/data";

type ModelMethod = "text_prompt" | "voice_clone" | "preset";

const methodLabels = {
  text_prompt: "テキスト指示で声を生成",
  voice_clone: "参照音声からの声クローン",
  preset: "プリセット話者",
} as const satisfies Record<ModelMethod, string>;

function deriveModelMethod(capabilities: ModelCapabilities): ModelMethod {
  if (capabilities.voice_prompt) {
    return "text_prompt";
  }
  if (capabilities.clone) {
    return "voice_clone";
  }
  return "preset";
}

export function ModelMethodBadge({
  capabilities,
  compact = false,
}: {
  capabilities: ModelCapabilities;
  compact?: boolean;
}) {
  const method = deriveModelMethod(capabilities);
  const label = methodLabels[method];

  return (
    <Badge
      aria-label={`生成方式: ${label}`}
      className={
        compact
          ? "h-auto max-w-full px-1.5 py-1 text-center font-mono text-[9px] leading-3 whitespace-normal"
          : undefined
      }
      title={`生成方式: ${label}`}
      variant="secondary"
    >
      {label}
    </Badge>
  );
}
