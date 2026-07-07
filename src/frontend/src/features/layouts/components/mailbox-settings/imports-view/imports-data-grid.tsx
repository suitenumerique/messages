import { DropdownMenu, DropdownMenuOption, Icon, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Column, DataGrid, useModals } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import { Fragment, ReactNode, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
    Mailbox,
    ImportRun,
    useMailboxesImportsList,
    useMailboxesImportsCancelCreate,
    useMailboxesImportsDestroy,
    useMailboxesImportsPartialUpdate,
    getMailboxesImportsListQueryKey,
} from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import ProgressBar from "@/features/ui/components/progress-bar";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { handle } from "@/features/utils/errors";
import { DateHelper } from "@/features/utils/date-helper";
import { isTerminal, STATUS_CANCELLED } from "@/hooks/use-import-status";

type ImportsDataGridProps = {
    mailbox: Mailbox;
};

const isRunning = (row: ImportRun) => !isTerminal(row.status);
const isImap = (row: ImportRun) => row.source_type === "imap";
const isContinuous = (row: ImportRun) => row.mode === "continuous";
// poll_interval is in seconds (global operator setting)
const pollMinutes = (row: ImportRun) => Math.max(1, Math.round((row.poll_interval ?? 900) / 60));

// A settled run whose channel row can be deleted without a live worker or
// poller losing it mid-run (the backend enforces the same rule).
const canForget = (row: ImportRun) => isTerminal(row.status) && row.is_active === false;

type RowActionsProps = {
    row: ImportRun;
    pending: boolean;
    // Open state lives in the parent (keyed by row id): the grid re-renders
    // every 2s while a run is in progress, and menu state local to the row
    // would be reset — closing the menu in the user's hand.
    isOpen: boolean;
    onOpenChange: (open: boolean) => void;
    onPause: (row: ImportRun) => void;
    onResume: (row: ImportRun) => void;
    onMakeRecurring: (row: ImportRun) => void;
    onForget: (row: ImportRun) => void;
    onCancel: (row: ImportRun) => void;
};

/** The per-row [...] menu: every action spelled out, nothing icon-only. */
const RowActions = ({ row, pending, isOpen, onOpenChange, onPause, onResume, onMakeRecurring, onForget, onCancel }: RowActionsProps) => {
    const { t } = useTranslation();

    const options: DropdownMenuOption[] = [];
    if (isContinuous(row)) {
        if (row.is_active !== false) {
            options.push({
                label: t("Pause polling"),
                icon: <Icon name="pause" />,
                callback: () => onPause(row),
            });
        } else {
            options.push({
                label: t("Resume polling"),
                icon: <Icon name="play_arrow" />,
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
        icon: <Icon name="delete" />,
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

export const ImportsDataGrid = ({ mailbox }: ImportsDataGridProps) => {
    const { t, i18n } = useTranslation();
    const modals = useModals();
    const queryClient = useQueryClient();
    const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());
    const [openMenuId, setOpenMenuId] = useState<string | null>(null);

    const { data, isLoading, error } = useMailboxesImportsList(mailbox.id, {
        query: {
            enabled: !!mailbox.id,
            // Poll while any run is still progressing so status/progress stay
            // live; stop once everything is terminal (continuous channels
            // between polls read "completed", so they don't pin the poll).
            refetchInterval: (query) => {
                const rows =
                    (query.state.data?.data as ImportRun[] | undefined) ?? [];
                const active = rows.some(
                    (r) => r.status === "pending" || r.status === "running",
                );
                return active ? 15000 : false;
            },
        },
        // Background status poll: let foreground requests win the wire.
        request: { priority: "low" },
    });

    const cancelMutation = useMailboxesImportsCancelCreate();
    const updateMutation = useMailboxesImportsPartialUpdate();
    const forgetMutation = useMailboxesImportsDestroy();

    // A cancelled run is removed server-side once its purge settles; filter
    // out any row still (or left) in the cancelled state so cancelling makes
    // the import disappear from the list immediately.
    const rows = (data?.data ?? []).filter((row) => row.status !== STATUS_CANCELLED);

    const invalidate = async () => {
        await queryClient.invalidateQueries({
            queryKey: getMailboxesImportsListQueryKey(mailbox.id),
            exact: false,
        });
    };

    const withPending = async (id: string, fn: () => Promise<unknown>, successMessage?: string) => {
        setPendingIds((prev) => new Set(prev).add(id));
        try {
            await fn();
            await invalidate();
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

    const handlePause = (row: ImportRun) =>
        withPending(
            row.id,
            () => updateMutation.mutateAsync({ mailboxId: mailbox.id, id: row.id, data: { is_active: false } }),
            t("Polling paused."),
        );

    const handleResume = (row: ImportRun) =>
        withPending(
            row.id,
            () => updateMutation.mutateAsync({ mailboxId: mailbox.id, id: row.id, data: { is_active: true } }),
            t("Polling resumed."),
        );

    const handleMakeRecurring = (row: ImportRun) =>
        withPending(
            row.id,
            () => updateMutation.mutateAsync({ mailboxId: mailbox.id, id: row.id, data: { mode: "continuous" } }),
            t("This account will now be checked for new mail regularly."),
        );

    const handleForget = (row: ImportRun) =>
        withPending(
            row.id,
            () => forgetMutation.mutateAsync({ mailboxId: mailbox.id, id: row.id }),
            t("Import removed from the list. Its messages were kept."),
        );

    const handleCancel = async (row: ImportRun) => {
        const decision = await modals.deleteConfirmationModal({
            title: <span className="c__modal__text--centered">{t('Delete the messages of "{{name}}"', { name: row.name })}</span>,
            children: t("This deletes every message this import created, except those in conversations with replies or other activity. This action is irreversible!"),
        });
        if (decision !== "delete") return;
        await withPending(
            row.id,
            () => cancelMutation.mutateAsync({ mailboxId: mailbox.id, id: row.id }),
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
                <span key="status" className="import-status import-status--error" title={row.error ?? undefined}>
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
                parts.push(
                    <span key="failures" className="import-status import-status--error">
                        {t("{{count}} failed", { count: row.failure_count ?? 0 })}
                    </span>,
                );
            }
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

    const columns: Column<ImportRun>[] = [
        {
            id: "type",
            headerName: t("Type"),
            size: 110,
            renderCell: ({ row }) => <span>{(row.source_type ?? "").toUpperCase()}</span>,
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
            renderCell: ({ row }) => (
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
            ),
        },
    ];

    if (isLoading) {
        return (
            <div className="admin-data-grid">
                <Banner type="info" icon={<Spinner />}>{t("Loading imports...")}</Banner>
            </div>
        );
    }
    if (error) {
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
                rows={rows}
                onSortModelChange={() => undefined}
                enableSorting={false}
                emptyPlaceholderLabel={t("No imports yet")}
            />
        </div>
    );
};
