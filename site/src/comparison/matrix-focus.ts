import type { Coordinate } from "./model";

export function coordinateKey(coordinate: Coordinate): string {
  return `${coordinate.rowIndex}:${coordinate.modelId}`;
}

export function focusCoordinate(coordinate: Coordinate): void {
  window.requestAnimationFrame(() => {
    document
      .querySelector<HTMLElement>(
        `[data-matrix-coordinate="${CSS.escape(coordinateKey(coordinate))}"]`,
      )
      ?.focus({ preventScroll: false });
  });
}

export function followSequenceFocus(coordinate: Coordinate): void {
  if (!(document.activeElement instanceof HTMLElement)) {
    return;
  }
  if (document.activeElement.dataset.matrixCoordinate === undefined) {
    return;
  }
  focusCoordinate(coordinate);
}
