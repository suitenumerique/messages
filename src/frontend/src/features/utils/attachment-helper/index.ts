import { Attachment } from "@/features/api/gen/models";
import { CALC_EXTENSIONS, MIME_TO_CATEGORY, MIME_TO_FORMAT_TRANSLATION_KEY, MIME_TO_ICON, MIME_TO_ICON_MINI, MimeCategory } from "./constants";
import { getBlobDownloadRetrieveUrl } from "@/features/api/gen/blob/blob";
import { getRequestUrl } from "@/features/api/utils";

/**
 * An helper class to handle attachments (Extract mime category, get icon, etc.)
 */
export class AttachmentHelper {
    /**
     * Get the extension of an attachment from its name
     */
    static getExtension(attachment: Attachment) {
        if (!attachment.name) return undefined;

        return attachment.name
                .split(".")
                .findLast((_, index) => index !== 0);
    }

    /**
     * Get the mime category of an attachment
     */
    static getMimeCategory(attachment: Attachment): MimeCategory {
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
    static getIcon(attachment: Attachment, mini: boolean = false) {
        const category = AttachmentHelper.getMimeCategory(attachment);
        return mini ? MIME_TO_ICON_MINI[category] : MIME_TO_ICON[category];
    }
      
    /**
     * Get the format translation key of an attachment
     */
    static getFormatTranslationKey(attachment: Attachment) {
        const category = AttachmentHelper.getMimeCategory(attachment);
        return MIME_TO_FORMAT_TRANSLATION_KEY[category];
    };

    /**
     * Build the download url of an attachment blob
     */
    static getDownloadUrl(attachment: Attachment) {
        return getRequestUrl(getBlobDownloadRetrieveUrl(attachment.blobId));
    }
}
