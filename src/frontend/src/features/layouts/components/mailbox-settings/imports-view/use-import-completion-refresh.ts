import { useEffect, useRef } from "react";
import { ImportRun } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { isTerminal } from "@/hooks/import-status";

/**
 * Refresh the mailbox views once an import run settles.
 *
 * A run delivers its messages from a Celery worker, so nothing on the wire tells
 * React Query that the thread list, its stats and the labels went stale —
 * without this, imported mail only surfaces on a manual reload. Callers pass the
 * runs they already poll; the first snapshot is only a baseline (pre-existing
 * runs must not trigger anything), and from then on any run reaching a terminal
 * status invalidates. A small archive can be created *and* finish between two
 * polls, so a run appearing already terminal counts as a transition too.
 */
export const useImportCompletionRefresh = (runs: ImportRun[] | undefined) => {
  const {
    refetchMailboxes,
    invalidateMailbox,
    invalidateThreadsStats,
    invalidateLabels,
  } = useMailboxContext();
  const previousStatuses = useRef<
    Map<string, string | null | undefined> | undefined
  >(undefined);
  // Depend on the statuses rather than the array: the polls that change nothing
  // must not re-run the effect.
  const statuses = (runs ?? [])
    .map((run) => `${run.id}:${run.status}`)
    .join(",");

  useEffect(() => {
    if (!runs) return;
    const previous = previousStatuses.current;
    previousStatuses.current = new Map(runs.map((run) => [run.id, run.status]));
    if (!previous) return;
    const settled = runs.some(
      (run) => isTerminal(run.status) && previous.get(run.id) !== run.status,
    );
    if (!settled) return;
    refetchMailboxes();
    invalidateMailbox();
    invalidateThreadsStats();
    invalidateLabels();
  }, [statuses]);
};
