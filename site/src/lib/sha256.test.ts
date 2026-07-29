import { describe, expect, it } from "vite-plus/test";

import { sha256Hex } from "./sha256";

describe("sha256Hex", () => {
  it("candidate-set の原始 UTF-8 bytes を既知 SHA-256 にする", async () => {
    const bytes = new TextEncoder().encode("abc");

    await expect(sha256Hex(bytes)).resolves.toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });
});
