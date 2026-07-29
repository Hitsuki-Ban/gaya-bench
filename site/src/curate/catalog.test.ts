import { describe, expect, it } from "vite-plus/test";

import type { DirectoryFile, ObjectUrlFactory } from "@/lib/local-directory";
import { sha256Hex, sha256Text } from "@/lib/sha256";
import { loadCurateCatalog } from "./catalog";

const INPUT_SHA = "a".repeat(64);

describe("loadCurateCatalog", () => {
  it("三方一致した原始 candidate-set digest、sidecar lines、local audio、blind order を束縛する", async () => {
    const fixture = await makeFixture();
    const firstUrls = new FakeObjectUrls();
    const secondUrls = new FakeObjectUrls();
    const first = await loadCurateCatalog(fixture.files, firstUrls);
    const second = await loadCurateCatalog([...fixture.files].reverse(), secondUrls);

    expect(first.candidateSetSha256).toBe(fixture.candidateSetSha256);
    expect(first.groups[0]).toMatchObject({
      scenarioTitle: "保存時のシーン",
      lineText: "保存時の台詞",
      delivery: "保存時の演技指示",
    });
    expect(first.groups[0]!.candidates.map(({ label, takeId }) => [label, takeId])).toEqual(
      second.groups[0]!.candidates.map(({ label, takeId }) => [label, takeId]),
    );
    expect(Object.keys(first.groups[0]!.candidates[0]!).sort()).toEqual([
      "audio",
      "gateContent",
      "label",
      "takeId",
    ]);
    expect(JSON.stringify(first.groups[0]!.candidates)).not.toContain("seed");
    expect(JSON.stringify(first.groups[0]!.candidates)).not.toContain("score");
    expect(JSON.stringify(first.groups[0]!.candidates)).not.toContain("take_index");
    expect(firstUrls.created).toHaveLength(2);

    first.dispose();
    first.dispose();
    expect(firstUrls.revoked).toEqual(firstUrls.created);
    second.dispose();
  });

  it("candidate-set の key 並べ替え・末尾改行を marker 未更新なら策展前に拒否する", async () => {
    const fixture = await makeFixture();
    const parsed = JSON.parse(fixture.candidateSetSource) as Record<string, unknown>;
    const reordered = JSON.stringify({
      failures: parsed.failures,
      candidates: parsed.candidates,
      models: parsed.models,
      lines: parsed.lines,
      scenario_sha256: parsed.scenario_sha256,
      format_version: parsed.format_version,
    });

    for (const source of [reordered, `${fixture.candidateSetSource}\n`]) {
      const urls = new FakeObjectUrls();
      await expect(
        loadCurateCatalog(replaceFile(fixture.files, "candidate-set.json", source), urls),
      ).rejects.toThrow("candidate-set.sha256 と一致しません");
      expect(urls.created).toEqual([]);
    }
  });

  it("marker と manifest の candidate-set digest 不一致を拒否する", async () => {
    const fixture = await makeFixture();
    await expect(
      loadCurateCatalog(
        replaceFile(fixture.files, "candidate-set.sha256", "e".repeat(64)),
        new FakeObjectUrls(),
      ),
    ).rejects.toThrow("candidate-set.sha256 と一致しません");

    const manifest = JSON.parse(fixture.manifestSource) as { candidate_set_sha256: string };
    manifest.candidate_set_sha256 = "e".repeat(64);
    await expect(
      loadCurateCatalog(
        replaceFile(fixture.files, "manifest-v4.json", JSON.stringify(manifest)),
        new FakeObjectUrls(),
      ),
    ).rejects.toThrow("manifest.candidate_set_sha256 と一致しません");

    await expect(
      loadCurateCatalog(
        replaceFile(fixture.files, "candidate-set.sha256", `${fixture.candidateSetSha256}\n`),
        new FakeObjectUrls(),
      ),
    ).rejects.toThrow("改行なし");
  });

  it("lines の欠落・余分・未ソート・同一 scenario title 不一致を拒否する", async () => {
    const fixture = await makeFixture();
    const original = JSON.parse(fixture.candidateSetSource) as CandidateSetFixture;
    const cases: Array<[CandidateSetFixture, string]> = [
      [{ ...original, lines: original.lines.slice(0, 1) }, "参照行がありません"],
      [
        {
          ...original,
          lines: [
            ...original.lines,
            {
              scenario: "z-scene",
              line: "line",
              scenario_title: "余分",
              text: "余分",
              delivery: "余分",
            },
          ],
        },
        "参照されない余分な行",
      ],
      [{ ...original, lines: [...original.lines].reverse() }, "昇順"],
      [
        {
          ...original,
          lines: original.lines.map((line, index) =>
            index === 1 ? { ...line, scenario_title: "異なるタイトル" } : line,
          ),
        },
        "scenario_title が一致しません",
      ],
    ];

    for (const [candidateSet, message] of cases) {
      const synchronized = await replaceCandidateSetAndDigests(fixture, candidateSet);
      await expect(loadCurateCatalog(synchronized, new FakeObjectUrls())).rejects.toThrow(message);
    }
  });

  it("manifest / candidate-set の v3 と candidate subset mismatch を拒否する", async () => {
    const fixture = await makeFixture();
    const v3Manifest = JSON.parse(fixture.manifestSource) as { format_version: number };
    v3Manifest.format_version = 3;
    await expect(
      loadCurateCatalog(
        replaceFile(fixture.files, "manifest-v4.json", JSON.stringify(v3Manifest)),
        new FakeObjectUrls(),
      ),
    ).rejects.toThrow("manifest.format_version は 4");

    const candidateSet = JSON.parse(fixture.candidateSetSource) as CandidateSetFixture;
    const v3Files = await replaceCandidateSetAndDigests(fixture, {
      ...candidateSet,
      format_version: 3,
    });
    await expect(loadCurateCatalog(v3Files, new FakeObjectUrls())).rejects.toThrow(
      "candidate-set.format_version は 4",
    );

    candidateSet.models[0]!.name = "Other";
    const subsetMismatch = await replaceCandidateSetAndDigests(fixture, candidateSet);
    await expect(loadCurateCatalog(subsetMismatch, new FakeObjectUrls())).rejects.toThrow(
      "candidate subset と一致しません",
    );
  });

  it("audio SHA mismatch を object URL 作成前に拒否する", async () => {
    const fixture = await makeFixture();
    const urls = new FakeObjectUrls();
    const mismatched = fixture.files.map((file) =>
      file.webkitRelativePath.endsWith("take-0001.opus")
        ? new MemoryFile(file.webkitRelativePath, "changed")
        : file,
    );

    await expect(loadCurateCatalog(mismatched, urls)).rejects.toThrow(
      "音声 SHA-256 が candidate と一致しません",
    );
    expect(urls.created).toEqual([]);
  });

  it("必須 marker、重複 path、異なる root を拒否する", async () => {
    const fixture = await makeFixture();
    await expect(
      loadCurateCatalog(
        fixture.files.filter((file) => file.name !== "candidate-set.sha256"),
        new FakeObjectUrls(),
      ),
    ).rejects.toThrow("candidate-set.sha256");

    await expect(
      loadCurateCatalog([...fixture.files, fixture.files[0]!], new FakeObjectUrls()),
    ).rejects.toThrow("path が重複");

    const wrongRoot = [...fixture.files, new MemoryFile("another/extra.txt", "extra")];
    await expect(loadCurateCatalog(wrongRoot, new FakeObjectUrls())).rejects.toThrow(
      "複数の run root",
    );
  });
});

interface CandidateSetFixture {
  readonly format_version: number;
  readonly scenario_sha256: string;
  readonly lines: Array<{
    scenario: string;
    line: string;
    scenario_title: string;
    text: string;
    delivery: string;
  }>;
  readonly models: Array<{
    id: string;
    name: string;
    version: string;
    license_note: string;
    capabilities: Record<string, boolean>;
  }>;
  readonly candidates: unknown[];
  readonly failures: unknown[];
}

class MemoryFile implements DirectoryFile {
  readonly name: string;
  readonly webkitRelativePath: string;
  private readonly contents: string;

  constructor(webkitRelativePath: string, contents: string) {
    this.webkitRelativePath = webkitRelativePath;
    this.contents = contents;
    this.name = webkitRelativePath.split("/").at(-1)!;
  }

  async arrayBuffer(): Promise<ArrayBuffer> {
    return new TextEncoder().encode(this.contents).buffer;
  }
}

class FakeObjectUrls implements ObjectUrlFactory {
  readonly created: string[] = [];
  readonly revoked: string[] = [];

  create(): string {
    const url = `blob:test-${this.created.length}`;
    this.created.push(url);
    return url;
  }

  revoke(url: string): void {
    this.revoked.push(url);
  }
}

async function makeFixture(): Promise<{
  files: readonly MemoryFile[];
  candidateSetSource: string;
  candidateSetSha256: string;
  manifestSource: string;
}> {
  const audioContents = ["audio-one", "audio-two"];
  const candidates = await Promise.all(
    audioContents.map(async (contents, index) => {
      const audioSha = await sha256Hex(new TextEncoder().encode(contents));
      const takeId = await sha256Text(
        `{"final_opus_sha256":"${audioSha}","generation_input_sha256":"${INPUT_SHA}"}`,
      );
      const takeIndex = index + 1;
      return {
        model: "dummy",
        scenario: "scene",
        line: "line",
        variant: "dry",
        take_index: takeIndex,
        take_id: takeId,
        path: `audio/takes/dummy/scene/line/dry/take-${String(takeIndex).padStart(4, "0")}-${audioSha}.opus`,
        duration_sec: 1.25,
        sha256: audioSha,
        generation_input_sha256: INPUT_SHA,
        gen_params: {
          seed: 100 + takeIndex,
          recipe_version: "seed-only-v1",
          sampling: {},
          requested: { temperature: 1.0 },
          realized: { temperature: 1.0 },
        },
        rtf: 0.5,
        loudness: {
          source: "encoded_opus",
          i_lufs: -18,
          tp_dbtp: -1,
          shortfall: false,
        },
        gate: {
          mechanical: "pass",
          content: index === 0 ? "review_required" : "pass",
          policy_version: "take-gate-v1",
        },
      };
    }),
  );
  const models = [
    {
      id: "dummy",
      name: "Dummy",
      version: "1",
      license_note: "",
      capabilities: {
        emotion: false,
        voice_prompt: false,
        clone: false,
        nonverbal: false,
        reading: false,
      },
    },
  ];
  const failures = [
    {
      model: "dummy",
      scenario: "scene",
      line: "other",
      variant: "dry",
      reason: "no_eligible_take",
    },
  ];
  const candidateSet: CandidateSetFixture = {
    format_version: 4,
    scenario_sha256: "c".repeat(64),
    lines: [
      {
        scenario: "scene",
        line: "line",
        scenario_title: "保存時のシーン",
        text: "保存時の台詞",
        delivery: "保存時の演技指示",
      },
      {
        scenario: "scene",
        line: "other",
        scenario_title: "保存時のシーン",
        text: "失敗行",
        delivery: "失敗行の演技指示",
      },
    ],
    models,
    candidates,
    failures,
  };
  const candidateSetSource = JSON.stringify(candidateSet);
  const candidateSetSha256 = await sha256Hex(new TextEncoder().encode(candidateSetSource));
  const manifest = {
    format_version: 4,
    generated_at: "2026-07-29T00:00:00Z",
    candidate_set_sha256: candidateSetSha256,
    models,
    candidates,
    curations: [],
    failures,
  };
  const manifestSource = JSON.stringify(manifest);
  return {
    candidateSetSource,
    candidateSetSha256,
    manifestSource,
    files: [
      new MemoryFile("run/manifest-v4.json", manifestSource),
      new MemoryFile("run/candidate-set.json", candidateSetSource),
      new MemoryFile("run/candidate-set.sha256", candidateSetSha256),
      ...audioContents.map(
        (contents, index) =>
          new MemoryFile(
            `run/audio/dummy/scene/line/dry/take-${String(index + 1).padStart(4, "0")}.opus`,
            contents,
          ),
      ),
    ],
  };
}

function replaceFile(
  files: readonly MemoryFile[],
  name: string,
  contents: string,
): readonly MemoryFile[] {
  return files.map((file) =>
    file.name === name ? new MemoryFile(file.webkitRelativePath, contents) : file,
  );
}

async function replaceCandidateSetAndDigests(
  fixture: Awaited<ReturnType<typeof makeFixture>>,
  candidateSet: CandidateSetFixture,
): Promise<readonly MemoryFile[]> {
  const source = JSON.stringify(candidateSet);
  const digest = await sha256Hex(new TextEncoder().encode(source));
  const manifest = JSON.parse(fixture.manifestSource) as { candidate_set_sha256: string };
  manifest.candidate_set_sha256 = digest;
  return replaceFile(
    replaceFile(
      replaceFile(fixture.files, "candidate-set.json", source),
      "candidate-set.sha256",
      digest,
    ),
    "manifest-v4.json",
    JSON.stringify(manifest),
  );
}
