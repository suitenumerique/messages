import { ImportRun, useMailboxesImportsRetrieve } from "@/features/api/gen";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const MAX_POLL_ERRORS = 10;

// Backend import statuses (Channel.settings["import"]["status"] / Redis).
const STATUS_COMPLETED = "completed";
const STATUS_FAILED = "failed";
const STATUS_CANCELLED = "cancelled";

/**
 * The state the importer UI reacts to. A deliberate cancel is its own state,
 * distinct from a failure, so the modal shows the cancelled flow (not an error
 * with a "retry" hint).
 */
export type ImportState = "progress" | "success" | "failed" | "cancelled";

const isTerminal = (status: string | null | undefined) =>
  status === STATUS_COMPLETED ||
  status === STATUS_FAILED ||
  status === STATUS_CANCELLED;

/**
 * Poll the import resource (`GET /mailboxes/{id}/imports/{id}/`) and expose its
 * live state + progress for the importer modal. Polling stops on a terminal
 * status or after MAX_POLL_ERRORS consecutive errors (e.g. a stale id left in
 * local storage), which surface as "failed".
 */
export function useImportStatus(
  mailboxId: string,
  importId: string | null,
  {
    refetchInterval = 1000,
    enabled = true,
    exhaustedError,
  }: {
    refetchInterval?: number;
    enabled?: boolean;
    exhaustedError?: string;
  } = {}
) {
  const { t } = useTranslation();
  const [queryEnabled, setQueryEnabled] = useState(enabled);
  const [hasExhaustedRetries, setHasExhaustedRetries] = useState(false);
  const errorCountRef = useRef(0);

  // Reset per-import polling state when the id changes so a retry doesn't
  // inherit the previous run's exhausted/error state.
  useEffect(() => {
    errorCountRef.current = 0;
    setHasExhaustedRetries(false);
    setQueryEnabled(enabled);
  }, [importId]);

  const query = useMailboxesImportsRetrieve(mailboxId, importId || "", {
    query: {
      enabled: Boolean(mailboxId) && Boolean(importId) && queryEnabled === true,
      refetchInterval,
      meta: {
        noGlobalError: true,
      },
    },
  });

  const data = query.data?.data as ImportRun | undefined;
  const status = data?.status;
  const totalMessages = data?.total_messages ?? 0;
  const hasKnownTotal = totalMessages > 0;
  const successCount = data?.success_count ?? 0;
  const failureCount = data?.failure_count ?? 0;
  const currentMessage = successCount + failureCount;

  useEffect(() => {
    if (query.isError) {
      errorCountRef.current += 1;
      if (errorCountRef.current >= MAX_POLL_ERRORS) {
        setHasExhaustedRetries(true);
      }
    } else if (query.data) {
      errorCountRef.current = 0;
    }
  }, [query.dataUpdatedAt, query.errorUpdatedAt]);

  useEffect(() => {
    if (!enabled || isTerminal(status) || hasExhaustedRetries) {
      setQueryEnabled(false);
    } else {
      setQueryEnabled(true);
    }
  }, [status, enabled, hasExhaustedRetries]);

  if (!importId) return null;

  let state: ImportState = "progress";
  if (hasExhaustedRetries || status === STATUS_FAILED) state = "failed";
  else if (status === STATUS_COMPLETED) state = "success";
  else if (status === STATUS_CANCELLED) state = "cancelled";

  // Show an indeterminate bar (null) while the run is still indexing (no total
  // yet); a known total drives the resource-computed percentage.
  const progress =
    state === "success"
      ? 100
      : hasKnownTotal
        ? Math.ceil(data?.progress ?? 0)
        : null;

  return {
    progress,
    state,
    loading: query.isPending || (state === "progress" && progress === null),
    error: hasExhaustedRetries
      ? (exhaustedError ?? t("Unable to check task status."))
      : (data?.error ?? null),
    hasKnownTotal,
    currentMessage,
    successCount,
    failureCount,
    totalMessages,
  };
}

export type ImportStatusResult = NonNullable<ReturnType<typeof useImportStatus>>;

/** The final counts handed to the "import complete" screen. */
export type ImportRecap = Pick<
  ImportStatusResult,
  "successCount" | "failureCount" | "totalMessages"
>;
