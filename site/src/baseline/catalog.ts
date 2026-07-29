import { isSafeClipPath } from "../lib/clip-path";
import { assertCanonicalJsonBytes } from "../lib/canonical-json";
import type { DirectoryFile, ObjectUrlFactory } from "../lib/local-directory";
import { sha256Hex } from "../lib/sha256";
import { loadCurateCatalog } from "../curate/catalog";
import { compareGroupTuple, groupKey } from "../curate/types";
import { validateBaselineEvidence } from "./evidence";
import type { BaselineCatalog, BaselineGroup } from "./types";

const EXPECTED_REFERENCE_COUNT = 381;
const HEX_64 = /^[0-9a-f]{64}$/;
const BASELINE_ROOT_KEYS = [
  "candidate_set_sha256",
  "format_version",
  "references",
  "source_manifest_sha256",
] as const;
const REFERENCE_KEYS = [
  "candidate_sha256",
  "comparison",
  "legacy_sha256",
  "line",
  "local_path",
  "model",
  "public_path",
  "scenario",
  "variant",
] as const;

type GroupIdentity = {
  readonly model: string;
  readonly scenario: string;
  readonly line: string;
  readonly variant: string;
};

type CandidateEntry = GroupIdentity & {
  take_index: number;
  sha256: string;
  path: string;
};

type FailureEntry = GroupIdentity;

type ReferenceEntry = GroupIdentity & {
  public_path: string;
  legacy_sha256: string;
  local_path: string;
  candidate_sha256: string | null;
  comparison: "identical" | "different" | "no_candidate";
};

function defaultObjectUrls(): ObjectUrlFactory {
  return {
    create(file) {
      if (!(file instanceof Blob)) {
        throw new Error("選択音声が browser File ではありません。");
      }
      return URL.createObjectURL(file);
    },
    revoke(url) {
      URL.revokeObjectURL(url);
    },
  };
}

function indexDirectoryFiles(
  selectedFiles: Iterable<DirectoryFile>,
): ReadonlyMap<string, DirectoryFile> {
  const files = [...selectedFiles];
  if (files.length === 0) {
    throw new Error("run root のファイルが選択されていません。");
  }

  let root: string | null = null;
  const byPath = new Map<string, DirectoryFile>();
  for (const file of files) {
    const relativePath = file.webkitRelativePath;
    if (relativePath.length === 0 || relativePath.includes("\\")) {
      throw new Error(`webkitRelativePath が不正です: ${relativePath || file.name}`);
    }
    const segments = relativePath.split("/");
    if (
      segments.length < 2 ||
      segments.some((segment) => segment.length === 0 || segment === "." || segment === "..")
    ) {
      throw new Error(`webkitRelativePath が run root を表していません: ${relativePath}`);
    }
    if (file.name !== segments.at(-1)) {
      throw new Error(`file name と webkitRelativePath が一致しません: ${relativePath}`);
    }
    if (root === null) {
      root = segments[0]!;
    } else if (segments[0] !== root) {
      throw new Error(`複数の run root が選択されています: ${root}, ${segments[0]}`);
    }

    const pathWithinRoot = segments.slice(1).join("/");
    if (byPath.has(pathWithinRoot)) {
      throw new Error(`run root 内の path が重複しています: ${pathWithinRoot}`);
    }
    byPath.set(pathWithinRoot, file);
  }
  return byPath;
}

function requiredFile(files: ReadonlyMap<string, DirectoryFile>, path: string): DirectoryFile {
  const file = files.get(path);
  if (!file) {
    throw new Error(`run root に必須ファイルがありません: ${path}`);
  }
  return file;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
  label: string,
): void {
  const actual = Object.keys(value).sort();
  const sortedExpected = [...expected].sort();
  if (
    actual.length !== sortedExpected.length ||
    actual.some((key, index) => key !== sortedExpected[index])
  ) {
    throw new Error(`${label} のキー構成が不正です。`);
  }
}

function requireString(record: Record<string, unknown>, key: string, label: string): string {
  const value = record[key];
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label}.${key} は空でない文字列である必要があります。`);
  }
  return value;
}

function requireSha256(record: Record<string, unknown>, key: string, label: string): string {
  const value = requireString(record, key, label);
  if (!HEX_64.test(value)) {
    throw new Error(`${label}.${key} は小文字64桁のSHA-256である必要があります。`);
  }
  return value;
}

function requireSafePath(record: Record<string, unknown>, key: string, label: string): string {
  const value = requireString(record, key, label);
  if (!isSafeClipPath(value)) {
    throw new Error(`${label}.${key} が安全な相対パスではありません。`);
  }
  return value;
}

function requireIdentity(record: Record<string, unknown>, label: string): GroupIdentity {
  return {
    model: requireString(record, "model", label),
    scenario: requireString(record, "scenario", label),
    line: requireString(record, "line", label),
    variant: requireString(record, "variant", label),
  };
}

function decodeJson(bytes: ArrayBuffer, label: string): unknown {
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(bytes));
  } catch {
    throw new Error(`${label} はUTF-8として読めません。`);
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`${label} は有効なJSONではありません。`);
  }
}

function parseMarker(bytes: ArrayBuffer, label: string): string {
  let marker: string;
  try {
    marker = new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(bytes));
  } catch {
    throw new Error(`${label} はUTF-8として読めません。`);
  }
  if (!HEX_64.test(marker)) {
    throw new Error(`${label} は改行なし・小文字64桁のSHA-256である必要があります。`);
  }
  return marker;
}

function parseCandidateSet(bytes: ArrayBuffer): {
  candidates: CandidateEntry[];
  failures: FailureEntry[];
  document: Readonly<Record<string, unknown>>;
} {
  const parsed = decodeJson(bytes, "candidate-set.json");
  if (!isRecord(parsed)) {
    throw new Error("candidate-set.json のルートはオブジェクトである必要があります。");
  }
  if (!Array.isArray(parsed.candidates) || !Array.isArray(parsed.failures)) {
    throw new Error("candidate-set.json の candidates / failures は配列である必要があります。");
  }

  const candidates = parsed.candidates.map((value, index): CandidateEntry => {
    const label = `candidate-set.json candidates[${index}]`;
    if (!isRecord(value)) {
      throw new Error(`${label} はオブジェクトである必要があります。`);
    }
    if (value.take_index !== 1) {
      throw new Error(`${label}.take_index は1である必要があります。`);
    }
    return {
      ...requireIdentity(value, label),
      take_index: 1,
      sha256: requireSha256(value, "sha256", label),
      path: requireSafePath(value, "path", label),
    };
  });
  const failures = parsed.failures.map((value, index): FailureEntry => {
    const label = `candidate-set.json failures[${index}]`;
    if (!isRecord(value)) {
      throw new Error(`${label} はオブジェクトである必要があります。`);
    }
    return requireIdentity(value, label);
  });

  const seen = new Set<string>();
  for (const candidate of candidates) {
    const key = groupKey(candidate);
    if (seen.has(key)) {
      throw new Error(`candidate-set.json に重複groupがあります: ${key}`);
    }
    seen.add(key);
  }
  for (const failure of failures) {
    const key = groupKey(failure);
    if (seen.has(key)) {
      throw new Error(`candidate-set.json の candidates / failures に重複groupがあります: ${key}`);
    }
    seen.add(key);
  }
  if (seen.size !== EXPECTED_REFERENCE_COUNT) {
    throw new Error(
      `candidate-set.json は${EXPECTED_REFERENCE_COUNT} groupを網羅する必要があります（実際: ${seen.size}）。`,
    );
  }

  return { candidates, failures, document: parsed };
}

function parseBaselineReference(
  bytes: ArrayBuffer,
  candidateSetSha256: string,
): { sourceManifestSha256: string; references: ReferenceEntry[] } {
  assertCanonicalJsonBytes(bytes, "baseline-reference.json");
  const parsed = decodeJson(bytes, "baseline-reference.json");
  if (!isRecord(parsed)) {
    throw new Error("baseline-reference.json のルートはオブジェクトである必要があります。");
  }
  assertExactKeys(parsed, BASELINE_ROOT_KEYS, "baseline-reference.json");
  if (parsed.format_version !== 1) {
    throw new Error("baseline-reference.json.format_version は1である必要があります。");
  }
  const sourceManifestSha256 = requireSha256(
    parsed,
    "source_manifest_sha256",
    "baseline-reference.json",
  );
  const boundCandidateSetSha256 = requireSha256(
    parsed,
    "candidate_set_sha256",
    "baseline-reference.json",
  );
  if (boundCandidateSetSha256 !== candidateSetSha256) {
    throw new Error(
      "baseline-reference.json の candidate_set_sha256 が candidate-set.json と一致しません。",
    );
  }
  if (!Array.isArray(parsed.references)) {
    throw new Error("baseline-reference.json.references は配列である必要があります。");
  }
  if (parsed.references.length !== EXPECTED_REFERENCE_COUNT) {
    throw new Error(
      `baseline-reference.json.references は${EXPECTED_REFERENCE_COUNT}件である必要があります（実際: ${parsed.references.length}）。`,
    );
  }

  const references = parsed.references.map((value, index): ReferenceEntry => {
    const label = `baseline-reference.json references[${index}]`;
    if (!isRecord(value)) {
      throw new Error(`${label} はオブジェクトである必要があります。`);
    }
    assertExactKeys(value, REFERENCE_KEYS, label);
    const identity = requireIdentity(value, label);
    const publicPath = requireString(value, "public_path", label);
    if (!isSafeClipPath(publicPath)) {
      throw new Error(`${label}.public_path が安全な相対パスではありません。`);
    }
    const legacySha256 = requireSha256(value, "legacy_sha256", label);
    const localPath = requireString(value, "local_path", label);
    const expectedLocalPath =
      `reference/${identity.model}/${identity.scenario}/` +
      `${identity.line}/${identity.variant}.opus`;
    if (localPath !== expectedLocalPath) {
      throw new Error(
        `${label}.local_path が規定パスと一致しません（期待: ${expectedLocalPath}）。`,
      );
    }
    if (!isSafeClipPath(localPath)) {
      throw new Error(`${label}.local_path が安全な相対パスではありません。`);
    }
    const candidateShaValue = value.candidate_sha256;
    if (
      candidateShaValue !== null &&
      (typeof candidateShaValue !== "string" || !HEX_64.test(candidateShaValue))
    ) {
      throw new Error(
        `${label}.candidate_sha256 はnullまたは小文字64桁のSHA-256である必要があります。`,
      );
    }
    const comparison = value.comparison;
    if (comparison !== "identical" && comparison !== "different" && comparison !== "no_candidate") {
      throw new Error(`${label}.comparison が不正です。`);
    }
    return {
      ...identity,
      public_path: publicPath,
      legacy_sha256: legacySha256,
      local_path: localPath,
      candidate_sha256: candidateShaValue,
      comparison,
    };
  });

  const seen = new Set<string>();
  for (let index = 0; index < references.length; index += 1) {
    const reference = references[index];
    const key = groupKey(reference);
    if (seen.has(key)) {
      throw new Error(`baseline-reference.json に重複groupがあります: ${key}`);
    }
    seen.add(key);
    if (index > 0 && compareGroupTuple(references[index - 1], reference) >= 0) {
      throw new Error("baseline-reference.json.references はgroup tuple昇順である必要があります。");
    }
  }

  return { sourceManifestSha256, references };
}

function validateBindings(
  candidates: CandidateEntry[],
  failures: FailureEntry[],
  references: ReferenceEntry[],
): void {
  const referencesByGroup = new Map(
    references.map((reference) => [groupKey(reference), reference]),
  );
  const expectedGroups = new Set([...candidates.map(groupKey), ...failures.map(groupKey)]);
  for (const reference of references) {
    if (!expectedGroups.has(groupKey(reference))) {
      throw new Error(
        `baseline-reference.json にcandidate-set外のgroupがあります: ${groupKey(reference)}`,
      );
    }
  }

  for (const candidate of candidates) {
    const key = groupKey(candidate);
    const reference = referencesByGroup.get(key);
    if (!reference) {
      throw new Error(`baseline-reference.json にgroupがありません: ${key}`);
    }
    if (reference.candidate_sha256 !== candidate.sha256) {
      throw new Error(
        `baseline-reference.json の candidate_sha256 がcandidateと一致しません: ${key}`,
      );
    }
    const expectedComparison =
      reference.legacy_sha256 === candidate.sha256 ? "identical" : "different";
    if (reference.comparison !== expectedComparison) {
      throw new Error(`baseline-reference.json の comparison がSHA比較結果と一致しません: ${key}`);
    }
  }
  for (const failure of failures) {
    const key = groupKey(failure);
    const reference = referencesByGroup.get(key);
    if (!reference) {
      throw new Error(`baseline-reference.json にgroupがありません: ${key}`);
    }
    if (reference.candidate_sha256 !== null || reference.comparison !== "no_candidate") {
      throw new Error(
        `candidate0 groupは candidate_sha256=null / comparison=no_candidate である必要があります: ${key}`,
      );
    }
  }
}

function expectedFilePaths(
  candidates: CandidateEntry[],
  references: ReferenceEntry[],
  evidencePaths: ReadonlySet<string>,
): Set<string> {
  return new Set([
    "manifest-v4.json",
    "candidate-set.json",
    "candidate-set.sha256",
    "baseline-reference.json",
    "baseline-reference.sha256",
    ...evidencePaths,
    ...candidates.map((candidate) => candidate.path),
    ...references.map((reference) => reference.local_path),
  ]);
}

function assertExactFileCoverage(
  actual: ReadonlyMap<string, DirectoryFile>,
  expected: ReadonlySet<string>,
): void {
  for (const path of expected) {
    if (!actual.has(path)) {
      throw new Error(`必須ファイルがありません: ${path}`);
    }
  }
  for (const path of actual.keys()) {
    if (!expected.has(path)) {
      throw new Error(`想定外のファイルがあります: ${path}`);
    }
  }
}

function createCurateInputFiles(
  selectedFiles: readonly DirectoryFile[],
  indexedFiles: ReadonlyMap<string, DirectoryFile>,
  candidates: readonly CandidateEntry[],
): {
  readonly files: readonly DirectoryFile[];
  readonly authorities: ReadonlyMap<DirectoryFile, DirectoryFile>;
} {
  const root = selectedFiles[0]!.webkitRelativePath.split("/")[0]!;
  const authorities = new Map<DirectoryFile, DirectoryFile>();
  const candidateViews = candidates.map((candidate): DirectoryFile => {
    const authority = requiredFile(indexedFiles, candidate.path);
    const name = `take-${String(candidate.take_index).padStart(4, "0")}.opus`;
    const view = {
      name,
      webkitRelativePath:
        `${root}/audio/${candidate.model}/${candidate.scenario}/` +
        `${candidate.line}/${candidate.variant}/${name}`,
      arrayBuffer() {
        return authority.arrayBuffer();
      },
    };
    authorities.set(view, authority);
    return view;
  });
  return {
    files: [...selectedFiles, ...candidateViews],
    authorities,
  };
}

export async function loadBaselineCatalog(
  files: Iterable<DirectoryFile>,
  objectUrls: ObjectUrlFactory = defaultObjectUrls(),
): Promise<BaselineCatalog> {
  const selectedFiles = [...files];
  const indexedFiles = indexDirectoryFiles(selectedFiles);
  const candidateSetBytes = await requiredFile(indexedFiles, "candidate-set.json").arrayBuffer();
  const candidateSet = parseCandidateSet(candidateSetBytes);
  const curateInput = createCurateInputFiles(selectedFiles, indexedFiles, candidateSet.candidates);
  const curateCatalog = await loadCurateCatalog(curateInput.files, {
    create(file) {
      return objectUrls.create(curateInput.authorities.get(file) ?? file);
    },
    revoke(url) {
      objectUrls.revoke(url);
    },
  });
  const referenceUrls: string[] = [];

  try {
    const baselineReferenceBytes = await requiredFile(
      indexedFiles,
      "baseline-reference.json",
    ).arrayBuffer();
    const baselineReferenceMarkerBytes = await requiredFile(
      indexedFiles,
      "baseline-reference.sha256",
    ).arrayBuffer();
    const candidateSetSha256 = await sha256Hex(candidateSetBytes);
    if (candidateSetSha256 !== curateCatalog.candidateSetSha256) {
      throw new Error("candidate-set.json のSHAがcuration catalogと一致しません。");
    }
    const baselineReferenceSha256 = await sha256Hex(baselineReferenceBytes);
    const baselineReferenceMarker = parseMarker(
      baselineReferenceMarkerBytes,
      "baseline-reference.sha256",
    );
    if (baselineReferenceMarker !== baselineReferenceSha256) {
      throw new Error(
        "baseline-reference.sha256 がbaseline-reference.jsonの生バイトSHAと一致しません。",
      );
    }

    const baselineReference = parseBaselineReference(baselineReferenceBytes, candidateSetSha256);
    validateBindings(candidateSet.candidates, candidateSet.failures, baselineReference.references);
    const evidencePaths = await validateBaselineEvidence(
      indexedFiles,
      candidateSet.document,
      baselineReference.references,
      baselineReference.sourceManifestSha256,
    );
    assertExactFileCoverage(
      indexedFiles,
      expectedFilePaths(candidateSet.candidates, baselineReference.references, evidencePaths),
    );

    const referencesByGroup = new Map(
      baselineReference.references.map((reference) => [groupKey(reference), reference]),
    );
    const referenceFiles = new Map<string, DirectoryFile>();
    await Promise.all(
      baselineReference.references.map(async (reference) => {
        const referenceFile = requiredFile(indexedFiles, reference.local_path);
        const actualLegacySha256 = await sha256Hex(await referenceFile.arrayBuffer());
        if (actualLegacySha256 !== reference.legacy_sha256) {
          throw new Error(`現行公開reference音声のSHAが不一致です: ${reference.local_path}`);
        }
        referenceFiles.set(groupKey(reference), referenceFile);
      }),
    );
    const candidateKeys = new Set(candidateSet.candidates.map((candidate) => groupKey(candidate)));
    if (
      curateCatalog.groups.length !== candidateKeys.size ||
      curateCatalog.groups.some((group) => !candidateKeys.has(groupKey(group)))
    ) {
      throw new Error("curation catalogのgroup集合がbaseline candidate集合と一致しません。");
    }

    const groups: BaselineGroup[] = [];
    for (const group of curateCatalog.groups) {
      if (group.candidates.length !== 1) {
        throw new Error(
          `baseline curation groupはcandidate 1件である必要があります: ${groupKey(group)}`,
        );
      }
      const reference = referencesByGroup.get(groupKey(group));
      if (
        !reference ||
        reference.comparison === "no_candidate" ||
        reference.candidate_sha256 === null
      ) {
        throw new Error(`curatable groupのreference bindingが不正です: ${groupKey(group)}`);
      }
      const referenceFile = referenceFiles.get(groupKey(group));
      if (!referenceFile) {
        throw new Error(`必須ファイルがありません: ${reference.local_path}`);
      }
      const referenceUrl = objectUrls.create(referenceFile);
      referenceUrls.push(referenceUrl);
      groups.push({
        model: group.model,
        scenario: group.scenario,
        line: group.line,
        variant: group.variant,
        scenarioTitle: group.scenarioTitle,
        lineText: group.lineText,
        delivery: group.delivery,
        candidate: group.candidates[0],
        candidateSha256: reference.candidate_sha256,
        reference: {
          audio: {
            key: `baseline-reference:${baselineReferenceSha256}:${groupKey(group)}`,
            url: referenceUrl,
          },
          publicPath: reference.public_path,
          sha256: reference.legacy_sha256,
          comparison: reference.comparison,
        },
      });
    }

    let disposed = false;
    return {
      candidateSetSha256,
      baselineReferenceSha256,
      groups,
      exportCandidatesByGroup: curateCatalog.exportCandidatesByGroup,
      auditedNoCandidateCount: candidateSet.failures.length,
      dispose() {
        if (disposed) return;
        disposed = true;
        curateCatalog.dispose();
        for (const url of referenceUrls) {
          objectUrls.revoke(url);
        }
      },
    };
  } catch (error) {
    curateCatalog.dispose();
    for (const url of referenceUrls) {
      objectUrls.revoke(url);
    }
    throw error;
  }
}
