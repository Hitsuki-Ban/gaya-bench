import { describe, expect, it } from "vite-plus/test";

import type { DirectoryFile, ObjectUrlFactory } from "@/lib/local-directory";
import { sha256Hex } from "@/lib/sha256";
import { canonicalJson } from "@/lib/canonical-json";
import { loadPilotCatalog } from "@/pilot/catalog";

const MODELS = ["qwen3-tts-12hz-1.7b", "irodori-tts-600m-v3-voicedesign", "voxcpm2"] as const;
const SCENARIOS = ["battlefield-camp", "dungeon-entrance"] as const;
const FEATURE_NAMES = [
  "duration_sec",
  "mora_per_second",
  "pause_sec",
  "voiced_ratio",
  "f0_semitone_std",
  "energy_median_dbfs",
] as const;

describe("loadPilotCatalog", () => {
  it("raw pilot SHA と全 Opus を検証し、candidate_ids 順を A/B/C に固定する", async () => {
    const fixture = await createFixture();
    const created: string[] = [];
    const revoked: string[] = [];
    const objectUrls: ObjectUrlFactory = {
      create(file) {
        created.push(file.webkitRelativePath);
        return `blob:${file.name}`;
      },
      revoke(url) {
        revoked.push(url);
      },
    };

    const catalog = await loadPilotCatalog(fixture.files, objectUrls);
    const firstGroupLine = fixture.pilotSet.lines.find(
      (line) =>
        line.scenario === fixture.pilotSet.groups[0]!.scenario &&
        line.line === fixture.pilotSet.groups[0]!.line,
    )!;

    expect(catalog.pilotSetSha256).toBe(await sha256Hex(fixture.pilotSetBytes));
    expect(catalog.groups).toHaveLength(72);
    expect(catalog.groups[0]!.presentation).toMatchObject({
      lineText: firstGroupLine.text,
      reading: firstGroupLine.reading,
      delivery: firstGroupLine.delivery,
    });
    expect(catalog.groups[0]!.presentation.candidates.map((candidate) => candidate.label)).toEqual([
      "A",
      "B",
      "C",
    ]);
    expect(
      catalog.groups[0]!.presentation.candidates.map((candidate) => candidate.candidateId),
    ).toEqual(fixture.pilotSet.groups[0]!.candidate_ids);
    expect(JSON.stringify(catalog.groups[0]!.presentation)).not.toContain(MODELS[0]);
    expect(created).toHaveLength(216);

    catalog.dispose();
    catalog.dispose();
    expect(revoked).toHaveLength(216);
  });

  it("exact v1 keys と final gate 状態を強制する", async () => {
    const unknownKey = await createFixture((pilotSet) => {
      Object.assign(pilotSet, { legacy_protocol: true });
    });
    await expect(loadPilotCatalog(unknownKey.files)).rejects.toThrow("pilot-set の key が不正");

    const invalidEligible = await createFixture((pilotSet) => {
      pilotSet.candidates[2]!.gates.primary_reject_rule = "mechanical_audio";
    });
    await expect(loadPilotCatalog(invalidEligible.files)).rejects.toThrow(
      "eligible gate の状態が不正",
    );

    const invalidMechanicalRule = await createFixture((pilotSet) => {
      const rejected = pilotSet.candidates.find(
        (candidate) => candidate.gates.mechanical === "reject",
      )!;
      rejected.gates.primary_reject_rule = "active_speech_nonpositive";
    });
    await expect(loadPilotCatalog(invalidMechanicalRule.files)).rejects.toThrow(
      "hard_rejected gate の状態が不正",
    );

    const tamperedIdentity = await createFixture((pilotSet) => {
      const candidate = pilotSet.candidates[0]!;
      const originalId = candidate.candidate_id;
      const tamperedId = "f".repeat(64);
      candidate.candidate_id = tamperedId;
      candidate.audio.path = `audio/${tamperedId}.opus`;
      const group = pilotSet.groups.find((group) => group.candidate_ids.includes(originalId))!;
      group.candidate_ids[group.candidate_ids.indexOf(originalId)] = tamperedId;
      group.candidate_ids.sort();
    });
    await expect(loadPilotCatalog(tamperedIdentity.files)).rejects.toThrow(
      "candidate_id が take_id と一致しません",
    );
  });

  it("non-canonical raw pilot-set bytes を人評開始前に拒否する", async () => {
    const fixture = await createFixture();
    const canonicalSource = new TextDecoder().decode(fixture.pilotSetBytes);
    const parsed = JSON.parse(canonicalSource) as Record<string, unknown>;
    const variants = [
      `${canonicalSource}\n`,
      JSON.stringify(parsed, null, 2),
      JSON.stringify({ protocol: parsed.protocol, ...parsed }),
    ];

    for (const source of variants) {
      const files = [...fixture.files];
      files[0] = makeFile("pilot-set.json", new TextEncoder().encode(source));
      let createCount = 0;
      await expect(
        loadPilotCatalog(files, {
          create() {
            createCount += 1;
            return "blob:never";
          },
          revoke() {},
        }),
      ).rejects.toThrow("canonical JSON bytes");
      expect(createCount).toBe(0);
    }
  });

  it("音声 tamper と Opus 集合差分を object URL 作成前に拒否する", async () => {
    const tampered = await createFixture();
    const audioFile = tampered.files.find((file) =>
      file.webkitRelativePath.endsWith(`${tampered.pilotSet.groups[0]!.candidate_ids[0]}.opus`),
    )!;
    const originalBytes = new Uint8Array(await audioFile.arrayBuffer());
    tampered.files[tampered.files.indexOf(audioFile)] = makeFile(
      audioFile.webkitRelativePath.slice("bundle/".length),
      new Uint8Array([...originalBytes, 0xff]),
    );
    let createCount = 0;
    await expect(
      loadPilotCatalog(tampered.files, {
        create() {
          createCount += 1;
          return "blob:never";
        },
        revoke() {},
      }),
    ).rejects.toThrow("音声 SHA-256");
    expect(createCount).toBe(0);

    const extra = await createFixture();
    extra.files.push(makeFile(`audio/${"f".repeat(64)}.opus`, new Uint8Array([1])));
    await expect(loadPilotCatalog(extra.files)).rejects.toThrow("file 集合");
  });

  it("全 group/candidate の完全被覆と同組 tuple を強制する", async () => {
    const wrongTuple = await createFixture((pilotSet) => {
      pilotSet.groups[0]!.candidate_ids[0] = pilotSet.groups[1]!.candidate_ids[0]!;
      pilotSet.groups[0]!.candidate_ids.sort();
    });
    await expect(loadPilotCatalog(wrongTuple.files)).rejects.toThrow("candidate tuple");

    const duplicatedReference = await createFixture((pilotSet) => {
      pilotSet.groups[0]!.candidate_ids[0] = pilotSet.groups[0]!.candidate_ids[1]!;
    });
    await expect(loadPilotCatalog(duplicatedReference.files)).rejects.toThrow("複数 group");
  });
});

interface MutableLine {
  scenario: (typeof SCENARIOS)[number];
  line: string;
  scenario_title: string;
  text: string;
  reading: string;
  delivery: string;
}

interface MutableGroup {
  group_id: string;
  model: (typeof MODELS)[number];
  scenario: (typeof SCENARIOS)[number];
  line: string;
  variant: string;
  candidate_ids: string[];
}

interface MutableCandidate {
  candidate_id: string;
  model: (typeof MODELS)[number];
  scenario: (typeof SCENARIOS)[number];
  line: string;
  variant: string;
  take_index: number;
  take_id: string;
  status: "eligible" | "hard_rejected";
  gates: {
    mechanical: "pass" | "reject";
    content: "pass" | "review_required" | "reject" | "not_run";
    policy_version: string;
    primary_reject_rule:
      | "mechanical_audio"
      | "active_speech_nonpositive"
      | "explicit_reading_mismatch"
      | null;
    reject_reason: string | null;
  };
  features: Record<(typeof FEATURE_NAMES)[number], number | null>;
  audio: {
    path: string;
    sha256: string;
  };
}

interface MutablePilotSet {
  format_version: number;
  protocol: string;
  generated_at: string;
  design: {
    models: string[];
    scenarios: string[];
    line_count: number;
    takes_per_group: number;
    seed_base: number;
    feature_specs: Array<{ name: string; source: string }>;
  };
  lines: MutableLine[];
  groups: MutableGroup[];
  candidates: MutableCandidate[];
}

async function createFixture(mutate?: (pilotSet: MutablePilotSet) => void): Promise<{
  pilotSet: MutablePilotSet;
  pilotSetBytes: ArrayBuffer;
  files: DirectoryFile[];
}> {
  const lines: MutableLine[] = Array.from({ length: 24 }, (_, index) => {
    const position = index + 1;
    const scenario = SCENARIOS[Math.floor(index / 12)]!;
    return {
      scenario,
      line: `line-${String(position).padStart(2, "0")}`,
      scenario_title: scenario === SCENARIOS[0] ? "戦場の野営地" : "ダンジョン入口",
      text: `台詞 ${String(position).padStart(2, "0")}`,
      reading: `せりふ ${String(position).padStart(2, "0")}`,
      delivery: `自然に ${String(position).padStart(2, "0")}`,
    };
  });
  const candidates: MutableCandidate[] = [];
  const groups: MutableGroup[] = [];
  const audioBytes = new Map<string, Uint8Array>();
  let candidateNumber = 0;
  for (const model of MODELS) {
    for (const line of lines) {
      const groupCandidateIds: string[] = [];
      for (const takeIndex of [1, 2, 3]) {
        candidateNumber += 1;
        const takeId = hex(10_000 + candidateNumber);
        const candidateId = await makeCandidateId(takeId);
        const bytes = new TextEncoder().encode(`pilot-audio-${candidateId}`);
        audioBytes.set(candidateId, bytes);
        groupCandidateIds.push(candidateId);
        const candidate: MutableCandidate = {
          candidate_id: candidateId,
          model,
          scenario: line.scenario,
          line: line.line,
          variant: "dry",
          take_index: takeIndex,
          take_id: takeId,
          status: "eligible",
          gates: {
            mechanical: "pass",
            content: "pass",
            policy_version: "n3-pilot-gate-v1",
            primary_reject_rule: null,
            reject_reason: null,
          },
          features: {
            duration_sec: null,
            mora_per_second: null,
            pause_sec: null,
            voiced_ratio: null,
            f0_semitone_std: null,
            energy_median_dbfs: null,
          },
          audio: {
            path: `audio/${candidateId}.opus`,
            sha256: await sha256Hex(bytes),
          },
        };
        candidates.push(candidate);
      }
      groups.push({
        group_id: await makeGroupId(model, line.scenario, line.line, "dry"),
        model,
        scenario: line.scenario,
        line: line.line,
        variant: "dry",
        candidate_ids: groupCandidateIds.sort(),
      });
    }
  }
  candidates[0]!.status = "hard_rejected";
  candidates[0]!.gates = {
    mechanical: "pass",
    content: "reject",
    policy_version: "n3-pilot-gate-v1",
    primary_reject_rule: "explicit_reading_mismatch",
    reject_reason: null,
  };
  candidates[1]!.status = "hard_rejected";
  candidates[1]!.gates = {
    mechanical: "reject",
    content: "not_run",
    policy_version: "n3-pilot-gate-v1",
    primary_reject_rule: "mechanical_audio",
    reject_reason: "decode failed",
  };
  candidates.sort(
    (left, right) =>
      compareText(left.model, right.model) ||
      compareText(left.scenario, right.scenario) ||
      compareText(left.line, right.line) ||
      compareText(left.variant, right.variant) ||
      left.take_index - right.take_index,
  );
  groups.sort((left, right) => compareText(left.group_id, right.group_id));
  const pilotSet: MutablePilotSet = {
    format_version: 1,
    protocol: "n3-pilot-v1",
    generated_at: "2026-07-29T01:02:03Z",
    design: {
      models: [...MODELS],
      scenarios: [...SCENARIOS],
      line_count: 24,
      takes_per_group: 3,
      seed_base: 103,
      feature_specs: [
        { name: "duration_sec", source: "content.prosody.duration_sec" },
        { name: "mora_per_second", source: "content.prosody.active_mora_per_sec" },
        { name: "pause_sec", source: "content.prosody.pause.internal_total_sec" },
        { name: "voiced_ratio", source: "content.prosody.f0.voiced_ratio" },
        { name: "f0_semitone_std", source: "content.prosody.f0.semitone_std" },
        { name: "energy_median_dbfs", source: "content.prosody.energy.median_dbfs" },
      ],
    },
    lines,
    groups,
    candidates,
  };
  mutate?.(pilotSet);
  const pilotSetSource = canonicalJson(pilotSet, "pilot test fixture").replace(
    '"duration_sec":null',
    '"duration_sec":1.0',
  );
  const pilotSetBytes = new TextEncoder().encode(pilotSetSource).buffer;
  const files: DirectoryFile[] = [
    makeFile("pilot-set.json", new Uint8Array(pilotSetBytes)),
    ...candidates.map((candidate) =>
      makeFile(candidate.audio.path, audioBytes.get(candidate.candidate_id)!),
    ),
  ];
  return { pilotSet, pilotSetBytes, files };
}

function makeFile(path: string, bytes: Uint8Array): DirectoryFile {
  return {
    name: path.split("/").at(-1)!,
    webkitRelativePath: `bundle/${path}`,
    async arrayBuffer() {
      return bytes.slice().buffer;
    },
  };
}

function hex(value: number): string {
  return value.toString(16).padStart(64, "0");
}

async function makeCandidateId(takeId: string): Promise<string> {
  return sha256Hex(new TextEncoder().encode(`{"protocol":"n3-pilot-v1","take_id":"${takeId}"}`));
}

async function makeGroupId(
  model: string,
  scenario: string,
  line: string,
  variant: string,
): Promise<string> {
  return sha256Hex(
    new TextEncoder().encode(
      `{"line":"${line}","model":"${model}","protocol":"n3-pilot-v1","scenario":"${scenario}","variant":"${variant}"}`,
    ),
  );
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}
