import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Checkbox, Column, DataGrid, useModal, useModals } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Mailbox, ReadMessageTemplate, MessageTemplateTypeChoices, useMailboxesMessageTemplatesList, useMailboxesMessageTemplatesDestroy, useMailboxesMessageTemplatesPartialUpdate, MessageTemplateMetadata } from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { ModalComposeMailboxAutoreply } from "../modal-compose-mailbox-autoreply";
import { Badge } from "@/features/ui/components/badge";

type AutoreplyDataGridProps = {
    mailbox: Mailbox;
}

const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const formatSchedule = (t: (key: string) => string, metadata?: MessageTemplateMetadata): string => {
    if (!metadata || !metadata.schedule_type) return t("Always");
    const scheduleType = metadata.schedule_type as string;

    if (scheduleType === "always") return t("Always");

    if (scheduleType === "date_range") {
        const startAt = metadata.start_at as string | undefined;
        const endAt = metadata.end_at as string | undefined;
        if (startAt && endAt) {
            try {
                const start = new Date(startAt).toLocaleDateString();
                const end = new Date(endAt).toLocaleDateString();
                return `${start} – ${end}`;
            } catch {
                return t("Date range");
            }
        }
        return t("Date range");
    }

    if (scheduleType === "recurring_weekly") {
        const intervals = metadata.intervals as Array<Record<string, unknown>> | undefined;
        if (intervals && intervals.length > 0) {
            const interval = intervals[0];
            const startDay = interval?.start_day as number | undefined;
            const startTime = interval?.start_time as string | undefined;
            const endDay = interval?.end_day as number | undefined;
            const endTime = interval?.end_time as string | undefined;
            if (startDay && endDay && startTime && endTime) {
                const startLabel = t(DAY_NAMES[startDay - 1] ?? "");
                const endLabel = t(DAY_NAMES[endDay - 1] ?? "");
                return `${startLabel} ${startTime} – ${endLabel} ${endTime}`;
            }
        }
        return t("Recurring weekly");
    }

    return "";
};

export const AutoreplyDataGrid = ({ mailbox }: AutoreplyDataGridProps) => {
    const { t } = useTranslation();
    const modals = useModals();
    const modal = useModal();
    const { data: autoreplies, isLoading, error, queryKey } = useMailboxesMessageTemplatesList(
        mailbox.id,
        {
            type: [MessageTemplateTypeChoices.autoreply],
        },
        {
            query: {
                enabled: !!mailbox.id,
            },
        }
    );
    const { mutateAsync: updateAutoreply, isPending: isUpdating } = useMailboxesMessageTemplatesPartialUpdate();
    const { mutateAsync: deleteAutoreply } = useMailboxesMessageTemplatesDestroy({
        mutation: {
            meta: { noGlobalError: false },
        },
    });
    const [deletingId, setDeletingId] = useState<string | null>(null);
    const [selectedAutoreply, setSelectedAutoreply] = useState<ReadMessageTemplate | undefined>();
    const queryClient = useQueryClient();

    const invalidateAutoreplies = async () => {
        await queryClient.invalidateQueries({ queryKey, exact: true });
    }

    const handleModifyRow = (autoreply: ReadMessageTemplate) => {
        setSelectedAutoreply(autoreply);
        modal.open();
    }

    const addUpdateSucceededToast = () => {
        addToast(
            <ToasterItem type="info">
                <span>{t("Auto-reply updated!")}</span>
            </ToasterItem>,
        );
    }

    const handleDeleteRow = async (autoreply: ReadMessageTemplate) => {
        const decision = await modals.deleteConfirmationModal({
            title: <span className="c__modal__text--centered">{t('Delete auto-reply "{{autoreply}}"', { autoreply: autoreply.name })}</span>,
            children: t('Are you sure you want to delete this auto-reply? This action is irreversible!'),
        });
        if (decision === 'delete') {
            setDeletingId(autoreply.id);
            try {
                await deleteAutoreply({ mailboxId: mailbox.id, id: autoreply.id });
                await invalidateAutoreplies();
                addToast(
                    <ToasterItem type="info">
                        <span>{t("Auto-reply deleted!")}</span>
                    </ToasterItem>,
                );
            } catch {
                addToast(
                    <ToasterItem type="error">
                        <span>{t("Failed to delete auto-reply.")}</span>
                    </ToasterItem>,
                );
            } finally {
                setDeletingId(null);
            }
        }
    }

    const toggleActive = async (autoreply: ReadMessageTemplate) => {
        try {
            await updateAutoreply({
                mailboxId: mailbox.id,
                id: autoreply.id,
                data: { is_active: !autoreply.is_active },
            });
            await invalidateAutoreplies();
            addUpdateSucceededToast();
        } catch {
            addToast(
                <ToasterItem type="error">
                    <span>{t("Failed to update auto-reply.")}</span>
                </ToasterItem>,
            );
        }
    }

    const columns: Column<ReadMessageTemplate>[] = [
        {
            id: "is_active",
            headerName: t("Active"),
            size: 75,
            renderCell: ({ row }) => (
                <div className="flex-row flex-justify-center">
                    <Checkbox
                        checked={row.is_active}
                        onChange={() => toggleActive(row)}
                        disabled={isUpdating}
                        aria-label={t("Active")}
                    />
                </div>
            ),
        },
        {
            id: "name",
            headerName: t("Name"),
            renderCell: ({ row }) => row.name,
        },
        {
            id: "schedule",
            headerName: t("Schedule"),
            renderCell: ({ row }) => (
                <div className="flex-row flex-align-center" style={{ gap: "var(--c--globals--spacings--2xs)" }}>
                    {formatSchedule(t, row.metadata)}
                    {row.is_active
                        ? row.is_active_autoreply
                            ? <Badge color="success">{t("On going")}</Badge>
                            : <Badge color="warning">{t("Scheduled")}</Badge>
                        : null
                    }
                </div>

            ),
        },
        {
            id: "actions",
            size: 154,
            headerName: t("Actions"),
            renderCell: ({ row }) => (
                <div className="flex-row flex-justify-start" style={{ width: "100%", gap: "var(--c--globals--spacings--2xs)" }}>
                    <Button
                        variant="bordered"
                        size="small"
                        onClick={() => handleModifyRow(row)}
                    >
                        {t("Modify")}
                    </Button>
                    <Button
                        color="error"
                        size="small"
                        onClick={() => handleDeleteRow(row)}
                        disabled={deletingId === row.id}
                        icon={deletingId === row.id ? <Spinner size="sm" /> : <Icon name="delete" size={IconSize.SMALL} />}
                        aria-label={t("Delete")}
                    />
                </div>
            ),
        },
    ];

    if (isLoading) {
        return (
            <div className="admin-data-grid">
                <Banner type="info" icon={<Spinner />}>
                    {t("Loading auto-replies...")}
                </Banner>
            </div>
        );
    }

    if (error) {
        return (
            <div className="admin-data-grid">
                <Banner type="error">
                    {t("Error while loading auto-replies")}
                </Banner>
            </div>
        );
    }

    return (
        <section className="admin-page__body">
            <div className="admin-data-grid">
                <DataGrid
                    columns={columns}
                    rows={autoreplies?.data ?? []}
                    onSortModelChange={() => undefined}
                    enableSorting={false}
                    emptyPlaceholderLabel={t("No auto-replies found")}
                />
                <ModalComposeMailboxAutoreply
                    isOpen={modal.isOpen}
                    onClose={
                        () => {
                            modal.close();
                            if (selectedAutoreply) {
                                setSelectedAutoreply(undefined);
                            }
                        }
                    }
                    autoreply={selectedAutoreply}
                />
            </div>
        </section>
    );
};
