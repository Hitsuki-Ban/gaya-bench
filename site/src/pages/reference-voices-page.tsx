import { useEffect } from "react";
import { ExternalLink, Mic2, Scale, ShieldCheck } from "lucide-react";
import { Link, useLocation } from "react-router";

import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { benchmarkData } from "@/data";
import { AGE_LABELS, GENDER_LABELS } from "@/ui-labels";

const redistributionLabels = {
  prohibited: "再配布禁止",
  allowed_with_conditions: "条件付きで可",
} as const;

export function ReferenceVoicesPage() {
  const voices = benchmarkData.credits.reference_voices;
  const { hash } = useLocation();

  useEffect(() => {
    if (hash.length <= 1) return;
    document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start" });
  }, [hash]);

  return (
    <div className="min-w-0 space-y-6">
      <PageIntro
        aside={
          <Link
            className="inline-flex items-center gap-2 text-sm text-primary underline-offset-4 hover:underline"
            to="/credits#voices"
          >
            <Scale aria-hidden="true" className="size-4" />
            権利・クレジット詳細
          </Link>
        }
        description="音声生成時の参照に使った収録音声の話者と声質を紹介します。原音声ファイルは本サイトでは配布・再生しません。"
        eyebrow="参照音声"
        title="収録音声の話者紹介"
      />

      <p
        className="rounded-md border border-primary/25 bg-primary/6 px-3 py-2 text-xs leading-5 text-muted-foreground"
        role="note"
      >
        比較画面の「参照:
        収録音声」から、各クリップで実際に使った話者を確認できます。収録元の音声を確認する場合は、各提供元のページをご覧ください。
      </p>

      <section aria-labelledby="reference-voices-heading">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3 border-b pb-3">
          <div>
            <p className="font-mono text-[10px] tracking-[0.18em] text-primary">参照素材</p>
            <h2 className="mt-1 text-xl font-semibold" id="reference-voices-heading">
              公開リリースで使用する収録音声
            </h2>
          </div>
          <span className="font-mono text-xs text-muted-foreground">{voices.length} 素材</span>
        </div>

        <div className="grid min-w-0 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {voices.map((voice) => (
            <Card className="min-w-0 scroll-mt-20" id={voice.id} key={voice.id}>
              <CardHeader className="border-b">
                <div className="flex min-w-0 items-start justify-between gap-3">
                  <div className="min-w-0">
                    <CardTitle className="break-words text-lg">{voice.source.speaker}</CardTitle>
                    <p className="mt-1 break-words text-xs leading-5 text-muted-foreground">
                      {voice.source.title}
                    </p>
                  </div>
                  <Mic2 aria-hidden="true" className="size-5 shrink-0 text-primary" />
                </div>
              </CardHeader>
              <CardContent className="min-w-0 space-y-4">
                <div className="flex flex-wrap gap-2">
                  <Badge variant="secondary">{GENDER_LABELS[voice.voice.gender]}</Badge>
                  <Badge variant="outline">{AGE_LABELS[voice.voice.age]}</Badge>
                  <Badge variant="outline">{voice.duration_sec.toFixed(1)} 秒</Badge>
                </div>

                <div>
                  <p className="font-mono text-[10px] tracking-[0.12em] text-muted-foreground uppercase">
                    声の特徴
                  </p>
                  <p className="mt-1 text-sm leading-6">{voice.voice.notes}</p>
                </div>

                <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs leading-5 text-muted-foreground">
                  <div className="mb-1 flex items-center gap-2 font-medium text-foreground">
                    <ShieldCheck aria-hidden="true" className="size-4 text-primary" />
                    利用条件
                  </div>
                  <dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-2 gap-y-1">
                    <dt>ライセンス</dt>
                    <dd className="text-foreground">{voice.rights.license}</dd>
                    <dt>再配布</dt>
                    <dd className="text-foreground">
                      {redistributionLabels[voice.rights.redistribution.status]}
                    </dd>
                  </dl>
                  <p className="mt-2">{voice.rights.redistribution.notes}</p>
                </div>

                <a
                  className="inline-flex items-center gap-1.5 text-sm text-primary underline-offset-4 hover:underline"
                  href={voice.source.download_page}
                  rel="noreferrer"
                  target="_blank"
                >
                  {voice.source.speaker}の提供元を見る
                  <ExternalLink aria-hidden="true" className="size-3.5" />
                </a>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}
