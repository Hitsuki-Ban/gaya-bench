import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";

const distDirectory = path.resolve("dist");
const publicEntry = path.join(distDirectory, "index.html");
const internalEntry = path.join(distDirectory, "internal.html");
const forbiddenText = ["gaya-bench-internal-ui-v1", "pilot-decision.json", "curation.json"];

await requireFile(publicEntry, "公開 build の index.html がありません。");

if (await exists(internalEntry)) {
  throw new Error("公開 build に internal.html が含まれています。");
}

for (const file of await textArtifacts(distDirectory)) {
  const content = await readFile(file, "utf8");
  for (const marker of forbiddenText) {
    if (content.includes(marker)) {
      throw new Error(
        `公開 build にローカル評価用 marker が含まれています: ${path.relative(
          distDirectory,
          file,
        )} (${marker})`,
      );
    }
  }
}

console.log("公開 build にローカル評価 entry / marker は含まれていません。");

async function textArtifacts(directory) {
  const files = [];

  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await textArtifacts(entryPath)));
    } else if (/\.(?:css|html|js)$/u.test(entry.name)) {
      files.push(entryPath);
    }
  }

  return files;
}

async function exists(file) {
  try {
    await stat(file);
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") {
      return false;
    }
    throw error;
  }
}

async function requireFile(file, message) {
  if (!(await exists(file))) {
    throw new Error(message);
  }
}
