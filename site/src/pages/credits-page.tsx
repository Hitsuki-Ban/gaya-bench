import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageIntro } from "@/components/page-intro";

export function CreditsPage() {
  return (
    <div className="space-y-5">
      <PageIntro
        description="モデル、参照音声、生成物、OSS の権利情報を一か所で確認できる公開ページです。"
        eyebrow="Provenance"
        title="クレジットとライセンス"
      />
      <Card>
        <CardHeader>
          <CardTitle>公開前に統合</CardTitle>
        </CardHeader>
        <CardContent className="text-sm leading-7 text-muted-foreground">
          ライセンス一覧と参照音声のクレジットは Issue #17 で確定します。
        </CardContent>
      </Card>
    </div>
  );
}
