import { useEffect, type RefObject } from "react";

const MAX_CONCURRENT_PREFETCHES = 4;
const completedUrls = new Set<string>();
const queuedUrls = new Set<string>();
const queue: string[] = [];
let activePrefetches = 0;

function drainQueue(): void {
  while (activePrefetches < MAX_CONCURRENT_PREFETCHES) {
    const url = queue.shift();
    if (url === undefined) {
      return;
    }

    queuedUrls.delete(url);
    activePrefetches += 1;
    void fetch(url, { cache: "force-cache", credentials: "omit" })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`音声 prefetch に失敗しました: HTTP ${response.status} ${url}`);
        }
        completedUrls.add(url);
      })
      .catch((error: unknown) => {
        console.warn("音声 prefetch に失敗しました。", error);
      })
      .finally(() => {
        activePrefetches -= 1;
        drainQueue();
      });
  }
}

function enqueuePrefetch(url: string): void {
  if (completedUrls.has(url) || queuedUrls.has(url)) {
    return;
  }

  queuedUrls.add(url);
  queue.push(url);
  drainQueue();
}

export function useVisibleAudioPrefetch(
  scopeRef: RefObject<HTMLElement | null>,
  scrollRootRef: RefObject<HTMLElement | null> | null,
  revision: string,
): void {
  useEffect(() => {
    const scope = scopeRef.current;
    if (scope === null) {
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) {
            continue;
          }
          const url = (entry.target as HTMLElement).dataset.audioUrl;
          if (url === undefined || url.length === 0) {
            throw new Error("prefetch 対象に data-audio-url がありません。");
          }
          enqueuePrefetch(url);
          observer.unobserve(entry.target);
        }
      },
      {
        root: scrollRootRef?.current ?? null,
        rootMargin: "320px 0px",
      },
    );

    for (const element of scope.querySelectorAll<HTMLElement>("[data-audio-url]")) {
      observer.observe(element);
    }

    return () => observer.disconnect();
  }, [revision, scopeRef, scrollRootRef]);
}
