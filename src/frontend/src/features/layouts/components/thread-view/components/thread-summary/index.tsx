import { useState, useMemo } from "react";
import { Button, Tooltip } from "@openfun/cunningham-react";
import { Icon, Spinner } from "@gouvfr-lasuite/ui-kit";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import ReactMarkdown from "react-markdown";
import { useThreadsRefreshSummaryCreate } from "@/features/api/gen";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

interface ThreadSummaryProps {
  threadId: string;
  summary: string;
  selectedMailboxId?: string;
  searchParams?: URLSearchParams;
  selectedThread?: { id: string };
  onSummaryUpdated?: (newSummary: string) => void;
}

export const ThreadSummary = ({
  threadId,
  summary,
  selectedMailboxId,
  searchParams,
  selectedThread,
  onSummaryUpdated,
}: ThreadSummaryProps) => {
  const { t } = useTranslation();
  const [localSummary, setLocalSummary] = useState(summary);

  // Build the cache key for the thread 
  const threadQueryKey = useMemo(() => {
    if (!selectedMailboxId || !searchParams) return ["threads"];
    const queryKey = ["threads", selectedMailboxId];
    if (searchParams.get("search")) {
      return [...queryKey, "search"];
    }
    return [...queryKey, searchParams.toString()];
  }, [selectedMailboxId, searchParams]);

  const queryClient = useQueryClient();
  /**
   * Cache the new summary in the thread query data.
   * This is used to update the thread summary in the thread list
   * when the summary is updated.
   */
  const cacheNewSummary = (newSummary: string) => {
    queryClient.setQueryData(
      threadQueryKey,
      (
        oldData:
          | {
              pages: Array<{
                data: {
                  results: { id: string; summary?: string }[];
                  count: number;
                  next: string | null;
                  previous: string | null;
                };
              }>;
            }
          | undefined
      ) => {
        if (!oldData) return oldData;
        return {
          ...oldData,
          pages: oldData.pages.map((page) => ({
            ...page,
            data: {
              ...page.data,
              results: page.data.results.map((thread) =>
                thread.id === selectedThread?.id
                  ? { ...thread, summary: newSummary }
                  : thread
              ),
            },
          })),
        };
      }
    );
  };

  const refreshMutation = useThreadsRefreshSummaryCreate({
    mutation: {
      onMutate: () => {
        addToast(
          <ToasterItem type="info">
            {t("Generating summary...")}
          </ToasterItem>
        );
      },
      onSuccess: (data) => {
        if (data.status === 200 && 'summary' in data.data) {
          const newSummary = data.data.summary;
          if (newSummary) {
            setLocalSummary(newSummary);
            cacheNewSummary(newSummary);
            onSummaryUpdated?.(newSummary);
            addToast(<ToasterItem type="info">{t("Summary refreshed!")}</ToasterItem>);
          }
        } else {
          addToast(<ToasterItem type="error">{t("Failed to refresh summary.")}</ToasterItem>);
        }
      },
      onError: () => {
        addToast(
          <ToasterItem type="error">
            {t("Failed to refresh summary.")}
          </ToasterItem>
        );
      },
    },
  });

  const handleRefresh = () => {
    refreshMutation.mutate({ id: threadId });
  };

  return (
    <div className="thread-summary__container">
      {refreshMutation.isPending ? (
        <div className="thread-summary__content">
          <Spinner />
        </div>
      ) : (
        <>
          <div className="thread-summary__content">
            {localSummary ? (
              <ReactMarkdown>{`**${t("Summary")} :** ${localSummary}`}</ReactMarkdown>
            ) : (
              <p>{t("No summary available.")}</p>
            )}
          </div>
          <div className="thread-summary__refresh-button">
            <Tooltip content={t("Refresh summary")}>
              <Button
                color="tertiary-text"
                size="small"
                icon={<Icon name="autorenew"/>}
                aria-label={t("Refresh summary")}
                onClick={handleRefresh}
              />
            </Tooltip>
          </div>
        </>
      )}
    </div>
  );
};