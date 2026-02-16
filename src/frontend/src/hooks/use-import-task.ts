import { StatusEnum, useTasksRetrieve } from "@/features/api/gen";
import { TaskMetadata } from "@/features/controlled-modals/message-importer/step-loader";
import { useEffect, useMemo, useState } from "react";

export function useImportTaskStatus(
  taskId: string | null,
  {
    refetchInterval = 1000,
    enabled = true,
  }: { refetchInterval?: number; enabled?: boolean } = {}
) {
  const [queryEnabled, setQueryEnabled] = useState(enabled);
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
    if (!enabled || taskStatus === StatusEnum.FAILURE || taskStatus === StatusEnum.SUCCESS) {
      setQueryEnabled(false);
    } else if (enabled || taskStatus === StatusEnum.PROGRESS || taskStatus === StatusEnum.PENDING) {
      setQueryEnabled(true);
    }
  }, [taskStatus, enabled]);

  if (!taskId) return null;
  return {
    progress: progress !== null ? Math.ceil(progress) : null,
    state: taskQuery.data?.data.status,
    loading: taskQuery.isPending || progress === null,
    error: taskQuery.data?.data.error,
    hasKnownTotal,
    currentMessage,
  };
}
