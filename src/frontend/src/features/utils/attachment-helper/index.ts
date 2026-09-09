import type { FilePreviewType } from "@gouvfr-lasuite/ui-kit";
import { Attachment } from "@/features/api/gen/models";
import { CALC_EXTENSIONS, MIME_TO_CATEGORY, MIME_TO_FORMAT_TRANSLATION_KEY, MIME_TO_ICON, MIME_TO_ICON_MINI, MimeCategory } from "./constants";
import { getBlobDownloadRetrieveUrl, getBlobPreviewRetrieveUrl } from "@/features/api/gen/blob/blob";
import { getRequestUrl } from "@/features/api/utils";
import { toNativeMediaUrl } from "@/features/native/media-url";
import { DriveFile } from "@/features/forms/components/message-form/drive-attachment-picker";

/**
 * Minimal shape needed to classify an attachment by mime type. ``Attachment``,
 * ``DriveFile`` and the DOM ``File`` all match it, but it also lets callers
 * adapt foreign shapes (e.g. the kit's ``FilePreviewType``) without a cast.
 */
type MimeFileLike = { name?: string; type: string };

/**
 * An helper class to handle attachments (Extract mime category, get icon, etc.)
 */
export class AttachmentHelper {
    /**
     * Get the extension of an attachment from its name
     */
    static getExtension(attachment: MimeFileLike) {
        if (!attachment.name) return undefined;

        return attachment.name
                .split(".")
                .findLast((_, index) => index !== 0);
    }

    /**
     * Get the mime category of an attachment
     */
    static getMimeCategory(attachment: MimeFileLike): MimeCategory {
        // Special case: some calc files have application/zip mimetype. For those we should check their extension too.
        // Otherwise they will be shown as zip files.
        const extension = AttachmentHelper.getExtension(attachment);
        if (
            attachment.type === "application/zip" &&
            extension && CALC_EXTENSIONS.includes(extension)
        ) {
            return MimeCategory.CALC;
        }
        if (MIME_TO_CATEGORY.hasOwnProperty(attachment.type)) return MIME_TO_CATEGORY[attachment.type];
        if (attachment.type.startsWith("image/")) return MimeCategory.IMAGE;
        if (attachment.type.startsWith("audio/")) return MimeCategory.AUDIO;
        if (attachment.type.startsWith("video/")) return MimeCategory.VIDEO;
        return MimeCategory.OTHER;
    }

    /**
     * Get the icon of an attachment
     */
    static getIcon(attachment: MimeFileLike, mini: boolean = false) {
        const category = AttachmentHelper.getMimeCategory(attachment);
        return mini ? MIME_TO_ICON_MINI[category] : MIME_TO_ICON[category];
    }

    /**
     * Get the format translation key of an attachment
     */
    static getFormatTranslationKey(attachment: MimeFileLike) {
        const category = AttachmentHelper.getMimeCategory(attachment);
        return MIME_TO_FORMAT_TRANSLATION_KEY[category];
    };

    /**
     * Stable identity of a composer entry. Blobs are content-addressed
     * server-side (same file → same ``blobId``) and the draft endpoint
     * upserts on ``(blob, message)``, so two entries sharing an identity
     * are the same attachment and must be collapsed.
     */
    static getIdentity(attachment: DriveFile | Attachment) {
        if ('blobId' in attachment) return `blob:${attachment.blobId}`;
        return `drive:${attachment.id}`;
    }

    /**
     * Collapse entries sharing an identity, keeping the first position.
     * An inline usage (``cid``) wins over a plain one: the server stores a
     * single row per blob and the outbound composer only emits a
     * Content-ID part when that row carries a ``cid``, so dropping it
     * would leave the body pointing at an image that is never attached.
     */
    static dedupe<T extends DriveFile | Attachment>(attachments: readonly T[]): T[] {
        const byIdentity = new Map<string, T>();
        for (const attachment of attachments) {
            const identity = AttachmentHelper.getIdentity(attachment);
            const existing = byIdentity.get(identity);
            if (!existing) {
                byIdentity.set(identity, attachment);
            } else if (!('cid' in existing && existing.cid) && 'cid' in attachment && attachment.cid) {
                byIdentity.set(identity, { ...existing, cid: attachment.cid });
            }
        }
        return [...byIdentity.values()];
    }

    /**
     * Build the download url of an attachment blob
     */
    static getDownloadUrl(attachment: DriveFile | Attachment) {
        if ('blobId' in attachment) {
            return getRequestUrl(getBlobDownloadRetrieveUrl(attachment.blobId));
        }
        return attachment.url;
    }

    /**
     * Map a Messages attachment to the shape expected by the kit's
     * FilePreview component. The id is stable per attachment so the
     * viewer can resolve which file to open via ``openedFileId``.
     *
     * The viewer loads ``url_preview`` as a plain ``<img>``/``<video>``
     * subresource, which carries no session inside the native shell — hence
     * the rewrite (see ``toNativeMediaUrl``). ``url`` stays raw: it is
     * consumed by ``nativeDownloadFile`` through ``CapacitorHttp``, which
     * already has the session and must not be pointed at the proxy.
     */
    static toFilePreviewType(attachment: Attachment): FilePreviewType {
        return {
            id: attachment.blobId,
            size: attachment.size,
            title: attachment.name,
            mimetype: attachment.type,
            url_preview: toNativeMediaUrl(getRequestUrl(getBlobPreviewRetrieveUrl(attachment.blobId))),
            url: getRequestUrl(getBlobDownloadRetrieveUrl(attachment.blobId)),
            isSuspicious: false,
        };
    }

    /**
     * Map a Drive attachment to FilePreviewType.
     *
     * ``url_preview`` points straight at Drive's own preview endpoint
     * (``{drive}/media/preview/item/{id}/{name}``): files attached from
     * Drive are shared through a public link, so the viewer loads it
     * cross-origin without any Drive session (Drive must still accept CORS
     * from Messages' origin for the PDF viewer's fetch). When Drive isn't
     * configured the URL is empty; the kit has no fallback for that and
     * renders an empty viewer.
     *
     * ``url`` keeps the Drive permalink so the modal's "Open in Drive"
     * header action keeps working.
     */
    static driveFileToFilePreviewType(file: DriveFile, drivePreviewBaseUrl: string): FilePreviewType {
        return {
            id: file.id,
            size: file.size,
            title: file.name,
            mimetype: file.type,
            url_preview: drivePreviewBaseUrl
                ? `${drivePreviewBaseUrl}/${file.id}/${encodeURIComponent(file.name)}`
                : "",
            url: file.url,
            isSuspicious: false,
        };
    }

    static getFormattedSize(size: number, language: string = 'en') {
        // Determine the appropriate unit using binary (1024) calculation
        const units: Array<{ divisor: number; unit: Intl.NumberFormatOptions['unit'] }> = [
            { divisor: 1024 ** 4, unit: 'terabyte' },
            { divisor: 1024 ** 3, unit: 'gigabyte' },
            { divisor: 1024 ** 2, unit: 'megabyte' },
            { divisor: 1024, unit: 'kilobyte' },
            { divisor: 1, unit: 'byte' },
        ];

        for (const { divisor, unit } of units) {
            if (size >= divisor) {
                const value = size / divisor;
                const formatter = new Intl.NumberFormat(language, {
                    notation: "compact",
                    style: "unit",
                    unit: unit,
                    unitDisplay: "narrow",
                });
                return formatter.format(value);
            }
        }

        // Fallback for 0 bytes
        const formatter = new Intl.NumberFormat(language, {
            notation: "compact",
            style: "unit",
            unit: "byte",
            unitDisplay: "narrow",
        });
        return formatter.format(size);
    }

    static getFormattedTotalSize(attachments: readonly (DriveFile | Attachment | File)[], language: string = 'en') {
        const totalSize = attachments.reduce((acc, attachment) => acc + attachment.size, 0);
        return AttachmentHelper.getFormattedSize(totalSize, language);
    }
}
