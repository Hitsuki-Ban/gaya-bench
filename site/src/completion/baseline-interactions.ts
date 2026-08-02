export function baselineInteractionLocks(options: {
  readonly finalized: boolean;
  readonly submitting: boolean;
  readonly hasError: boolean;
}): {
  readonly mutationLocked: boolean;
  readonly navigationLocked: boolean;
  readonly playbackLocked: boolean;
} {
  return {
    mutationLocked: options.finalized || options.submitting || options.hasError,
    navigationLocked: options.submitting,
    playbackLocked: options.submitting,
  };
}

export function navigateBaselineGroup(options: {
  readonly index: number;
  readonly submitting: boolean;
  readonly stopPlayback: () => void;
  readonly setIndex: (index: number) => void;
}): boolean {
  if (options.submitting) return false;
  options.stopPlayback();
  options.setIndex(options.index);
  return true;
}
