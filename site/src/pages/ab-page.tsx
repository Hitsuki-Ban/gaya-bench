import { AbComparison } from "@/ab/ab-comparison";
import { PageIntro } from "@/components/page-intro";

export function AbPage() {
  return (
    <div className="space-y-7">
      <PageIntro
        description="同じセリフを 2 つの匿名候補で聴き比べます。モデル名を見ずに選んだ結果から、自分の耳ランキングを集計します。"
        eyebrow="Blind comparison"
        title="A/B ブラインド"
      />
      <AbComparison />
    </div>
  );
}
