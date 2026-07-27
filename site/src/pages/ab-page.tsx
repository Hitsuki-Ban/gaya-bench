import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageIntro } from "@/components/page-intro";

export function AbPage() {
  return (
    <div className="space-y-7">
      <PageIntro
        description="同じセリフから匿名の 2 候補を提示し、先入観を避けて聴き比べる画面です。投票機能は Issue #14 で実装します。"
        eyebrow="Blind comparison"
        title="A/B ブラインド"
      />
      <Card className="border-dashed">
        <CardHeader>
          <Badge variant="outline">準備中</Badge>
          <CardTitle>匿名比較の導線を予約済み</CardTitle>
        </CardHeader>
        <CardContent className="text-sm leading-7 text-muted-foreground">
          このルートはサイト骨組みの一部です。現在はモデル名を表示せず、後続実装の責務を明示しています。
        </CardContent>
      </Card>
    </div>
  );
}
