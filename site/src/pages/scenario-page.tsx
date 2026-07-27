import { Link, useParams } from "react-router";

import { ClipButton } from "@/components/clip-button";
import { PageIntro } from "@/components/page-intro";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getClipsForScenario, scenarioById } from "@/data";
import { NotFoundPage } from "@/pages/not-found-page";

export function ScenarioPage() {
  const { id } = useParams();
  const scenario = id ? scenarioById.get(id) : undefined;
  if (!scenario) {
    return <NotFoundPage />;
  }

  const clips = getClipsForScenario(scenario.id);
  return (
    <div className="space-y-7">
      <PageIntro
        aside={
          <Link className="text-sm text-primary underline-offset-4 hover:underline" to="/">
            比較へ戻る
          </Link>
        }
        description={scenario.scene.setting}
        eyebrow={`Scenario / ${scenario.id}`}
        title={scenario.title}
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {scenario.lines.map((line) => {
          const character = scenario.characters.find((item) => item.id === line.character)!;
          const lineClips = clips.filter((clip) => clip.line === line.id);
          return (
            <Card key={line.id} size="sm">
              <CardHeader>
                <div className="flex flex-wrap gap-2">
                  <Badge variant="secondary">{character.name}</Badge>
                  <Badge variant="outline">{line.emotion}</Badge>
                  <Badge variant="outline">{line.difficulty}</Badge>
                </div>
                <CardTitle className="mt-2 leading-7">{line.text}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {lineClips.length > 0 ? (
                  lineClips.map((clip) => <ClipButton clip={clip} key={clip.model} />)
                ) : (
                  <p className="text-xs text-muted-foreground">未生成</p>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
