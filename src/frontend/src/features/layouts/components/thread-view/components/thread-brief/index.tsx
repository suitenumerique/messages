import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconSize, IconType } from "@gouvfr-lasuite/ui-kit";
import clsx from "clsx";
import { fetchAPI } from "@/features/api/fetch-api";
import { Badge } from "@/features/ui/components/badge";
import { TextLoader } from "@/features/ui/components/text-loader";
import { AttachmentHelper } from "@/features/utils/attachment-helper";

type AttachmentStatus = "ok" | "warning" | "unreadable" | "unchecked";

export type ThreadBriefAttachment = {
    name: string;
    content_type: string;
    sent_on: string;
    status: AttachmentStatus;
    description: string;
    note: string;
};

export type ThreadBriefData = {
    summary: string;
    request: string;
    key_points: string[];
    attachments: ThreadBriefAttachment[];
    generated_at: string;
};

type ThreadBriefResponse = {
    status: number;
    data: ThreadBriefData | null;
};

type ThreadBriefProps = {
    threadId: string;
    // Changes when a message arrives, so the brief is fetched again.
    latestMessageId?: string;
};

const COLLAPSED_STORAGE_KEY = "thread-brief-collapsed";

const fetchThreadBrief = async (threadId: string, language: string, refresh: boolean): Promise<ThreadBriefData | null> => {
    const params: Record<string, string> = refresh ? { language, refresh: "true" } : { language };
    const response = await fetchAPI<ThreadBriefResponse>(
        `/api/v1.0/threads/${threadId}/ai-brief/`,
        { params },
    );
    return response.data;
};

const readCollapsed = (): boolean => {
    try {
        return localStorage.getItem(COLLAPSED_STORAGE_KEY) === "true";
    } catch {
        return false;
    }
};

const writeCollapsed = (isCollapsed: boolean) => {
    try {
        localStorage.setItem(COLLAPSED_STORAGE_KEY, String(isCollapsed));
    } catch {
        // Storage unavailable (private mode…): the panel simply won't remember.
    }
};

const AttachmentStatusBadge = ({ status }: { status: AttachmentStatus }) => {
    const { t } = useTranslation();
    switch (status) {
        case "ok":
            return <Badge color="success" variant="secondary" compact>{t("Looks valid")}</Badge>;
        case "warning":
            return <Badge color="warning" variant="secondary" compact>{t("To check")}</Badge>;
        case "unreadable":
            return <Badge color="error" variant="secondary" compact>{t("Unreadable")}</Badge>;
        default:
            return <Badge color="neutral" variant="secondary" compact>{t("Not checked")}</Badge>;
    }
};

/** Translate the reason an attachment could not be read, sent by the backend. */
const useUnreadableReason = () => {
    const { t } = useTranslation();
    const reasons: Record<string, string> = {
        "unsupported format": t("Format not supported by the AI"),
        "file too large": t("File too large to be read"),
        "could not be read": t("The document could not be read"),
        "OCR not configured": t("Document reading is not configured"),
        "no text found": t("No text found in the document"),
        "not read (attachment limit reached)": t("Not read: too many attachments"),
    };
    return (note: string) => reasons[note] ?? note;
};

const BriefAttachment = ({ attachment }: { attachment: ThreadBriefAttachment }) => {
    const { t, i18n } = useTranslation();
    const unreadableReason = useUnreadableReason();
    const icon = AttachmentHelper.getIcon({ name: attachment.name, type: attachment.content_type }, true);
    const detail = attachment.status === "unreadable" ? unreadableReason(attachment.note) : attachment.description;
    const sentOn = attachment.sent_on
        ? new Date(`${attachment.sent_on}T00:00:00`).toLocaleDateString(i18n.language, { day: "numeric", month: "short" })
        : null;

    return (
        <li className={clsx("thread-brief__attachment", `thread-brief__attachment--${attachment.status}`)}>
            <img className="thread-brief__attachment-icon" src={icon} alt="" />
            <div className="thread-brief__attachment-body">
                <p className="thread-brief__attachment-name">
                    <span title={attachment.name}>{attachment.name}</span>
                    {sentOn && <span className="thread-brief__muted">{t("received {{date}}", { date: sentOn })}</span>}
                </p>
                {detail && <p className="thread-brief__attachment-detail">{detail}</p>}
                {attachment.status === "warning" && attachment.note && (
                    <p className="thread-brief__attachment-note">
                        <Icon name="warning" type={IconType.OUTLINED} size={IconSize.SMALL} />
                        {attachment.note}
                    </p>
                )}
            </div>
            <AttachmentStatusBadge status={attachment.status} />
        </li>
    );
};

const BriefContent = ({ brief }: { brief: ThreadBriefData }) => {
    const { t } = useTranslation();
    const warnings = brief.attachments.filter(({ status }) => status === "warning" || status === "unreadable").length;

    return (
        <div className="thread-brief__content">
            <div className="thread-brief__main">
                {brief.summary && <p className="thread-brief__summary">{brief.summary}</p>}
                {brief.request && (
                    <p className="thread-brief__request">
                        <Icon name="task_alt" type={IconType.OUTLINED} size={IconSize.SMALL} />
                        <span>
                            <span className="thread-brief__label">{t("Expected action")}</span>
                            {brief.request}
                        </span>
                    </p>
                )}
                {brief.key_points.length > 0 && (
                    <ul className="thread-brief__points">
                        {brief.key_points.map((point) => <li key={point}>{point}</li>)}
                    </ul>
                )}
            </div>
            <div className="thread-brief__side">
                <h3 className="thread-brief__side-title">
                    {t("Attachments")}
                    <span className="thread-brief__count">{brief.attachments.length}</span>
                    {warnings > 0 && (
                        <span className="thread-brief__alert">
                            {t("{{count}} to check", { count: warnings })}
                        </span>
                    )}
                </h3>
                {brief.attachments.length > 0 ? (
                    <ul className="thread-brief__attachments">
                        {brief.attachments.map((attachment, index) => (
                            <BriefAttachment key={`${attachment.name}-${index}`} attachment={attachment} />
                        ))}
                    </ul>
                ) : (
                    <p className="thread-brief__muted">{t("No attachment from the citizen.")}</p>
                )}
            </div>
        </div>
    );
};

/**
 * AI brief shown at the top of a thread: what the citizen writes about, what
 * they expect, the key facts and a check of their attachments, so the agent
 * understands the case before reading the messages.
 */
export const ThreadBrief = ({ threadId, latestMessageId }: ThreadBriefProps) => {
    const { t, i18n } = useTranslation();
    const queryClient = useQueryClient();
    const [isCollapsed, setIsCollapsed] = useState(readCollapsed);
    const [isRefreshing, setIsRefreshing] = useState(false);
    const [hasRefreshError, setHasRefreshError] = useState(false);
    const queryKey = ["threads", threadId, "ai-brief", i18n.language, latestMessageId];

    const { data: brief, isLoading, isError, refetch } = useQuery({
        queryKey,
        queryFn: () => fetchThreadBrief(threadId, i18n.language, false),
        staleTime: Infinity,
        retry: false,
        meta: { noGlobalError: true },
    });

    const toggleCollapsed = () => {
        setIsCollapsed((collapsed) => {
            writeCollapsed(!collapsed);
            return !collapsed;
        });
    };

    const handleRefresh = async () => {
        setIsRefreshing(true);
        setHasRefreshError(false);
        try {
            const freshBrief = await fetchThreadBrief(threadId, i18n.language, true);
            queryClient.setQueryData(queryKey, freshBrief);
        } catch {
            setHasRefreshError(true);
        } finally {
            setIsRefreshing(false);
        }
    };

    // Nothing to summarize (e.g. a thread with only drafts).
    if (!isLoading && !isError && !brief) return null;

    const isBusy = isLoading || isRefreshing;
    const hasError = isError || hasRefreshError;

    return (
        <section className={clsx("thread-brief", { "thread-brief--collapsed": isCollapsed })} aria-label={t("AI brief")}>
            <header className="thread-brief__header">
                <button
                    type="button"
                    className="thread-brief__toggle"
                    aria-expanded={!isCollapsed}
                    onClick={toggleCollapsed}
                >
                    <span className="thread-brief__spark">
                        <Icon name="auto_awesome" size={IconSize.SMALL} />
                    </span>
                    <span className="thread-brief__title">{t("AI brief")}</span>
                    {isCollapsed && brief?.request && (
                        <span className="thread-brief__peek">{brief.request}</span>
                    )}
                    <Icon name={isCollapsed ? "expand_more" : "expand_less"} size={IconSize.SMALL} />
                </button>
                <Button
                    color="brand"
                    variant="tertiary"
                    size="small"
                    icon={<Icon name="refresh" type={IconType.OUTLINED} />}
                    aria-label={t("Regenerate the AI brief")}
                    title={t("Regenerate the AI brief")}
                    onClick={handleRefresh}
                    disabled={isBusy}
                />
            </header>
            {!isCollapsed && (
                <div className="thread-brief__body" aria-busy={isBusy}>
                    {isBusy ? (
                        <div className="thread-brief__loading">
                            <p className="thread-brief__muted">{t("Reading the email and its attachments…")}</p>
                            <TextLoader lines={3} />
                        </div>
                    ) : hasError ? (
                        <div className="thread-brief__error">
                            <p>{t("The AI brief could not be generated.")}</p>
                            <Button color="brand" variant="secondary" size="small" onClick={() => (isError ? refetch() : handleRefresh())}>
                                {t("Retry")}
                            </Button>
                        </div>
                    ) : (
                        brief && <BriefContent brief={brief} />
                    )}
                    {!isBusy && !hasError && (
                        <p className="thread-brief__disclaimer">
                            {t("Generated by AI from the email and its attachments: check the original before replying.")}
                        </p>
                    )}
                </div>
            )}
        </section>
    );
};
