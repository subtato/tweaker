import { useCallback, useEffect, useRef, useState } from "react";

export function useAsync<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
  options: { immediate?: boolean } = {},
) {
  const { immediate = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(immediate);
  const requestId = useRef(0);

  const run = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const value = await loader();
      if (id === requestId.current) setData(value);
      return value;
    } catch (err) {
      if (id === requestId.current) setError(err instanceof Error ? err : new Error(String(err)));
      throw err;
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, deps);

  useEffect(() => {
    if (immediate) run().catch(() => undefined);
  }, [run, immediate]);

  return { data, error, loading, refresh: run, setData };
}

export function usePolling(callback: () => void, active: boolean, interval = 1800) {
  const saved = useRef(callback);
  saved.current = callback;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => saved.current(), interval);
    return () => window.clearInterval(timer);
  }, [active, interval]);
}

export function useDocumentTitle(title: string) {
  useEffect(() => {
    document.title = title ? `${title} · Tweaker` : "Tweaker · Seer for self-hosted Sentry";
  }, [title]);
}

export function useCopy() {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = useCallback(async (value: string, key = "default") => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(key);
      window.setTimeout(() => setCopied(null), 1600);
    } catch {
      setCopied(null);
    }
  }, []);
  return { copied, copy };
}
