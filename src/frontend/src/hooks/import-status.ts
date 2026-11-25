import { MaildomainsImportsListStatusItem as ListStatus } from "@/features/api/gen/models/maildomains_imports_list_status_item";

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

/** The statuses a domain's imports are filtered by, in display order. */
export const IMPORT_LIST_STATUSES: ListStatus[] = [
  ListStatus.in_progress,
  ListStatus.failed,
  ListStatus.completed,
];

/** What a domain admin still has to watch. */
export const DEFAULT_IMPORT_LIST_STATUSES: ListStatus[] = [
  ListStatus.in_progress,
  ListStatus.failed,
];

/** The list statuses held by a comma-separated URL value, in display order;
 * the default ones when it holds none. */
export const parseImportListStatuses = (value: unknown): ListStatus[] => {
  const picked = typeof value === "string" ? value.split(",") : [];
  const statuses = IMPORT_LIST_STATUSES.filter((status) => picked.includes(status));
  return statuses.length > 0 ? statuses : DEFAULT_IMPORT_LIST_STATUSES;
};

/** The list status of a run, as the domain imports list files it. */
export const importListStatusOf = (run: { status: string | null; is_active: boolean }) => {
  if (run.status === STATUS_FAILED) return ListStatus.failed;
  return run.is_active ? ListStatus.in_progress : ListStatus.completed;
};
