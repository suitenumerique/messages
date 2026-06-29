import { useTranslation } from "react-i18next";
import { Button, Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { type FilePreviewType, Icon, IconSize, IconType, UserAvatar } from "@gouvfr-lasuite/ui-kit";
import { useNavigate } from "@tanstack/react-router";
import type { AttachmentOrigin } from "./index";
import type { Attachment } from "@/features/api/gen/models";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useConfig } from "@/features/providers/config";
import { DateHelper } from "@/features/utils/date-helper";
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { DriveIcon } from "@/features/forms/components/message-form/drive-icon";
import { SidebarDriveAction } from "./sidebar-drive-action";

type AttachmentPreviewSidebarProps = {
    file: FilePreviewType;
    /** Provenance metadata of the source message. ``undefined`` for draft
     *  attachments — they live in local form state and have no persisted
     *  message to point to (sender/date/subject would all be meaningless). */
    origin: AttachmentOrigin | undefined;
    isDrive: boolean;
    /** The underlying blob attachment for non-Drive PJ. ``undefined`` for
     *  Drive PJ (the file already lives in Drive) and for draft attachments
     *  (not yet persisted on a Message). */
    attachment: Attachment | undefined;
    onClose: () => void;
};

/**
 * Contextual sidebar shown next to the file preview. Stacked sections:
 * the file itself (icon + name + size + format), either its provenance
 * (sender avatar, relative sent date, subject) or a "draft" notice when
 * the PJ isn't sent yet, and an optional security warning when the bytes
 * don't match the declared previewable type.
 */
export const AttachmentPreviewSidebar = ({ file, origin, isDrive, attachment, onClose }: AttachmentPreviewSidebarProps) => {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const { selectedThread } = useMailboxContext();
    const { DRIVE } = useConfig();

    const language = i18n.resolvedLanguage ?? i18n.language;
    // ``AttachmentHelper.getMimeCategory`` only reads ``type`` and ``name`` —
    // FilePreviewType carries them under ``mimetype``/``title``.
    const fileShape = { name: file.title, type: file.mimetype };
    const iconSrc = AttachmentHelper.getIcon(fileShape);
    const formatLabel = t(AttachmentHelper.getFormatTranslationKey(fileShape));
    const formattedSize = AttachmentHelper.getFormattedSize(file.size, language);
    const senderHasDistinctEmail = origin && origin.senderName !== origin.senderEmail;
    const fullDate = origin ? DateHelper.formatEventTimestamp(origin.sentAt, language) : null;
    const relativeDate = origin ? DateHelper.formatRelativeTime(origin.sentAt) : null;

    const goToMessage = () => {
        onClose();
        if (!selectedThread) return;

        const scrollToTarget = (target: HTMLElement, root: HTMLElement) => {
            const offset =
                target.getBoundingClientRect().top -
                root.getBoundingClientRect().top +
                root.scrollTop;
            // 225px matches the sticky-header offset used by the thread
            // view's own deep-link scroll (see thread-view/index.tsx).
            root.scrollTo({ top: Math.max(offset - 225, 0), behavior: "smooth" });
            target.classList.add("thread-view__highlight");
            window.setTimeout(
                () => target.classList.remove("thread-view__highlight"),
                1700,
            );
        };

        // Scroll the thread view to the actual attachment item, not just the
        // source message — long threads can hide a PJ several screenfuls
        // below its message header. rAF gives the viewer time to unmount
        // before we read the underlying DOM.
        requestAnimationFrame(() => {
            const root = document.querySelector<HTMLElement>(".thread-view");
            const target = document.getElementById(`attachment-anchor-${file.id}`);
            const messageEl = origin
                ? document.getElementById(`thread-message-${origin.messageId}`)
                : null;
            if (!root || !target) {
                // Truly absent (different thread): fall back to deep-linking
                // the source message so thread-view re-scrolls on mount.
                // Drafts have no persisted message to point to — nothing to
                // do beyond closing the viewer.
                if (origin) {
                    // Stay on the current route, keep its search params and only
                    // deep-link the source message via the hash fragment.
                    navigate({
                        to: ".",
                        search: (prev) => prev,
                        hash: `thread-message-${origin.messageId}`,
                        replace: true,
                    });
                }
                return;
            }

            // The footer (and therefore the AttachmentItem) is ``display: none``
            // while the message is folded — measuring would yield a 0/0 rect.
            // Unfold via the header toggle and wait for React to commit
            // before reading the layout.
            if (messageEl?.classList.contains("thread-message--folded")) {
                const toggle = messageEl.querySelector<HTMLButtonElement>(".thread-message__header-toggle");
                toggle?.click();
                requestAnimationFrame(() => requestAnimationFrame(() => scrollToTarget(target, root)));
                return;
            }
            scrollToTarget(target, root);
        });
    };

    return (
        <div className="attachment-preview-sidebar">
            <section className="attachment-preview-sidebar__file">
                <div className="attachment-preview-sidebar__file-icon">
                    <img src={iconSrc} alt="" />
                    {isDrive && <DriveIcon className="attachment-preview-sidebar__file-icon-badge" size="small" />}
                </div>
                <div className="attachment-preview-sidebar__file-meta">
                    <p className="attachment-preview-sidebar__file-name" title={file.title}>
                        {file.title}
                    </p>
                    <p className="attachment-preview-sidebar__file-info">
                        <span>{formatLabel}</span>
                        <span aria-hidden="true">·</span>
                        <span>{formattedSize}</span>
                    </p>
                </div>
            </section>

            {file.isSuspicious && (
                <section
                    className="attachment-preview-sidebar__warning"
                    role="status"
                >
                    <Icon name="warning" type={IconType.FILLED} className="attachment-preview-sidebar__warning-icon" />
                    <div>
                        <p className="attachment-preview-sidebar__warning-title">
                            {t("This file looks suspicious")}
                        </p>
                        <p className="attachment-preview-sidebar__warning-text">
                            {t("Its actual content doesn't match its declared type. Open it only if you trust the sender.")}
                        </p>
                    </div>
                </section>
            )}

            {origin ? (
                <section className="attachment-preview-sidebar__provenance">
                    <h3 className="attachment-preview-sidebar__section-title">{t("Provenance")}</h3>
                    <div className="attachment-preview-sidebar__sender">
                        <UserAvatar fullName={origin.senderName} size="small" />
                        <div className="attachment-preview-sidebar__sender-meta">
                            <p className="attachment-preview-sidebar__sender-name">{origin.senderName}</p>
                            {senderHasDistinctEmail && (
                                <p className="attachment-preview-sidebar__sender-email">{origin.senderEmail}</p>
                            )}
                        </div>
                    </div>
                    {origin.subject && (
                        <div className="attachment-preview-sidebar__field">
                            <span className="attachment-preview-sidebar__field-label">{t("Subject")}</span>
                            <p className="attachment-preview-sidebar__field-value">{origin.subject}</p>
                        </div>
                    )}
                    <div className="attachment-preview-sidebar__field">
                        <span className="attachment-preview-sidebar__field-label">
                            {origin.isSender ? t("Sent on") : t("Received on")}
                        </span>
                        <Tooltip content={fullDate ?? ""} placement="bottom">
                            <p className="attachment-preview-sidebar__field-value attachment-preview-sidebar__field-value--muted">
                                {relativeDate}
                            </p>
                        </Tooltip>
                    </div>
                </section>
            ) : (
                <section
                    className="attachment-preview-sidebar__draft"
                    role="status"
                >
                    <Icon name="mode_edit" size={IconSize.SMALL} className="attachment-preview-sidebar__draft-icon" />
                    <div>
                        <p className="attachment-preview-sidebar__draft-title">{t("Draft")}</p>
                        <p className="attachment-preview-sidebar__draft-text">
                            {t("This attachment isn't sent yet — it's part of the draft you're composing.")}
                        </p>
                    </div>
                </section>
            )}

            <div className="attachment-preview-sidebar__actions">
                {isDrive ? (
                    <Button
                        size="small"
                        variant="secondary"
                        fullWidth
                        // ``origin.driveUrl`` (the SDK ``url_permalink``) triggers
                        // a download in Drive. The explorer route opens the
                        // preview page — same pattern as DrivePreviewLink.
                        href={`${DRIVE.file_url}/${file.id}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        icon={<Icon name="open_in_new" />}
                    >
                        {t("Open in {{driveAppName}}", { driveAppName: DRIVE.app_name })}
                    </Button>
                ) : origin && attachment && (
                    // Hide "Save to Drive" for drafts: the PJ blob exists in the
                    // auto-saved Message, but the file isn't shareable until the
                    // draft is actually sent — ``origin`` is the reliable marker.
                    <SidebarDriveAction attachment={attachment} />
                )}
                {selectedThread && (
                    <Button size="small" variant="secondary" fullWidth onClick={goToMessage}>
                        {t("Show in conversation")}
                    </Button>
                )}
            </div>
        </div>
    );
};
