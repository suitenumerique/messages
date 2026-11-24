// Backend import statuses (Channel.settings["import"]["status"] / Redis).
export const STATUS_PENDING = "pending";
export const STATUS_RUNNING = "running";
export const STATUS_COMPLETED = "completed";
export const STATUS_FAILED = "failed";
export const STATUS_CANCELLED = "cancelled";

/** How often an imports list refreshes while one of its runs progresses. */
export const IMPORTS_POLL_INTERVAL = 15_000;

/** True once a run can no longer make progress (the one terminal-status list —
 * shared with the imports settings grid and the header progress indicator). */
export const isTerminal = (status: string | null | undefined) =>
  status === STATUS_COMPLETED ||
  status === STATUS_FAILED ||
  status === STATUS_CANCELLED;

/** True while a run is working through its source. A continuous run between
 * two polls reads "completed", so it does not count. */
export const isProgressing = (status: string | null | undefined) =>
  status === STATUS_PENDING || status === STATUS_RUNNING;

/** `refetchInterval` of an imports list: poll while one of its runs
 * progresses, stop once they all wait or are over. */
export const importsRefetchInterval = (
  runs: ReadonlyArray<{ status: string | null }> | undefined,
) => (runs?.some((run) => isProgressing(run.status)) ? IMPORTS_POLL_INTERVAL : false);
