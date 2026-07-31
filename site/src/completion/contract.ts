import type { CompletionCatalog } from "./types";

export const COMPLETION_PLAN_MARKER = "completion-plan.sha256";
export const COMPLETION_PLAN_SHA256 =
  "a3d9480a6f38b7fdce3ae96f23d3382bc41e9d7be66caca1634eead16e148bb9";

const EXPECTED_COMPLETION_GROUPS = 45;
const EXPECTED_MODEL_GROUPS = new Map([
  ["chatterbox-multilingual-v3", 1],
  ["cosyvoice3-0.5b-2512", 2],
  ["qwen3-tts-12hz-1.7b", 40],
  ["voxcpm2", 2],
]);

export function assertCompletionCatalogContract(
  catalog: CompletionCatalog,
  planSha256: string,
): void {
  if (planSha256 !== COMPLETION_PLAN_SHA256) {
    throw new Error(`補録 plan SHA-256 がIssue #174の固定planと一致しません: ${planSha256}`);
  }
  if (catalog.manifestCurationCount !== 0 || catalog.manifestFailureCount !== 0) {
    throw new Error("補録 bundle の manifest は curations 0 / failures 0 が必要です。");
  }
  if (catalog.groups.length !== EXPECTED_COMPLETION_GROUPS) {
    throw new Error(
      `補録 bundle は${EXPECTED_COMPLETION_GROUPS} groupが必要です: ${catalog.groups.length}`,
    );
  }
  const actualModels = new Map<string, number>();
  for (const group of catalog.groups) {
    actualModels.set(group.model, (actualModels.get(group.model) ?? 0) + 1);
    if (group.candidates.length < 3) {
      throw new Error(
        `mechanical-pass candidate が3件未満の group があります: ${group.model}/${group.scenario}/${group.line}`,
      );
    }
  }
  if (
    actualModels.size !== EXPECTED_MODEL_GROUPS.size ||
    [...EXPECTED_MODEL_GROUPS].some(([model, count]) => actualModels.get(model) !== count)
  ) {
    throw new Error(
      `補録 bundle のmodel別group数が固定planと一致しません: ${JSON.stringify(Object.fromEntries(actualModels))}`,
    );
  }
}
