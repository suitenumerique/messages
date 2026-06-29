import { createContext, PropsWithChildren, useCallback, useContext, useMemo, useState } from "react";
import type { FilePreviewType } from "@gouvfr-lasuite/ui-kit";

/**
 * File set supplied by the opener when the attachments to preview are not
 * (yet) part of the thread's persisted ``messages`` query — typically a draft
 * compose form, whose attachments live in local form state and are never
 * reflected back into ``messages`` (the draft auto-save doesn't invalidate it,
 * and a brand-new draft has no persisted Message at all).
 */
export type AttachmentPreviewOverride = {
    files: FilePreviewType[];
    /** Drive permalink per file id, so the viewer keeps its "Open in Drive" action. */
    driveUrlById?: Map<string, string>;
};

type AttachmentPreviewContextType = {
    /**
     * Open the viewer focused on a specific attachment id (blobId or Drive id).
     * Pass ``override`` to preview files that aren't in the thread (draft PJ);
     * omit it to let the modal aggregate the current thread's attachments.
     */
    openPreview: (fileId: string, override?: AttachmentPreviewOverride) => void;
    /** Close the viewer without changing which file would have been focused. */
    closePreview: () => void;
    isOpen: boolean;
    /** Id of the attachment the viewer should display first. */
    openedFileId: string | null;
    /** When set, the modal renders these files instead of aggregating the thread. */
    overrideFiles: FilePreviewType[] | null;
    /** Drive permalinks for the override files (id → url). */
    overrideDriveUrlById: Map<string, string> | null;
};

const AttachmentPreviewContext = createContext<AttachmentPreviewContextType | undefined>(undefined);

/**
 * Provider that owns the open/close state of the attachment preview modal.
 *
 * State is intentionally minimal — the list of files to render is derived
 * from the current thread inside the modal itself (via the mailbox
 * provider). Keeping a single source of truth there avoids the modal
 * showing stale attachments when the user navigates between threads.
 */
export const AttachmentPreviewProvider = ({ children }: PropsWithChildren) => {
    const [openedFileId, setOpenedFileId] = useState<string | null>(null);
    const [overrideFiles, setOverrideFiles] = useState<FilePreviewType[] | null>(null);
    const [overrideDriveUrlById, setOverrideDriveUrlById] = useState<Map<string, string> | null>(null);

    const openPreview = useCallback((fileId: string, override?: AttachmentPreviewOverride) => {
        setOpenedFileId(fileId);
        // Reset on every open so a thread-attachment preview never inherits a
        // previous draft's override (and vice-versa).
        setOverrideFiles(override?.files ?? null);
        setOverrideDriveUrlById(override?.driveUrlById ?? null);
    }, []);

    const closePreview = useCallback(() => {
        setOpenedFileId(null);
        setOverrideFiles(null);
        setOverrideDriveUrlById(null);
    }, []);

    const value = useMemo<AttachmentPreviewContextType>(() => ({
        openPreview,
        closePreview,
        isOpen: openedFileId !== null,
        openedFileId,
        overrideFiles,
        overrideDriveUrlById,
    }), [openPreview, closePreview, openedFileId, overrideFiles, overrideDriveUrlById]);

    return (
        <AttachmentPreviewContext.Provider value={value}>
            {children}
        </AttachmentPreviewContext.Provider>
    );
};

export const useAttachmentPreview = (): AttachmentPreviewContextType => {
    const ctx = useContext(AttachmentPreviewContext);
    if (!ctx) {
        throw new Error("useAttachmentPreview must be used within an AttachmentPreviewProvider");
    }
    return ctx;
};
