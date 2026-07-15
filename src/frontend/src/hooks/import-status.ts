// Backend import statuses (Channel.settings["import"]["status"] / Redis).
export const STATUS_COMPLETED = "completed";
export const STATUS_FAILED = "failed";
export const STATUS_CANCELLED = "cancelled";

/** True once a run can no longer make progress (the one terminal-status list —
 * shared with the imports settings grid and the header progress indicator). */
export const isTerminal = (status: string | null | undefined) =>
  status === STATUS_COMPLETED ||
  status === STATUS_FAILED ||
  status === STATUS_CANCELLED;
