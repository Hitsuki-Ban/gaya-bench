export function canonicalJson(value: unknown, artifactLabel: string): string {
  return JSON.stringify(canonicalize(value, artifactLabel));
}

export function assertCanonicalJsonBytes(bytes: ArrayBuffer, artifactLabel: string): void {
  const raw = new Uint8Array(bytes);
  if (raw.length >= 3 && raw[0] === 0xef && raw[1] === 0xbb && raw[2] === 0xbf) {
    throw canonicalBytesError(artifactLabel);
  }
  let source: string;
  try {
    source = new TextDecoder("utf-8", { fatal: true }).decode(raw);
  } catch {
    throw new Error(`${artifactLabel} は正しい UTF-8 ではありません。`);
  }
  let canonical: string;
  try {
    canonical = new CanonicalJsonSourceParser(source).parse();
  } catch {
    throw canonicalBytesError(artifactLabel);
  }
  if (source !== canonical) {
    throw canonicalBytesError(artifactLabel);
  }
}

function canonicalize(value: unknown, artifactLabel: string): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) {
      throw new Error(`${artifactLabel} の数値は安全な整数である必要があります。`);
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => canonicalize(item, artifactLabel));
  }
  if (typeof value === "object" && value !== null) {
    const result: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) {
      if (!/^[\x20-\x7e]+$/.test(key)) {
        throw new Error(`${artifactLabel} の key は ASCII である必要があります: ${key}`);
      }
      result[key] = canonicalize((value as Record<string, unknown>)[key], artifactLabel);
    }
    return result;
  }
  throw new Error(`${artifactLabel} に JSON ではない値があります。`);
}

class CanonicalJsonSourceParser {
  private position = 0;
  private readonly source: string;

  constructor(source: string) {
    this.source = source;
  }

  parse(): string {
    const result = this.parseValue();
    if (this.position !== this.source.length) {
      throw new Error("trailing bytes");
    }
    return result;
  }

  private parseValue(): string {
    const token = this.source[this.position];
    if (token === "{") {
      return this.parseObject();
    }
    if (token === "[") {
      return this.parseArray();
    }
    if (token === '"') {
      return this.parseString().canonical;
    }
    if (token === "t") {
      return this.parseLiteral("true");
    }
    if (token === "f") {
      return this.parseLiteral("false");
    }
    if (token === "n") {
      return this.parseLiteral("null");
    }
    if (token === "-" || (token !== undefined && token >= "0" && token <= "9")) {
      return this.parseNumber();
    }
    throw new Error("unexpected token");
  }

  private parseObject(): string {
    this.position += 1;
    if (this.consume("}")) {
      return "{}";
    }
    const entries: Array<{ key: string; keySource: string; valueSource: string }> = [];
    const keys = new Set<string>();
    while (true) {
      const key = this.parseString();
      if (keys.has(key.value)) {
        throw new Error("duplicate key");
      }
      keys.add(key.value);
      this.expect(":");
      entries.push({
        key: key.value,
        keySource: key.canonical,
        valueSource: this.parseValue(),
      });
      if (this.consume("}")) {
        break;
      }
      this.expect(",");
    }
    entries.sort((left, right) => compareText(left.key, right.key));
    return `{${entries.map((entry) => `${entry.keySource}:${entry.valueSource}`).join(",")}}`;
  }

  private parseArray(): string {
    this.position += 1;
    if (this.consume("]")) {
      return "[]";
    }
    const values: string[] = [];
    while (true) {
      values.push(this.parseValue());
      if (this.consume("]")) {
        break;
      }
      this.expect(",");
    }
    return `[${values.join(",")}]`;
  }

  private parseString(): { value: string; canonical: string } {
    const start = this.position;
    this.expect('"');
    let escaped = false;
    while (this.position < this.source.length) {
      const character = this.source[this.position]!;
      this.position += 1;
      if (escaped) {
        escaped = false;
        continue;
      }
      if (character === "\\") {
        escaped = true;
        continue;
      }
      if (character === '"') {
        const raw = this.source.slice(start, this.position);
        const value = JSON.parse(raw) as unknown;
        if (typeof value !== "string") {
          throw new Error("invalid string");
        }
        return { value, canonical: JSON.stringify(value) };
      }
      if (character.charCodeAt(0) <= 0x1f) {
        throw new Error("unescaped control");
      }
    }
    throw new Error("unterminated string");
  }

  private parseLiteral(literal: "true" | "false" | "null"): string {
    if (!this.source.startsWith(literal, this.position)) {
      throw new Error("invalid literal");
    }
    this.position += literal.length;
    return literal;
  }

  private parseNumber(): string {
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(
      this.source.slice(this.position),
    );
    if (!match) {
      throw new Error("invalid number");
    }
    const raw = match[0];
    this.position += raw.length;
    const canonical = canonicalPythonNumber(raw);
    if (canonical === null || raw !== canonical) {
      throw new Error("non-canonical number");
    }
    return canonical;
  }

  private consume(token: "}" | "]"): boolean {
    if (this.source[this.position] !== token) {
      return false;
    }
    this.position += 1;
    return true;
  }

  private expect(token: '"' | ":" | ","): void {
    if (this.source[this.position] !== token) {
      throw new Error(`expected ${token}`);
    }
    this.position += 1;
  }
}

function canonicalPythonNumber(source: string): string | null {
  const value = Number(source);
  if (!Number.isFinite(value)) {
    return null;
  }
  if (!/[.eE]/.test(source)) {
    return source === "-0" ? "0" : source;
  }
  if (Object.is(value, -0)) {
    return "-0.0";
  }
  if (value === 0) {
    return "0.0";
  }
  const sign = value < 0 ? "-" : "";
  const shortest = Math.abs(value).toString();
  const { digits, exponent } = decimalParts(shortest);
  if (exponent < -4 || exponent >= 16) {
    const mantissa = digits.length === 1 ? digits : `${digits[0]}.${digits.slice(1)}`;
    const exponentSign = exponent < 0 ? "-" : "+";
    const exponentDigits = Math.abs(exponent).toString().padStart(2, "0");
    return `${sign}${mantissa}e${exponentSign}${exponentDigits}`;
  }
  return `${sign}${shortest.includes(".") ? shortest : `${shortest}.0`}`;
}

function decimalParts(shortest: string): { digits: string; exponent: number } {
  const exponentIndex = shortest.indexOf("e");
  if (exponentIndex !== -1) {
    const mantissa = shortest.slice(0, exponentIndex);
    const digits = stripInsignificantZeros(mantissa.replace(".", ""));
    return {
      digits,
      exponent: Number(shortest.slice(exponentIndex + 1)),
    };
  }
  const [integer, decimal = ""] = shortest.split(".") as [string, string?];
  if (integer !== "0") {
    return {
      digits: stripInsignificantZeros(integer + decimal),
      exponent: integer.length - 1,
    };
  }
  const firstNonZero = decimal.search(/[1-9]/);
  if (firstNonZero === -1) {
    throw new Error("zero must be handled before decimalParts");
  }
  return {
    digits: stripInsignificantZeros(decimal.slice(firstNonZero)),
    exponent: -(firstNonZero + 1),
  };
}

function stripInsignificantZeros(value: string): string {
  return value.replace(/^0+/, "").replace(/0+$/, "");
}

function canonicalBytesError(artifactLabel: string): Error {
  return new Error(
    `${artifactLabel} は UTF-8・再帰 key 順・余分な空白や末尾改行なしの canonical JSON bytes である必要があります。`,
  );
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}
