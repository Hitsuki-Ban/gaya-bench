const SCHEME = /^[a-z][a-z\d+.-]*:/i;

export function isSafeClipPath(value: string): boolean {
  return (
    value.length > 0 &&
    !value.startsWith("/") &&
    !value.includes("\\") &&
    !value.includes("%") &&
    !value.includes("#") &&
    !value.includes("?") &&
    !SCHEME.test(value) &&
    value.split("/").every((segment) => segment !== "" && segment !== "." && segment !== "..")
  );
}
