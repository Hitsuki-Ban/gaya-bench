import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, lazyPlugins, normalizePath, type Plugin } from "vite-plus";

import { gayaDataPlugin } from "./scripts/gaya-data-plugin.ts";

const INTERNAL_MODULE_FILES = new Set([
  "src/components/human-rubric-fields.tsx",
  "src/internal-main.tsx",
  "src/lib/canonical-json.ts",
  "src/lib/local-directory.ts",
  "src/lib/sha256.ts",
  "src/pages/curate-page.tsx",
  "src/pages/pilot-page.tsx",
]);
const INTERNAL_MODULE_DIRECTORIES = ["src/curate/", "src/internal/", "src/pilot/"];

// https://vite.dev/config/
export default defineConfig({
  fmt: {},
  lint: {
    plugins: ["react", "typescript", "oxc"],
    rules: {
      "react/rules-of-hooks": "error",
      "react/only-export-components": [
        "warn",
        {
          allowConstantExport: true,
        },
      ],
      "vite-plus/prefer-vite-plus-imports": "error",
    },
    options: {
      typeAware: true,
      typeCheck: true,
    },
    jsPlugins: [
      {
        name: "vite-plus",
        specifier: "vite-plus/oxlint-plugin",
      },
    ],
  },
  plugins: lazyPlugins(() => [
    gayaDataPlugin({
      repositoryRoot: path.resolve(import.meta.dirname, ".."),
    }),
    publicBundleBoundaryPlugin(),
    react(),
    tailwindcss(),
  ]),
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
    },
  },
  test: {
    include: ["src/**/*.test.ts"],
  },
});

function publicBundleBoundaryPlugin(): Plugin {
  return {
    name: "gaya-public-bundle-boundary",
    apply: "build",
    generateBundle(_options, bundle) {
      const leaks: string[] = [];

      for (const [outputFile, output] of Object.entries(bundle)) {
        if (output.type !== "chunk") {
          continue;
        }

        for (const moduleId of Object.keys(output.modules)) {
          const sourceFile = siteRelativeModuleId(moduleId);
          if (isInternalModule(sourceFile)) {
            leaks.push(`${outputFile}: ${sourceFile}`);
          }
        }
      }

      if (leaks.length > 0) {
        this.error(
          `公開 build にローカル評価 module が含まれています:\n${leaks
            .sort()
            .map((leak) => `- ${leak}`)
            .join("\n")}`,
        );
      }
    },
  };
}

function siteRelativeModuleId(moduleId: string): string {
  const sourcePath = normalizePath(moduleId).split("?", 1)[0]!;
  return normalizePath(path.relative(import.meta.dirname, sourcePath));
}

function isInternalModule(sourceFile: string): boolean {
  return (
    INTERNAL_MODULE_FILES.has(sourceFile) ||
    INTERNAL_MODULE_DIRECTORIES.some((directory) => sourceFile.startsWith(directory))
  );
}
