import { DropdownMenu, DropdownMenuOption, IconSize, Spinner, usePagination } from "@gouvfr-lasuite/ui-components";
import { Button, Column, DataGrid, useModals } from "@gouvfr-lasuite/ui-components";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import { Fragment, ReactNode, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
    ImportRun,
    useMailboxesImportsList,
    useMailboxesImportsCancelCreate,
    useMailboxesImportsDestroy,
    useMailboxesImportsPartialUpdate,
    getMailboxesImportsListQueryKey,
} from "@/features/api/gen";
import { Badge } from "@/features/ui/components/badge";
import { Banner } from "@/features/ui/components/banner";
import ProgressBar from "@/features/ui/components/progress-bar";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { handle } from "@/features/utils/errors";
import { DateHelper } from "@/features/utils/date-helper";
import { importsRefetchInterval, isTerminal, STATUS_CANCELLED } from "@/hooks/import-status";
import { Icon } from "@/features/ui/components/icon";
import { Pause, Play, Trash } from "@gouvfr-lasuite/ui-components/icons";

type ImportsDataGridProps<TRow extends ImportRun> = {
    rows: TRow[];
    isLoading: boolean;
    isError: boolean;
    /** Refreshes the lists showing `row` once an action on it has settled. */
    onRowChange: (row: TRow) => Promise<unknown>;
    /** Columns shown before the import type, e.g. the mailbox of each run. */
    leadingColumns?: Column<TRow>[];
    emptyPlaceholderLabel?: string;
    pagination?: ReturnType<typeof usePagination>;
};

const isRunning = (row: ImportRun) => !isTerminal(row.status);
const isImap = (row: ImportRun) => row.source_type === "imap";
const isContinuous = (row: ImportRun) => row.mode === "continuous";
// poll_interval is in seconds (global operator setting)
const pollMinutes = (row: ImportRun) => Math.max(1, Math.round((row.poll_interval ?? 900) / 60));

// A settled run whose channel row can be deleted without a live worker or
// poller losing it mid-run (the backend enforces the same rule).
const canForget = (row: ImportRun) => isTerminal(row.status) && row.is_active === false;

// Human-readable label for each backend failure category (see the importer's
// `deliver()`), so the "N failed" tooltip explains *why* messages were skipped.
const failureReasonLabel = (
    reason: string,
    t: ReturnType<typeof useTranslation>["t"],
): string => {
    switch (reason) {
        case "oversized":
            return t("Too large (over the size limit)");
        case "unparseable":
            return t("Unreadable or malformed");
        case "undeliverable":
            return t("Could not be delivered");
        default:
            return t("Unexpected error");
    }
};

// A whole-run failure carries a backend error string. Known, machine-tagged
// causes get a friendly translated message; anything else is surfaced verbatim.
const importFailureMessage = (
    error: string | null | undefined,
    t: ReturnType<typeof useTranslation>["t"],
): string | undefined => {
    if (!error) return undefined;
    if (error.includes("PST_UNREADABLE")) {
        return t("The PST archive is unreadable: the file is corrupt or its internal structure is incomplete. Retrying will not help — please try to re-generate the archive.");
    }
    if (error.includes("AUTHENTICATIONFAILED")) {
        return t("Authentication failed. Please check your credentials and ensure you have enabled IMAP connections in your account.");
    }
    return error;
};

type RowActionsProps<TRow extends ImportRun> = {
    row: TRow;
    pending: boolean;
    // Open state lives in the parent (keyed by row id): the grid re-renders
    // on every poll while a run is in progress, and menu state local to the row
    // would be reset — closing the menu in the user's hand.
    isOpen: boolean;
    onOpenChange: (open: boolean) => void;
    onPause: (row: TRow) => void;
    onResume: (row: TRow) => void;
    onMakeRecurring: (row: TRow) => void;
    onForget: (row: TRow) => void;
    onCancel: (row: TRow) => void;
};

/** The per-row [...] menu: every action spelled out, nothing icon-only. */
const RowActions = <TRow extends ImportRun>({ row, pending, isOpen, onOpenChange, onPause, onResume, onMakeRecurring, onForget, onCancel }: RowActionsProps<TRow>) => {
    const { t } = useTranslation();

    const options: DropdownMenuOption[] = [];
    if (isContinuous(row)) {
        if (row.is_active !== false) {
            options.push({
                label: t("Pause polling"),
                icon: <Icon icon={Pause} size={IconSize.SMALL} />,
                callback: () => onPause(row),
            });
        } else {
            options.push({
                label: t("Resume polling"),
                icon: <Icon icon={Play} size={IconSize.SMALL} />,
                callback: () => onResume(row),
            });
        }
    } else if (isImap(row) && isTerminal(row.status)) {
        options.push({
            label: t("Check for new mail regularly"),
            icon: <Icon name="autorenew" />,
            callback: () => onMakeRecurring(row),
        });
    }
    if (canForget(row)) {
        options.push({
            label: t("Remove from list (keep messages)"),
            icon: <Icon name="playlist_remove" />,
            callback: () => onForget(row),
        });
    }
    if (options.length > 0) {
        options[options.length - 1].showSeparator = true;
    }
    options.push({
        label: isRunning(row)
            ? t("Cancel import and delete its messages")
            : t("Delete imported messages"),
        icon: <Icon icon={Trash} size={IconSize.SMALL} />,
        callback: () => onCancel(row),
        variant: "danger",
    });

    return (
        <DropdownMenu isOpen={isOpen} onOpenChange={onOpenChange} options={options}>
            <Button
                onClick={() => onOpenChange(true)}
                icon={pending ? <Spinner size="sm" /> : <Icon name="more_horiz" />}
                variant="tertiary"
                size="nano"
                disabled={pending}
                aria-label={t("Import actions")}
            />
        </DropdownMenu>
    );
};

/**
 * The imports of one or many mailboxes, with the actions on each run. Rows
 * come from the caller; every action goes through the run's own mailbox route,
 * which domain admins pass too.
 */
export const ImportsDataGrid = <TRow extends ImportRun>({
    rows,
    isLoading,
    isError,
    onRowChange,
    leadingColumns = [],
    emptyPlaceholderLabel,
    pagination,
}: ImportsDataGridProps<TRow>) => {
    const { t, i18n } = useTranslation();
    const modals = useModals();
    const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());
    const [openMenuId, setOpenMenuId] = useState<string | null>(null);

    const cancelMutation = useMailboxesImportsCancelCreate();
    const updateMutation = useMailboxesImportsPartialUpdate();
    const forgetMutation = useMailboxesImportsDestroy();

    // A cancelled run is removed server-side once its purge settles; filter
    // out any row still (or left) in the cancelled state so cancelling makes
    // the import disappear from the list immediately.
    const visibleRows = rows.filter((row) => row.status !== STATUS_CANCELLED);

    const withPending = async (row: TRow, fn: (mailboxId: string) => Promise<unknown>, successMessage?: string) => {
        const { id, mailbox: mailboxId } = row;
        if (!mailboxId) return;
        setPendingIds((prev) => new Set(prev).add(id));
        try {
            await fn(mailboxId);
            await onRowChange(row);
            if (successMessage) {
                addToast(
                    <ToasterItem type="info">
                        <span>{successMessage}</span>
                    </ToasterItem>,
                );
            }
        } catch (err) {
            handle(err);
            addToast(
                <ToasterItem type="error">
                    <span>{t("Failed to update import.")}</span>
                </ToasterItem>,
            );
        } finally {
            setPendingIds((prev) => {
                const next = new Set(prev);
                next.delete(id);
                return next;
            });
        }
    };

    const handlePause = (row: TRow) =>
        withPending(
            row,
            (mailboxId) => updateMutation.mutateAsync({ mailboxId, id: row.id, data: { is_active: false } }),
            t("Polling paused."),
        );

    const handleResume = (row: TRow) =>
        withPending(
            row,
            (mailboxId) => updateMutation.mutateAsync({ mailboxId, id: row.id, data: { is_active: true } }),
            t("Polling resumed."),
        );

    const handleMakeRecurring = (row: TRow) =>
        withPending(
            row,
            (mailboxId) => updateMutation.mutateAsync({ mailboxId, id: row.id, data: { mode: "continuous" } }),
            t("This account will now be checked for new mail regularly."),
        );

    const handleForget = (row: TRow) =>
        withPending(
            row,
            (mailboxId) => forgetMutation.mutateAsync({ mailboxId, id: row.id }),
            t("Import removed from the list. Its messages were kept."),
        );

    const handleCancel = async (row: TRow) => {
        const decision = await modals.deleteConfirmationModal({
            title: t('Delete the messages of "{{name}}"', { name: row.name }),
            children: t("This deletes every message this import created, except those in conversations with replies or other activity. This action is irreversible!"),
        });
        if (decision !== "delete") return;
        await withPending(
            row,
            (mailboxId) => cancelMutation.mutateAsync({ mailboxId, id: row.id }),
            t("Import cancelled and messages deleted."),
        );
    };

    /** One compact line: status, then date, then account/schedule context. */
    const renderStatusLine = (row: ImportRun) => {
        if (isRunning(row)) {
            const hasTotal = (row.total_messages ?? 0) > 0;
            return (
                <div className="import-status import-status--running">
                    <ProgressBar progress={hasTotal ? Math.ceil(row.progress ?? 0) : null} />
                    <span>
                        {hasTotal
                            ? t("{{progress}}% imported", { progress: Math.ceil(row.progress ?? 0) })
                            : t("Starting…")}
                    </span>
                </div>
            );
        }

        const parts: ReactNode[] = [];
        if (row.status === "failed") {
            parts.push(
                <span key="status" className="import-status import-status--error" title={importFailureMessage(row.error, t)}>
                    {t("Failed")}
                </span>,
            );
        } else if (row.status === "cancelled") {
            parts.push(
                <span key="status" className="import-status import-status--muted">{t("Cancelled")}</span>,
            );
        } else if (isContinuous(row)) {
            parts.push(
                <span key="status" className={`import-status import-status--${row.is_active !== false ? "success" : "muted"}`}>
                    {row.is_active !== false
                        ? t("Checking every {{count}} min", { count: pollMinutes(row) })
                        : t("Polling paused")}
                </span>,
            );
        } else {
            parts.push(
                <span key="status" className="import-status import-status--success">
                    {t("{{count}} messages imported", { count: row.success_count ?? 0 })}
                </span>,
            );
            if ((row.failure_count ?? 0) > 0) {
                const failedLabel = t("{{count}} failed", { count: row.failure_count ?? 0 });
                const reasons = row.failure_reasons;
                // Breakdown as a native title (one "reason: count" per line) — a
                // hover tooltip that stays reliable across the grid's frequent
                // (polling) re-renders.
                const breakdown =
                    reasons && Object.keys(reasons).length > 0
                        ? Object.entries(reasons)
                              .map(([reason, count]) => `${failureReasonLabel(reason, t)}: ${count}`)
                              .join("\n")
                        : undefined;
                parts.push(
                    <span
                        key="failures"
                        className={clsx("import-status import-status--error", {
                            "import-status--has-tooltip": breakdown,
                        })}
                        title={breakdown}
                    >
                        {failedLabel}
                    </span>,
                );
            }
        }

        // The backend keeps only the first names; the count is the real total.
        const skippedFolders = row.skipped_folders ?? [];
        const skippedCount = Math.max(row.skipped_folders_count ?? 0, skippedFolders.length);
        if (skippedCount > 0) {
            const unlistedCount = skippedCount - skippedFolders.length;
            const tooltipLines = [t("Folders that could not be opened and were not imported:"), ...skippedFolders];
            if (unlistedCount > 0) {
                tooltipLines.push(t("…and {{count}} more", { count: unlistedCount }));
            }
            parts.push(
                <span
                    key="skipped-folders"
                    className="import-status import-status--muted import-status--has-tooltip"
                    title={tooltipLines.join("\n")}
                >
                    {t("{{count}} folders skipped", { count: skippedCount })}
                </span>,
            );
        }

        const date = row.finished_at ?? row.started_at ?? row.created_at;
        if (date) {
            parts.push(
                <span key="date" className="import-status import-status--muted">
                    {DateHelper.formatEventTimestamp(date, i18n.language)}
                </span>,
            );
        }
        if (row.imap_username) {
            parts.push(
                <span key="account" className="import-status import-status--muted">{row.imap_username}</span>,
            );
        }

        return (
            <div className="import-status-line">
                {parts.map((part, index) => (
                    <Fragment key={index}>
                        {index > 0 && <span className="import-status-line__separator" aria-hidden>·</span>}
                        {part}
                    </Fragment>
                ))}
            </div>
        );
    };

    const columns: Column<TRow>[] = [
        ...leadingColumns,
        {
            id: "type",
            headerName: t("Type"),
            size: 110,
            renderCell: ({ row }) => (
                <div className="import-type">
                    <span>{(row.source_type ?? "").toUpperCase()}</span>
                    {isContinuous(row) && (
                        <Badge
                            className="import-type__continuous"
                            color="info"
                            variant="secondary"
                            compact
                            title={t("Keeps importing the new messages of the account")}
                        >
                            <Icon name="autorenew" size={12} />
                            {t("Continuous")}
                        </Badge>
                    )}
                </div>
            ),
        },
        {
            id: "status",
            headerName: t("Status"),
            renderCell: ({ row }) => renderStatusLine(row),
        },
        {
            id: "actions",
            headerName: "",
            size: 60,
            renderCell: ({ row }) => row.mailbox ? (
                <RowActions
                    row={row}
                    pending={pendingIds.has(row.id)}
                    isOpen={openMenuId === row.id}
                    onOpenChange={(open) => setOpenMenuId(open ? row.id : null)}
                    onPause={handlePause}
                    onResume={handleResume}
                    onMakeRecurring={handleMakeRecurring}
                    onForget={handleForget}
                    onCancel={handleCancel}
                />
            ) : null,
        },
    ];

    if (isLoading) {
        return (
            <div className="admin-data-grid">
                <Banner type="info" icon={<Spinner />}>{t("Loading imports...")}</Banner>
            </div>
        );
    }
    if (isError) {
        return (
            <div className="admin-data-grid">
                <Banner type="error">{t("Error while loading imports")}</Banner>
            </div>
        );
    }

    return (
        <div className="admin-data-grid">
            <DataGrid
                columns={columns}
                rows={visibleRows}
                pagination={pagination}
                onSortModelChange={() => undefined}
                enableSorting={false}
                emptyPlaceholderLabel={emptyPlaceholderLabel ?? t("No imports yet")}
            />
        </div>
    );
};

type MailboxImportsDataGridProps = {
    mailboxId: string;
};

/** Every import of one mailbox, kept live while one of them progresses. */
export const MailboxImportsDataGrid = ({ mailboxId }: MailboxImportsDataGridProps) => {
    const queryClient = useQueryClient();
    const { data, isLoading, error } = useMailboxesImportsList(mailboxId, {
        query: {
            enabled: !!mailboxId,
            refetchInterval: (query) =>
                importsRefetchInterval(query.state.data?.data as ImportRun[] | undefined),
        },
        // Background status poll: let foreground requests win the wire.
        request: { priority: "low" },
    });

    const invalidate = () =>
        queryClient.invalidateQueries({
            queryKey: getMailboxesImportsListQueryKey(mailboxId),
            exact: false,
        });

    return (
        <ImportsDataGrid
            rows={(data?.data as ImportRun[] | undefined) ?? []}
            isLoading={isLoading}
            isError={!!error}
            onRowChange={invalidate}
        />
    );
};
