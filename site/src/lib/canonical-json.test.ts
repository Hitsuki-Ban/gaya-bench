import { describe, expect, it } from "vite-plus/test";

import { assertCanonicalJsonBytes, canonicalJson } from "@/lib/canonical-json";

describe("canonical JSON", () => {
  it("既存 artifact writer の safe-integer 契約を維持する", () => {
    expect(canonicalJson({ z: 1, a: { y: true, x: null } }, "artifact")).toBe(
      '{"a":{"x":null,"y":true},"z":1}',
    );
    expect(() => canonicalJson({ score: 1.5 }, "artifact")).toThrow("安全な整数");
  });

  it("Python canonical JSON の UTF-8、key 順を受け入れる", () => {
    const source = '{"a":[1.0,0.0001,1e-05,1e+16,-0.0],"nested":{"a":null,"z":"値"},"z":true}';

    expect(() => assertCanonicalJsonBytes(bytes(source), "document")).not.toThrow();
  });

  it.each(["0", "0.0", "-0.0", "0.1", "0.0001", "1e-05", "-1e-05", "1000000000000000.0", "1e+16"])(
    "Python canonical number を受け入れる: %s",
    (number) => {
      expect(() => assertCanonicalJsonBytes(bytes(`{"a":${number}}`), "document")).not.toThrow();
    },
  );

  it.each([
    ["0e+16", "positive zero"],
    ["-0e+16", "negative zero"],
    ["-0", "integer negative zero"],
    ["0.10000000000000001", "redundant rounding digits"],
    ["1.0000000000000001", "rounded integer float"],
    ["1e-04", "-4 must be fixed"],
    ["0.00001", "-5 must be scientific"],
    ["1e+15", "15 must be fixed"],
    ["10000000000000000.0", "16 must be scientific"],
    ["1e-5", "exponent must have two digits"],
  ])("non-canonical number を拒否する: %s (%s)", (number) => {
    expect(() => assertCanonicalJsonBytes(bytes(`{"a":${number}}`), "document")).toThrow(
      "canonical JSON bytes",
    );
  });

  it.each([
    ['{"z":1,"a":2}', "key reorder"],
    ['{"a":1, "z":2}', "whitespace"],
    ['{"a":1}\n', "trailing newline"],
    ['{"a":1.00}', "non-canonical float"],
    ['{"a":1e+04}', "non-canonical exponent"],
  ])("%s を拒否する: %s", (source) => {
    expect(() => assertCanonicalJsonBytes(bytes(source), "document")).toThrow(
      "canonical JSON bytes",
    );
  });

  it("UTF-8 BOM を拒否する", () => {
    const source = bytes('{"a":1}');
    const withBom = new Uint8Array(source.byteLength + 3);
    withBom.set([0xef, 0xbb, 0xbf]);
    withBom.set(new Uint8Array(source), 3);

    expect(() => assertCanonicalJsonBytes(withBom.buffer, "document")).toThrow(
      "canonical JSON bytes",
    );
  });
});

function bytes(source: string): ArrayBuffer {
  return new TextEncoder().encode(source).buffer;
}
