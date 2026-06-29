import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FilePreview, type FilePreviewType } from "@gouvfr-lasuite/ui-kit";
import { useAttachmentPreview } from "@/features/providers/attachment-preview";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useConfig } from "@/features/providers/config";
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import MailHelper from "@/features/utils/mail-helper";
import type { Attachment, Message } from "@/features/api/gen/models";
import type { DriveFile } from "@/features/forms/components/message-form/drive-attachment-picker";
import { AttachmentPreviewSidebar } from "./sidebar";

/**
 * Error ``code`` returned by /blob/{id}/preview/ in a 415 body when the
 * declared (previewable) type doesn't match the actual bytes. Mirrors
 * ``PreviewRefusalCode.SUSPICIOUS`` in ``src/backend/core/enums.py``.
 */
const PREVIEW_SUSPICIOUS_CODE = "suspicious";

/**
 * Origin metadata kept aside from FilePreviewType (the kit type has no slot
 * for it). Indexed by FilePreviewType.id so the sidebar can resolve the
 * source message of whichever file the viewer is currently showing.
 */
export type AttachmentOrigin = {
    messageId: string;
    senderName: string;
    senderEmail: string;
    sentAt: string;
    subject: string | null;
    /** Whether the source message was sent by the active mailbox. Used by the
     *  sidebar to label the date as "Sent on" vs. "Received on". */
    isSender: boolean;
    /** Drive-specific permalink, used by the "Open in Drive" sidebar action
     *  and as the destination when the user clicks the header download button. */
    driveUrl?: string;
};

type ResolvedAttachment = {
    file: FilePreviewType;
    origin: AttachmentOrigin;
};

/**
 * Aggregates every attachment of the current thread (regular + Drive) and
 * renders the kit's FilePreview as a fullscreen modal. Mounted once at the
 * MainLayout level so it survives navigation between messages of the thread.
 */
export const AttachmentPreviewModal = () => {
    const { DRIVE } = useConfig();
    const { isOpen, openedFileId, closePreview, overrideFiles, overrideDriveUrlById } = useAttachmentPreview();
    const { messages } = useMailboxContext();
    const [currentFileId, setCurrentFileId] = useState<string | null>(openedFileId);

    // Sync the current file when the user opens the viewer on a new attachment.
    useEffect(() => {
        if (openedFileId !== null) {
            setCurrentFileId(openedFileId);
        }
    }, [openedFileId]);

    const resolved = useMemo<ResolvedAttachment[]>(() => {
        if (!messages) return [];
        return messages.flatMap((message) =>
            resolveMessageAttachments(message, DRIVE.preview_url),
        );
    }, [messages, DRIVE.preview_url]);

    const messagesFiles = useMemo(() => resolved.map((r) => r.file), [resolved]);
    // When the opener supplies its own files (a draft compose form), they take
    // precedence: a draft's attachments live in local form state and are never
    // part of the thread's persisted ``messages``.
    const files = overrideFiles ?? messagesFiles;

    const originById = useMemo(() => {
        const map = new Map<string, AttachmentOrigin>();
        for (const r of resolved) {
            map.set(r.file.id, r.origin);
        }
        return map;
    }, [resolved]);

    // Underlying blob attachments indexed by blobId — the header's "Save to
    // Drive" action needs the raw Attachment (blobId, name, size). Draft
    // attachments are persisted on the auto-saved Message and thus appear
    // here too; the sidebar hides the action for them based on ``origin``.
    const attachmentByBlobId = useMemo(() => {
        const map = new Map<string, Attachment>();
        if (!messages) return map;
        for (const message of messages) {
            for (const attachment of message.attachments) {
                map.set(attachment.blobId, attachment);
            }
        }
        return map;
    }, [messages]);

    // Resolve a Drive permalink from either source: the override map (draft)
    // or the message-derived origin (thread). Used to keep the "Open in Drive"
    // header/menu action working regardless of where the file came from.
    const getDriveUrl = useCallback(
        (fileId: string | null | undefined): string | undefined => {
            if (!fileId) return undefined;
            return overrideDriveUrlById?.get(fileId) ?? originById.get(fileId)?.driveUrl;
        },
        [overrideDriveUrlById, originById],
    );

    // Ids whose preview endpoint refused with 415: the declared (previewable)
    // type doesn't match the actual bytes, so the file is flagged suspicious.
    const [suspiciousIds, setSuspiciousIds] = useState<ReadonlySet<string>>(() => new Set());
    // Already-probed ids, so we don't re-query the endpoint on every re-render.
    const probedIdsRef = useRef<Set<string>>(new Set());

    const currentFile = useMemo(
        () => (currentFileId ? files.find((file) => file.id === currentFileId) : undefined),
        [currentFileId, files],
    );
    const currentDriveUrl = getDriveUrl(currentFileId);

    // Inject the suspicious flag discovered by the probe. A new array reference
    // is only created when something is actually flagged.
    const displayFiles = useMemo(
        () =>
            suspiciousIds.size === 0
                ? files
                : files.map((file) =>
                      suspiciousIds.has(file.id) && !file.isSuspicious
                          ? { ...file, isSuspicious: true }
                          : file,
                  ),
        [files, suspiciousIds],
    );

    // Provenance (sender/date) only applies to thread attachments; for a
    // draft's own PJ the sidebar still renders, but skips that section and
    // shows a "draft" notice instead.
    const currentOrigin = !overrideFiles && currentFileId ? originById.get(currentFileId) : undefined;
    const isCurrentDriveFile = currentDriveUrl !== undefined;
    const currentDisplayFile = useMemo(
        () => (currentFileId ? displayFiles.find((file) => file.id === currentFileId) : undefined),
        [currentFileId, displayFiles],
    );

    const handleDownloadFile = (file?: FilePreviewType) => {
        if (!file?.url) return;
        // Drive files: open the permalink instead of triggering a download
        // (there is no blob endpoint to stream the bytes from).
        const driveUrl = getDriveUrl(file.id);
        if (driveUrl) {
            window.open(driveUrl, "_blank", "noopener,noreferrer");
            return;
        }
        window.location.href = file.url;
    };

    const currentAttachment = currentFileId ? attachmentByBlobId.get(currentFileId) : undefined;

    // Probe the preview endpoint for the file being viewed. The kit checks
    // ``isSuspicious`` *before* fetching and exposes no error callback, so we
    // can't react to its own load failure — we ask the endpoint ourselves and
    // flip ``isSuspicious`` when it answers 415 with the "suspicious" code
    // (declared previewable type, but the bytes don't match). Drive files
    // (no blob endpoint) are skipped.
    useEffect(() => {
        if (!currentFile || currentDriveUrl) return;
        const { id, url_preview: urlPreview } = currentFile;
        if (!urlPreview) return;
        if (probedIdsRef.current.has(id)) return;
        probedIdsRef.current.add(id);

        const controller = new AbortController();
        void (async () => {
            try {
                const response = await fetch(urlPreview, {
                    credentials: "include",
                    signal: controller.signal,
                });
                if (response.status === 415) {
                    // The backend disambiguates suspicious vs. plainly
                    // unsupported; only the former warrants the warning UI.
                    const body = (await response.json().catch(() => null)) as
                        | { code?: string }
                        | null;
                    if (body?.code === PREVIEW_SUSPICIOUS_CODE) {
                        setSuspiciousIds((prev) => {
                            if (prev.has(id)) return prev;
                            const next = new Set(prev);
                            next.add(id);
                            return next;
                        });
                    }
                } else {
                    // Valid (or other status): don't stream the body twice —
                    // the kit fetches it again to render.
                    void response.body?.cancel().catch(() => {});
                }
            } catch {
                // Aborted (file changed) or network error: allow a later retry.
                probedIdsRef.current.delete(id);
            }
        })();

        return () => controller.abort();
    }, [currentFile, currentDriveUrl]);

    if (!isOpen || displayFiles.length === 0) {
        return null;
    }

    return (
        <FilePreview
            isOpen={isOpen}
            onClose={closePreview}
            files={displayFiles}
            openedFileId={openedFileId ?? undefined}
            onChangeFile={(file) => setCurrentFileId(file?.id ?? null)}
            handleDownloadFile={handleDownloadFile}
            pdfWorkerSrc="/pdf.worker.min.mjs"
            sidebarContent={
                currentDisplayFile ? (
                    <AttachmentPreviewSidebar
                        file={currentDisplayFile}
                        origin={currentOrigin}
                        isDrive={isCurrentDriveFile}
                        attachment={currentAttachment}
                        onClose={closePreview}
                    />
                ) : null
            }
        />
    );
};

/**
 * Extracts the previewable attachments of a single message — both regular
 * blobs (via ``message.attachments``) and Drive files (extracted from the
 * HTML body via ``MailHelper.extractDriveAttachmentsFromHtmlBody``).
 */
function resolveMessageAttachments(message: Message, drivePreviewBaseUrl: string): ResolvedAttachment[] {
    const senderName = message.sender.name || message.sender.email;
    const baseOrigin: AttachmentOrigin = {
        messageId: message.id,
        senderName,
        senderEmail: message.sender.email,
        sentAt: message.sent_at || message.created_at,
        subject: message.subject,
        isSender: message.is_sender,
    };

    const regular: ResolvedAttachment[] = message.attachments.map((attachment) => ({
        file: AttachmentHelper.toFilePreviewType(attachment),
        origin: baseOrigin,
    }));

    const driveFiles = collectDriveFiles(message);
    const drives: ResolvedAttachment[] = driveFiles.map((file) => ({
        file: AttachmentHelper.driveFileToFilePreviewType(file, drivePreviewBaseUrl),
        origin: { ...baseOrigin, driveUrl: file.url },
    }));

    return [...regular, ...drives];
}

/**
 * Walks every HTML body part of the message and returns the Drive files
 * declared as ``<a class="drive-attachment">`` blocks. Mirrors the
 * extraction already done by ThreadMessage so the modal stays in sync
 * with what's visible in the conversation pane.
 */
function collectDriveFiles(message: Message): DriveFile[] {
    if (message.htmlBody.length === 0) return [];
    const collected: DriveFile[] = [];
    for (const part of message.htmlBody) {
        const partContent = part?.content || "";
        const [, attachments] = MailHelper.extractDriveAttachmentsFromHtmlBody(partContent);
        collected.push(...attachments);
    }
    return collected;
}
