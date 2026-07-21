import { useTranslation } from "react-i18next"
import { Link } from "@tanstack/react-router"
import clsx from "clsx"
import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit"
import { Button } from "@gouvfr-lasuite/cunningham-react"
import { DateHelper } from "@/features/utils/date-helper"
import { useMessageSummaries } from "./use-message-summaries"

type ThreadItemMessageSummariesProps = {
    threadId: string
    mailboxId: string
}

/**
 * Renders the per-message summary rows shown when a thread-list row is
 * expanded (Task 8's chevron). Sender, date, snippet, unread weight and
 * attachment icon per message; drafts are visually distinguished but not
 * yet wired to open the composer on click (see Task 10 brief) — they
 * navigate like any other row, landing on the draft's position in the
 * thread view.
 */
export const ThreadItemMessageSummaries = ({ threadId, mailboxId }: ThreadItemMessageSummariesProps) => {
    const { t, i18n } = useTranslation()
    const { data, isLoading, isError, refetch } = useMessageSummaries(threadId, mailboxId, { enabled: true })

    if (isLoading) {
        return (
            <div className="thread-item__summaries thread-item__summaries--loading" role="status">
                <Spinner size="sm" />
            </div>
        )
    }

    if (isError) {
        return (
            <div className="thread-item__summaries thread-item__summaries--error">
                <span>{t("Could not load messages.")}</span>
                <Button size="small" variant="secondary" onClick={() => refetch()}>
                    {t("Retry")}
                </Button>
            </div>
        )
    }

    return (
        <ul className="thread-item__summaries">
            {(data ?? []).map((message) => (
                <li key={message.id} className="thread-item__summary-row">
                    <Link
                        to="/mailbox/$mailboxId/thread/$threadId"
                        params={{ mailboxId, threadId }}
                        search={true}
                        hash={`thread-message-${message.id}`}
                        className={clsx("thread-item__summary-link", {
                            "thread-item__summary-link--unread": message.is_unread,
                            "thread-item__summary-link--draft": message.is_draft,
                        })}
                    >
                        <span className="thread-item__summary-sender">{message.sender.name}</span>
                        {message.is_draft && (
                            <span className="thread-item__summary-draft-label">{t("Draft")}</span>
                        )}
                        {message.sent_at && (
                            <span className="thread-item__summary-date">
                                {DateHelper.formatDate(message.sent_at, i18n.resolvedLanguage)}
                            </span>
                        )}
                        {message.has_attachments && (
                            <Icon
                                name="attachment"
                                type={IconType.OUTLINED}
                                aria-hidden="true"
                                className="icon--size-sm"
                            />
                        )}
                        <span className="thread-item__summary-snippet">{message.snippet}</span>
                    </Link>
                </li>
            ))}
        </ul>
    )
}
