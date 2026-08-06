import { Link } from "react-router";

import { ConditioningBadge } from "@/components/conditioning-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  baseModelLabel,
  conditioningModeDescription,
  conditioningModeLabel,
  findVariantSibling,
} from "@/data/conditioning";
import type { Model } from "@/data/types";

/**
 * 条件バリアント列 (#201) の生成条件と、同じ base model のもう一方の列への導線。
 * 単方式モデル (conditioning なし) では何も描画しない。
 */
export function ModelConditioningCard({
  model,
  models,
  search,
}: {
  model: Model;
  models: readonly Model[];
  search: string;
}) {
  const conditioning = model.conditioning;
  if (!conditioning) {
    return null;
  }
  const sibling = findVariantSibling(models, model);

  return (
    <Card data-conditioning-mode={conditioning.mode} size="sm">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <span>生成条件</span>
          <ConditioningBadge conditioning={conditioning} />
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm text-muted-foreground">
        <p>
          この列は <span className="text-foreground">{baseModelLabel(model)}</span> を
          <span className="text-foreground">
            「{conditioningModeLabel(conditioning.mode)}」（
            {conditioningModeDescription(conditioning.mode)}）
          </span>
          の条件だけで全行そろえたものです。同じモデルでも条件が違えば別の列として公開しています。
        </p>
        {sibling?.conditioning ? (
          <p>
            <Link
              className="text-primary underline-offset-4 hover:underline"
              data-sibling-model={sibling.id}
              to={{ pathname: `/models/${sibling.id}`, search }}
            >
              もう一方の条件（{conditioningModeLabel(sibling.conditioning.mode)}）の列を見る
            </Link>
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
