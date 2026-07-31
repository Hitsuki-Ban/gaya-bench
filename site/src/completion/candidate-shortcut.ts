export function candidateKeyboardShortcut(candidateIndex: number): string | null {
  return Number.isInteger(candidateIndex) && candidateIndex >= 0 && candidateIndex < 9
    ? String(candidateIndex + 1)
    : null;
}
