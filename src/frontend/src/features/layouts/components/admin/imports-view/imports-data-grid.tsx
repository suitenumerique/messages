import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { keepPreviousData, useQueryClient } from "@tanstack/react-query";
import { Column, usePagination } from "@gouvfr-lasuite/ui-components";
import {
    AdminImportRun,
    MailDomainAdmin,
    MaildomainsImportsListStatus,
    getMailboxesImportsListQueryKey,
    getMaildomainsImportsListQueryKey,
    useMaildomainsImportsList,
} from "@/features/api/gen";
import { ImportsDataGrid } from "@/features/layouts/components/mailbox-settings/imports-view/imports-data-grid";
import { importsRefetchInterval } from "@/hooks/import-status";
import { useJustCompletedRuns } from "./use-just-completed-runs";

type AdminImportsDataGridProps = {
    domain: MailDomainAdmin;
    pagination: ReturnType<typeof usePagination>;
    searchQuery: string;
    showAll: boolean;
};

/** The imports of every mailbox of a domain, kept live while one progresses. */
export const AdminImportsDataGrid = ({ domain, pagination, searchQuery, showAll }: AdminImportsDataGridProps) => {
    const { t } = useTranslation();
    const queryClient = useQueryClient();
    const trimmedQuery = searchQuery.trim();
    const { data, isLoading, isPlaceholderData, error } = useMaildomainsImportsList(
        domain.id,
        {
            page: pagination.page,
            status: showAll ? MaildomainsImportsListStatus.all : MaildomainsImportsListStatus.active,
            ...(trimmedQuery ? { q: trimmedQuery } : {}),
        },
        {
            query: {
                placeholderData: keepPreviousData,
                refetchInterval: (query) => importsRefetchInterval(query.state.data?.data.results),
            },
            // Background status poll: let foreground requests win the wire.
            request: { priority: "low" },
        },
    );
    const rows = data?.data.results;
    const { completedRuns, dismiss } = useJustCompletedRuns(
        isPlaceholderData ? undefined : rows,
        [domain.id, pagination.page, showAll, trimmedQuery].join("|"),
        !showAll,
    );
    const listedIds = new Set((rows ?? []).map((row) => row.id));
    const displayedRows = [
        ...completedRuns.filter((run) => !listedIds.has(run.id)),
        ...(rows ?? []),
    ];

    // Any action outdates the final state kept for a just-completed run: the
    // refreshed list tells where the run stands now.
    const handleRowChange = (row: AdminImportRun) => {
        dismiss(row.id);
        return Promise.all([
            queryClient.invalidateQueries({
                queryKey: getMaildomainsImportsListQueryKey(domain.id),
                exact: false,
            }),
            row.mailbox && queryClient.invalidateQueries({
                queryKey: getMailboxesImportsListQueryKey(row.mailbox),
                exact: false,
            }),
        ]);
    };

    const leadingColumns: Column<AdminImportRun>[] = [
        {
            id: "mailbox",
            headerName: t("Mailbox"),
            renderCell: ({ row }) => (
                <div className="admin-imports__mailbox">
                    <strong>{row.mailbox_details.email}</strong>
                    {row.created_by && (
                        <span className="admin-imports__launcher">
                            {t("Started by {{name}}", {
                                name: row.created_by.full_name || row.created_by.email,
                            })}
                        </span>
                    )}
                </div>
            ),
        },
    ];

    useEffect(() => {
        if (data?.data.count !== undefined) {
            pagination.setPagesCount(
                Math.max(1, Math.ceil(data.data.count / pagination.pageSize)),
            );
        }
    }, [data?.data.count, pagination.pageSize, pagination.setPagesCount]);

    return (
        <ImportsDataGrid
            rows={displayedRows}
            isLoading={isLoading}
            isError={!!error}
            onRowChange={handleRowChange}
            leadingColumns={leadingColumns}
            pagination={pagination}
            emptyPlaceholderLabel={
                showAll ? t("No imports yet") : t("No import in progress or failed")
            }
        />
    );
};
