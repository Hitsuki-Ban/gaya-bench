import { Box, ExternalLink, FileCode2, Fingerprint, Library, Mic2, Scale } from "lucide-react";

import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { benchmarkData, modelCreditById, playableModels, selectedCandidates } from "@/data";
import type { ModelCredit } from "@/data";

const REPOSITORY_URL = "https://github.com/Hitsuki-Ban/gaya-bench";
const CODE_LICENSE_URL = `${REPOSITORY_URL}/blob/main/LICENSE`;
const SCENARIO_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/";

const redistributionLabels = {
  prohibited: "再配布禁止",
  allowed_with_conditions: "条件付きで可",
} as const;

function getModelCredit(modelId: string): ModelCredit {
  const credit = modelCreditById.get(modelId);
  if (!credit) {
    throw new Error(`model credit がありません: ${modelId}`);
  }
  return credit;
}

function EvidenceLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      href={href}
      rel="noreferrer"
      target="_blank"
    >
      {children}
      <ExternalLink aria-hidden="true" className="size-3.5 shrink-0" />
    </a>
  );
}

export function CreditsPage() {
  const modelCount = benchmarkData.manifest.models.length;
  const voiceCount = benchmarkData.credits.reference_voices.length;

  return (
    <div className="space-y-7">
      <PageIntro
        aside={
          <div className="flex items-center gap-2 border-l-2 border-primary pl-3 font-mono text-[11px] tracking-[0.12em] text-muted-foreground uppercase">
            <Fingerprint aria-hidden="true" className="size-4 text-primary" />
            Release dossier
          </div>
        }
        description="公開中の固定リリースについて、モデル、参照音声、シナリオ、コードの出所と利用条件を追跡できる記録です。"
        eyebrow="Provenance / Release record"
        title="クレジットとライセンス"
      />

      <div className="grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_17rem] lg:items-start">
        <div className="min-w-0 space-y-8">
          <section aria-labelledby="models-heading" id="models">
            <div className="mb-3 flex items-end justify-between gap-4">
              <div>
                <p className="font-mono text-[10px] tracking-[0.18em] text-primary uppercase">
                  01 / Model inventory
                </p>
                <h2 className="mt-1 text-xl font-semibold" id="models-heading">
                  モデルと生成物の条件
                </h2>
              </div>
              <span className="font-mono text-xs text-muted-foreground">{modelCount} models</span>
            </div>
            <p className="mb-4 max-w-3xl text-sm leading-6 text-muted-foreground">
              名称・バージョン・条件は固定 manifest の model エントリ、リンクは同じ manifest
              内の生成時 provenance
              から自動表示しています。生成音声には各モデルの条件に加え、使用した参照音声の条件も適用されます。
            </p>

            <Card className="py-0">
              <CardContent
                aria-label="モデルライセンス表。左右キーで横スクロールできます"
                className="overflow-x-auto px-0 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                tabIndex={0}
              >
                <table className="w-full min-w-3xl border-collapse text-left">
                  <thead className="border-b bg-muted/45 font-mono text-[10px] tracking-[0.12em] text-muted-foreground uppercase">
                    <tr>
                      <th className="px-4 py-3 font-medium" scope="col">
                        Model / version
                      </th>
                      <th className="px-4 py-3 font-medium" scope="col">
                        License & output conditions
                      </th>
                      <th className="px-4 py-3 font-medium" scope="col">
                        Pinned sources
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {benchmarkData.manifest.models.map((model) => {
                      const credit = getModelCredit(model.id);
                      return (
                        <tr
                          className="align-top transition-colors hover:bg-muted/25 motion-reduce:transition-none"
                          data-model-id={model.id}
                          key={model.id}
                        >
                          <td className="w-1/4 px-4 py-4">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="font-medium text-foreground">{model.name}</span>
                              {model.id === "dummy" ? (
                                <Badge variant="outline">テスト専用</Badge>
                              ) : null}
                            </div>
                            <p className="mt-2 break-all font-mono text-[10px] leading-5 text-muted-foreground">
                              {model.id}
                              <br />
                              {model.version}
                            </p>
                          </td>
                          <td className="w-1/2 px-4 py-4 text-sm leading-6 text-muted-foreground">
                            {model.license_note}
                          </td>
                          <td className="w-1/4 px-4 py-4">
                            {credit.sources.length === 0 ? (
                              <span className="text-xs text-muted-foreground">外部モデルなし</span>
                            ) : (
                              <ul className="space-y-3">
                                {credit.sources.map((source) => (
                                  <li key={`${source.repository}/${source.revision}`}>
                                    <EvidenceLink href={source.url}>{source.label}</EvidenceLink>
                                    <p className="mt-1 break-all font-mono text-[10px] leading-4 text-muted-foreground">
                                      {source.repository}@{source.revision}
                                    </p>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          </section>

          <section aria-labelledby="voices-heading" id="voices">
            <div className="mb-3 flex items-end justify-between gap-4">
              <div>
                <p className="font-mono text-[10px] tracking-[0.18em] text-primary uppercase">
                  02 / Reference voices
                </p>
                <h2 className="mt-1 text-xl font-semibold" id="voices-heading">
                  参照音声のクレジット
                </h2>
              </div>
              <span className="font-mono text-xs text-muted-foreground">{voiceCount} voices</span>
            </div>
            <p className="mb-4 max-w-3xl text-sm leading-6 text-muted-foreground">
              <code className="font-mono text-xs text-foreground">assets/voices/metadata.yaml</code>
              の全エントリを自動表示しています。原音声そのものは本サイトから再配布しません。
            </p>

            <div className="grid gap-4 xl:grid-cols-2">
              {benchmarkData.credits.reference_voices.map((voice) => (
                <Card data-reference-voice-id={voice.id} key={voice.id}>
                  <CardHeader className="border-b">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <CardTitle>{voice.source.title}</CardTitle>
                        <p className="mt-1 text-xs text-muted-foreground">
                          話者: {voice.source.speaker}
                        </p>
                      </div>
                      <Badge variant="secondary">{voice.rights.license}</Badge>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="border-l-2 border-primary/70 pl-3">
                      <p className="font-mono text-[10px] tracking-[0.12em] text-muted-foreground uppercase">
                        Required credit
                      </p>
                      <p className="mt-1 whitespace-pre-line text-sm leading-6">
                        {voice.credit_text}
                      </p>
                    </div>

                    <dl className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs leading-5">
                      <dt className="text-muted-foreground">確認日</dt>
                      <dd>{voice.rights.verified_on}</dd>
                      <dt className="text-muted-foreground">再配布</dt>
                      <dd>
                        <Badge
                          variant={
                            voice.rights.redistribution.status === "prohibited"
                              ? "destructive"
                              : "outline"
                          }
                        >
                          {redistributionLabels[voice.rights.redistribution.status]}
                        </Badge>
                      </dd>
                      <dt className="text-muted-foreground">条件</dt>
                      <dd className="text-muted-foreground">{voice.rights.redistribution.notes}</dd>
                      <dt className="text-muted-foreground">台本</dt>
                      <dd>{voice.transcript_rights.license}</dd>
                      <dt className="text-muted-foreground">台本クレジット</dt>
                      <dd className="whitespace-pre-line text-muted-foreground">
                        {voice.transcript_rights.credit_text}
                      </dd>
                    </dl>

                    <div className="flex flex-wrap gap-x-4 gap-y-2 border-t pt-3 text-xs">
                      <EvidenceLink href={voice.source.download_page}>公式配布元</EvidenceLink>
                      <EvidenceLink href={voice.rights.voice_synthesis_evidence_url}>
                        音声合成条件
                      </EvidenceLink>
                      <EvidenceLink href={voice.rights.commercial_use_evidence_url}>
                        商用利用条件
                      </EvidenceLink>
                      <EvidenceLink href={voice.rights.redistribution.evidence_url}>
                        再配布条件
                      </EvidenceLink>
                      <EvidenceLink href={voice.transcript_rights.evidence_url}>
                        台本の権利
                      </EvidenceLink>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </section>

          <section aria-labelledby="project-heading" id="project">
            <div className="mb-4">
              <p className="font-mono text-[10px] tracking-[0.18em] text-primary uppercase">
                03 / Project materials
              </p>
              <h2 className="mt-1 text-xl font-semibold" id="project-heading">
                プロジェクト成果物
              </h2>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <Card>
                <CardHeader>
                  <FileCode2 aria-hidden="true" className="size-5 text-primary" />
                  <CardTitle>ソースコード — MIT</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm leading-6 text-muted-foreground">
                  <p>
                    本リポジトリのコードは MIT License
                    です。著作権表示と許諾表示を保持してください。
                  </p>
                  <EvidenceLink href={CODE_LICENSE_URL}>LICENSE を確認</EvidenceLink>
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <Library aria-hidden="true" className="size-5 text-primary" />
                  <CardTitle>シナリオ — CC BY 4.0</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm leading-6 text-muted-foreground">
                  <p>
                    <code className="font-mono text-xs text-foreground">scenarios/</code>{" "}
                    の台詞・設定は CC BY 4.0
                    です。利用時は適切なクレジット、ライセンスへのリンク、変更の表示が必要です。
                  </p>
                  <EvidenceLink href={SCENARIO_LICENSE_URL}>CC BY 4.0 を確認</EvidenceLink>
                </CardContent>
              </Card>
            </div>
          </section>

          <section aria-labelledby="disclaimer-heading" id="disclaimer">
            <Card className="border-l-2 border-l-primary">
              <CardHeader>
                <Scale aria-hidden="true" className="size-5 text-primary" />
                <h2 className="text-base leading-snug font-medium" id="disclaimer-heading">
                  目的と免責
                </h2>
              </CardHeader>
              <CardContent className="space-y-3 text-sm leading-6 text-muted-foreground">
                <p>
                  Gaya Bench
                  は、RPGモブNPC向けTTSの研究・比較を目的とするベンチマークです。掲載結果は固定条件下の記録であり、モデルや生成音声の品質、正確性、特定用途への適合性を保証しません。
                </p>
                <p>
                  公開音声はAI生成です。利用者は、各モデル、参照音声、台本その他の権利条件を原典で確認し、声の権利、人格権、商標その他の適用法令を含め、自らの用途に必要な許諾を判断してください。
                </p>
              </CardContent>
            </Card>
          </section>
        </div>

        <aside className="space-y-4 lg:sticky lg:top-20">
          <Card>
            <CardHeader className="border-b">
              <Box aria-hidden="true" className="size-5 text-primary" />
              <CardTitle>Release identity</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="space-y-3 text-xs">
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-muted-foreground">manifest models</dt>
                  <dd className="font-mono">{modelCount}</dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-muted-foreground">playable models</dt>
                  <dd className="font-mono">{playableModels.length}</dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-muted-foreground">selected clips</dt>
                  <dd className="font-mono">{selectedCandidates.length}</dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-muted-foreground">reference voices</dt>
                  <dd className="font-mono">{voiceCount}</dd>
                </div>
                <div className="border-t pt-3">
                  <dt className="text-muted-foreground">release marker</dt>
                  <dd className="mt-1 break-all font-mono text-[10px]">
                    {benchmarkData.manifest.generated_at}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">candidate set SHA-256</dt>
                  <dd className="mt-1 break-all font-mono text-[10px] leading-4">
                    {benchmarkData.manifest.candidate_set_sha256}
                  </dd>
                </div>
              </dl>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="border-b">
              <Mic2 aria-hidden="true" className="size-5 text-primary" />
              <CardTitle>Contents</CardTitle>
            </CardHeader>
            <CardContent>
              <nav aria-label="ページ内目次">
                <ol className="space-y-2 font-mono text-xs">
                  {[
                    ["01", "モデル", "#models"],
                    ["02", "参照音声", "#voices"],
                    ["03", "成果物", "#project"],
                    ["04", "目的と免責", "#disclaimer"],
                  ].map(([number, label, href]) => (
                    <li key={href}>
                      <a
                        className="flex items-center gap-3 rounded-sm py-1 text-muted-foreground hover:text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                        href={href}
                      >
                        <span className="text-primary">{number}</span>
                        {label}
                      </a>
                    </li>
                  ))}
                </ol>
              </nav>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
