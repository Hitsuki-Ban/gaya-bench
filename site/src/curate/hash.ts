const HEX = "0123456789abcdef";

export async function sha256Hex(bytes: BufferSource): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  let result = "";
  for (const byte of digest) {
    result += HEX[byte >>> 4] + HEX[byte & 0x0f];
  }
  return result;
}

export async function sha256Text(value: string): Promise<string> {
  return sha256Hex(new TextEncoder().encode(value));
}
