import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative, resolve } from "node:path";

import { describe, expect, it } from "vite-plus/test";

import type { DirectoryFile, ObjectUrlFactory } from "@/lib/local-directory";
import { canonicalJson } from "@/lib/canonical-json";
import { sha256Text } from "@/lib/sha256";

import { loadBaselineCatalog } from "./baseline-contract";
import { buildBaselineDecisionJson } from "./baseline-export";
import { createBaselineDraft } from "./baseline-storage";
import { BASELINE_WORKFLOW, validateListeningBundle } from "../../scripts/listening-app-server";

describe("Phase B Python / site cross contract", () => {
  it("Python producerの597-group実fixtureとgroup別minimumを検証し、site decisionをPython validatorへ戻す", async () => {
    const temporary = mkdtempSync(join(tmpdir(), "gaya-phase-b-cross-"));
    try {
      const fixtureRoot = join(temporary, "fixture");
      const siteRoot = process.cwd();
      const pipelineRoot = resolve(siteRoot, "../pipeline");
      const generator = resolve(
        siteRoot,
        "src/completion/test-fixtures/generate-phase-b-bundle.py",
      );
      const produced = spawnSync(
        "uv",
        ["run", "--project", pipelineRoot, "--locked", "python", generator, fixtureRoot],
        { encoding: "utf8" },
      );
      expect(produced.status, processFailure(produced)).toBe(0);

      const bundleRoot = join(fixtureRoot, "bundle");
      const authorityPlan = readFileSync(join(fixtureRoot, "fixture-plan.json"), "utf8");
      const daemonBundle = await validateListeningBundle(
        BASELINE_WORKFLOW,
        bundleRoot,
        await sha256Text(authorityPlan),
      );
      expect(daemonBundle.candidates.size).toBeGreaterThanOrEqual(597);
      expect(daemonBundle.document.groups as unknown[]).toHaveLength(597);
      const files = diskDirectoryFiles(bundleRoot);
      const catalog = await loadBaselineCatalog(files, NOOP_OBJECT_URLS);
      expect(catalog.groups).toHaveLength(597);
      const aivisGroups = catalog.groups.filter((group) => group.model === "aivisspeech-kohaku");
      expect(aivisGroups.length).toBeGreaterThan(0);
      expect(
        aivisGroups.every(
          (group) => group.minimumEligibleCandidates === 1 && group.exportCandidates.length === 1,
        ),
      ).toBe(true);
      expect(catalog.groups[0]!.roleEpochSha256).toMatch(/^[0-9a-f]{64}$/);

      const sourceMap = JSON.parse(
        readFileSync(join(bundleRoot, "phase-b-source-map-v1.json"), "utf8"),
      ) as { groups: Array<Record<string, unknown>> };
      const belowDeclaredMinimum = structuredClone(sourceMap);
      const firstGroup = catalog.groups[0]!;
      belowDeclaredMinimum.groups[0]!.minimum_eligible_candidates =
        firstGroup.exportCandidates.length + 1;
      await expect(
        loadBaselineCatalog(await replaceSourceMap(files, belowDeclaredMinimum), NOOP_OBJECT_URLS),
      ).rejects.toThrow("minimum_eligible_candidates以上");

      const withUnknownGroupField = structuredClone(sourceMap);
      withUnknownGroupField.groups[0]!.unexpected = true;
      await expect(
        loadBaselineCatalog(await replaceSourceMap(files, withUnknownGroupField), NOOP_OBJECT_URLS),
      ).rejects.toThrow("exact contract");

      const threeCandidateIndex = catalog.groups.findIndex(
        (group) => group.minimumEligibleCandidates === 3,
      );
      expect(threeCandidateIndex).toBeGreaterThanOrEqual(0);
      const loweredMinimum = structuredClone(sourceMap);
      loweredMinimum.groups[threeCandidateIndex]!.minimum_eligible_candidates = 1;
      const reboundCatalog = await loadBaselineCatalog(
        await replaceSourceMap(files, loweredMinimum),
        NOOP_OBJECT_URLS,
      );
      expect(reboundCatalog.groups[threeCandidateIndex]!.minimumEligibleCandidates).toBe(1);
      expect(reboundCatalog.groups[threeCandidateIndex]!.groupSha256).not.toBe(
        catalog.groups[threeCandidateIndex]!.groupSha256,
      );
      reboundCatalog.dispose();

      const empty = createBaselineDraft(catalog);
      const rubric = {
        content_correct: true,
        prompt_leakage: false,
        reading_correct: true,
        accent_naturalness: 4,
        role_match: 4,
        delivery_match: 4,
        audio_quality: 4,
        adoptable: true,
        notes: "",
      } as const;
      const complete = {
        ...empty,
        groups: empty.groups.map((group) => ({
          ...group,
          heard_candidate_ids: group.candidates.map((candidate) => candidate.take_id),
          candidates: group.candidates.map((candidate) => ({
            ...candidate,
            rubric,
          })),
          decision: {
            type: "selected" as const,
            take_id: group.candidates[0]!.take_id,
          },
        })),
      };
      const decision = buildBaselineDecisionJson(catalog, complete);
      const validation = spawnSync(
        "uv",
        [
          "run",
          "--project",
          pipelineRoot,
          "--locked",
          "python",
          generator,
          "validate-decision",
          bundleRoot,
        ],
        { encoding: "utf8", input: decision, maxBuffer: 16 * 1024 * 1024 },
      );
      expect(validation.status, processFailure(validation)).toBe(0);
      expect(validation.stdout).toBe(decision);
      catalog.dispose();
    } finally {
      rmSync(temporary, { force: true, recursive: true });
    }
  }, 120_000);
});

function diskDirectoryFiles(root: string): readonly DirectoryFile[] {
  return walk(root).map((path) => {
    const bytes = readFileSync(path);
    const relativePath = relative(root, path).replaceAll("\\", "/");
    return {
      name: relativePath.split("/").at(-1)!,
      webkitRelativePath: `bundle/${relativePath}`,
      async arrayBuffer() {
        return bytes.buffer.slice(
          bytes.byteOffset,
          bytes.byteOffset + bytes.byteLength,
        ) as ArrayBuffer;
      },
    };
  });
}

async function replaceSourceMap(
  files: readonly DirectoryFile[],
  sourceMap: unknown,
): Promise<readonly DirectoryFile[]> {
  const source = canonicalJson(sourceMap, "Phase B source map test fixture");
  const marker = await sha256Text(source);
  return files.map((file) => {
    if (file.name === "phase-b-source-map-v1.json") {
      return memoryDirectoryFile(file.webkitRelativePath, source);
    }
    if (file.name === "phase-b-source-map-v1.sha256") {
      return memoryDirectoryFile(file.webkitRelativePath, marker);
    }
    return file;
  });
}

function memoryDirectoryFile(webkitRelativePath: string, contents: string): DirectoryFile {
  return {
    name: webkitRelativePath.split(/[\\/]/).at(-1)!,
    webkitRelativePath,
    async arrayBuffer() {
      return new TextEncoder().encode(contents).buffer;
    },
  };
}

function walk(root: string): readonly string[] {
  const paths: string[] = [];
  for (const name of readdirSync(root)) {
    const path = join(root, name);
    if (statSync(path).isDirectory()) {
      paths.push(...walk(path));
    } else {
      paths.push(path);
    }
  }
  return paths;
}

const NOOP_OBJECT_URLS: ObjectUrlFactory = {
  create(file) {
    return `blob:${file.webkitRelativePath}`;
  },
  revoke() {},
};

function processFailure(result: {
  readonly error?: Error;
  readonly stderr: string | null;
  readonly stdout: string | null;
}): string {
  return [result.error?.message, result.stdout, result.stderr]
    .filter((value): value is string => Boolean(value))
    .join("\n");
}
