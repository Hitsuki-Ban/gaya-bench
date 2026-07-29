import { isRubricComplete } from "@/curate/storage";
import type { Rubric } from "@/curate/types";

export function baselineSelectionStatus(rubric: Rubric): string {
  if (!isRubricComplete(rubric)) {
    return "選択には rubric 全4項目の入力が必要です。";
  }
  if (rubric.content_correct !== true || rubric.adoptable !== true) {
    return "選択には content_correct=true かつ adoptable=true が必要です。skip は可能です。";
  }
  return "新 baseline candidate を選択できます。";
}
