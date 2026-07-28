import { Badge } from "@/components/ui/badge";
import type { CharacterKind } from "@/data";

export const CHARACTER_KIND_LABELS = {
  human: "人間",
  machine: "機械",
  creature: "魔物",
  spirit: "精霊",
} as const satisfies Readonly<Record<CharacterKind, string>>;

export function CharacterKindBadge({ kind }: { kind: CharacterKind }) {
  if (kind === "human") {
    return null;
  }

  return (
    <Badge className="font-mono text-[9px]" variant="secondary">
      {CHARACTER_KIND_LABELS[kind]}
    </Badge>
  );
}
