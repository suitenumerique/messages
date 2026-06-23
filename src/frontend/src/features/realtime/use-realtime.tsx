/**
 * RealtimeProvider — wires the single-per-browser SSE client into the app.
 *
 * On a (thin) realtime event it invalidates the mailbox + thread queries so
 * TanStack Query refetches the fresh data. It also exposes the *adaptive poll
 * interval*: slow while the SSE stream is live (it's just a safety net), fast
 * while it's offline (the fallback). Both intervals come from the backend
 * /config endpoint, so they're tunable per-deploy via env vars.
 */
import {
  PropsWithChildren,
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useQueryClient } from "@tanstack/react-query";

import { getMailboxesListQueryKey } from "@/features/api/gen";
import { useConfig } from "@/features/providers/config";
import { RealtimeClient } from "./client";

// SSE stream path. Hardcoded (not configurable): Caddy reverse-proxies it to the
// relay so it's always same-origin.
const EVENTS_PATH = "/realtime-relay/";
// Event names the client listens for (EventSource needs per-name listeners).
const EVENT_NAMES = ["inbox.changed"];
// The mailboxes-LIST key (carries unread metrics). Matched exactly so we don't
// also invalidate per-mailbox sub-resources (channels, templates, calendar,
// image-proxy, …) whose orval keys merely contain "/mailboxes".
const MAILBOXES_LIST_KEY = getMailboxesListQueryKey()[0];
// Polling cadence when realtime is disabled (no SSE stream).
const DEFAULT_POLL_INTERVAL_MS = 30_000;

type RealtimeContextValue = {
  /** Whether the SSE stream is currently connected. */
  live: boolean;
  /** Background poll interval to use right now (ms). */
  pollIntervalMs: number;
};

const RealtimeContext = createContext<RealtimeContextValue>({
  live: false,
  pollIntervalMs: DEFAULT_POLL_INTERVAL_MS,
});

export const RealtimeProvider = ({ children }: PropsWithChildren) => {
  const config = useConfig();
  const queryClient = useQueryClient();
  const [live, setLive] = useState(false);

  const enabled = config.REALTIME_ENABLED;

  useEffect(() => {
    if (!enabled) return;

    const client = new RealtimeClient({
      eventsPath: EVENTS_PATH,
      eventNames: EVENT_NAMES,
    });

    const offStatus = client.onStatus(setLive);
    const offEvent = client.subscribe(() => {
      // Thin event → refetch the lists that carry mailbox metrics / threads.
      // The thread list & stats use custom keys `['threads', …]` (no leading
      // slash) while the mailboxes list uses the orval URL key
      // (`/api/v1.0/mailboxes/`), so match both shapes explicitly.
      queryClient.invalidateQueries({
        predicate: (query) => {
          const key = query.queryKey?.[0];
          if (typeof key !== "string") return false;
          // `['threads', …]` covers the thread list + stats subtrees; the
          // mailboxes list (exact) carries the unread metrics.
          return key === "threads" || key === MAILBOXES_LIST_KEY;
        },
      });
    });

    client.start();
    return () => {
      offStatus();
      offEvent();
      client.stop();
    };
  }, [enabled, queryClient]);

  const value = useMemo<RealtimeContextValue>(
    () => ({
      live,
      // When realtime is disabled there is no SSE stream, so poll on a steady
      // 30s cadence. When enabled: slow while live, fast while offline. The
      // config intervals are in seconds; refetchInterval wants ms.
      pollIntervalMs: !enabled
        ? DEFAULT_POLL_INTERVAL_MS
        : live
          ? config.REALTIME_POLL_INTERVAL_LIVE * 1000
          : config.REALTIME_POLL_INTERVAL_FALLBACK * 1000,
    }),
    [
      enabled,
      live,
      config.REALTIME_POLL_INTERVAL_LIVE,
      config.REALTIME_POLL_INTERVAL_FALLBACK,
    ],
  );

  return (
    <RealtimeContext.Provider value={value}>{children}</RealtimeContext.Provider>
  );
};

export const useRealtime = () => useContext(RealtimeContext);
