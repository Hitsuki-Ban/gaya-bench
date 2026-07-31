import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative, resolve } from "node:path";

import { describe, expect, it } from "vite-plus/test";

import type { DirectoryFile, ObjectUrlFactory } from "@/lib/local-directory";

import { loadBaselineCatalog } from "./baseline-contract";
import { buildBaselineDecisionJson } from "./baseline-export";
import { createBaselineDraft } from "./baseline-storage";

describe("Phase B Python / site cross contract", () => {
  it("Python producerの363-group実fixtureをsiteで読み、site decisionをPython validatorへ戻す", async () => {
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
        ["run", "--project", pipelineRoot, "python", generator, fixtureRoot],
        { encoding: "utf8" },
      );
      expect(produced.status, `${produced.stdout}\n${produced.stderr}`).toBe(0);

      const bundleRoot = join(fixtureRoot, "bundle");
      const catalog = await loadBaselineCatalog(diskDirectoryFiles(bundleRoot), NOOP_OBJECT_URLS);
      expect(catalog.groups).toHaveLength(363);
      expect(catalog.groups.every((group) => group.exportCandidates.length === 3)).toBe(true);
      expect(catalog.groups[0]!.roleEpochSha256).toMatch(/^[0-9a-f]{64}$/);

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
          "python",
          "-c",
          [
            "import json,sys",
            "from gaya_pipeline.completion_selection import canonical_completion_decision_bytes",
            "sys.stdout.buffer.write(canonical_completion_decision_bytes(json.load(sys.stdin)))",
          ].join(";"),
        ],
        { encoding: "utf8", input: decision },
      );
      expect(validation.status, validation.stderr).toBe(0);
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
