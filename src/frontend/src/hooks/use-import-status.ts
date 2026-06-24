import { MessageImport, StatusEnum, useImportsRetrieve } from "@/features/api/gen";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const MAX_POLL_ERRORS = 10;

// Backend import statuses (Channel.settings["import"]["status"]).
const STATUS_COMPLETED = "completed";
const STATUS_FAILED = "failed";
const STATUS_CANCELLED = "cancelled";

const isTerminal = (status: string | null | undefined) =>
  status === STATUS_COMPLETED ||
  status === STATUS_FAILED ||
  status === STATUS_CANCELLED;

/**
 * Map the backend import status onto the Celery-style {@link StatusEnum} the
 * importer UI already understands, so consumers keep a single status vocabulary:
 * - completed                 -> SUCCESS
 * - failed / cancelled        -> FAILURE
 * - pending/indexing/running  -> PROGRESS
 */
const toState = (status: string | null | undefined): StatusEnum => {
  if (status === STATUS_COMPLETED) return StatusEnum.SUCCESS;
  if (status === STATUS_FAILED || status === STATUS_CANCELLED)
    return StatusEnum.FAILURE;
  return StatusEnum.PROGRESS;
};

/**
 * Poll the import resource (`GET /imports/{id}/`) and expose the same shape as
 * {@link useTaskStatus}, so the importer UI can swap the Celery-task polling for
 * the durable, cancellable import run with no downstream changes.
 *
 * Polling stops on a terminal status or after MAX_POLL_ERRORS consecutive
 * errors (e.g. a stale id left in local storage), surfaced as FAILURE.
 */
export function useImportStatus(
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

  const query = useImportsRetrieve(importId || "", {
    query: {
      enabled: Boolean(importId) && queryEnabled === true,
      refetchInterval,
      meta: {
        noGlobalError: true,
      },
    },
  });

  const data = query.data?.data as MessageImport | undefined;
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

  const state = hasExhaustedRetries ? StatusEnum.FAILURE : toState(status);
  // Show an indeterminate bar (null) while the run is still indexing (no total
  // yet); a known total drives the resource-computed percentage.
  const progress =
    state === StatusEnum.SUCCESS
      ? 100
      : hasKnownTotal
        ? Math.ceil(data?.progress ?? 0)
        : null;

  return {
    progress,
    state,
    loading: query.isPending || (state === StatusEnum.PROGRESS && progress === null),
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
