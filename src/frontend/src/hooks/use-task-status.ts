import { StatusEnum, useTasksRetrieve } from "@/features/api/gen";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const MAX_POLL_ERRORS = 10;

export type TaskMetadata = {
  current_message: number;
  total_messages: number | null;
  failure_count: number;
  success_count: number;
  message_status: string;
  type: string;
}

export function useTaskStatus(
  taskId: string | null,
  {
    refetchInterval = 1000,
    enabled = true,
    exhaustedError,
  }: {
    refetchInterval?: number;
    enabled?: boolean;
    // Message shown when polling exhausts its retry budget. Defaults to a
    // generic connection error; callers can override when a task-specific
    // wording is more helpful (e.g. the message importer).
    exhaustedError?: string;
  } = {}
) {
  const { t } = useTranslation();
  const [queryEnabled, setQueryEnabled] = useState(enabled);
  const [hasExhaustedRetries, setHasExhaustedRetries] = useState(false);
  const errorCountRef = useRef(0);

  // Reset per-task polling state when the taskId changes so retries with a
  // new task don't inherit the previous task's exhausted/error state.
  // `enabled` is deliberately omitted from the dependency list: live
  // toggles of `enabled` are handled by the effect below; including
  // it here would cause spurious resets every time the caller flips
  // it. The closure value of `enabled` at the moment a new taskId
  // arrives is exactly the right initial state.
  useEffect(() => {
    errorCountRef.current = 0;
    setHasExhaustedRetries(false);
    setQueryEnabled(enabled);
  }, [taskId]);

  const taskQuery = useTasksRetrieve(taskId || "", {
    query: {
      enabled: Boolean(taskId) && queryEnabled === true,
      refetchInterval,
      meta: {
        noGlobalError: true,
      },
    },
  });

  const taskStatus = taskQuery.data?.data.status;
  const taskMetadata = taskQuery.data?.data.result as TaskMetadata | undefined;

  const hasKnownTotal = taskMetadata?.total_messages != null && taskMetadata.total_messages > 0;
  const currentMessage = taskMetadata?.current_message ?? 0;

  const progress = useMemo(() => {
    if (taskStatus === StatusEnum.SUCCESS) return 100;
    if (taskStatus && taskStatus !== StatusEnum.PROGRESS) return 0;
    if (!hasKnownTotal) return null;
    if (!taskMetadata?.success_count || !taskMetadata.total_messages)
      return null;
    return (taskMetadata.success_count / taskMetadata.total_messages) * 100;
  }, [taskStatus, taskMetadata, hasKnownTotal]);

  useEffect(() => {
    if (taskQuery.isError) {
      errorCountRef.current += 1;
      if (errorCountRef.current >= MAX_POLL_ERRORS) {
        setHasExhaustedRetries(true);
      }
    } else if (taskQuery.data) {
      errorCountRef.current = 0;
    }
  }, [taskQuery.dataUpdatedAt, taskQuery.errorUpdatedAt]);

  useEffect(() => {
    if (!enabled || taskStatus === StatusEnum.FAILURE || taskStatus === StatusEnum.SUCCESS || hasExhaustedRetries) {
      setQueryEnabled(false);
    } else if (enabled || taskStatus === StatusEnum.PROGRESS || taskStatus === StatusEnum.PENDING) {
      setQueryEnabled(true);
    }
  }, [taskStatus, enabled, hasExhaustedRetries]);

  if (!taskId) return null;
  return {
    progress: progress !== null ? Math.ceil(progress) : null,
    state: hasExhaustedRetries ? StatusEnum.FAILURE : taskQuery.data?.data.status,
    loading: taskQuery.isPending || progress === null,
    error: hasExhaustedRetries
      ? (exhaustedError ?? t('Unable to check task status.'))
      : taskQuery.data?.data.error,
    hasKnownTotal,
    currentMessage,
    successCount: taskMetadata?.success_count ?? 0,
    failureCount: taskMetadata?.failure_count ?? 0,
    totalMessages: taskMetadata?.total_messages ?? 0,
  };
}

export type ImportTaskStatus = NonNullable<ReturnType<typeof useTaskStatus>>;

export type ImportTaskRecap = Pick<
  ImportTaskStatus,
  'successCount' | 'failureCount' | 'totalMessages'
>;
