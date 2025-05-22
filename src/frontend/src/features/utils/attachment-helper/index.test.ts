import { describe, it, expect, vi } from 'vitest';
import { Attachment } from "@/features/api/gen/models";
import { AttachmentHelper } from "./index";
import { MimeCategory } from "./constants";
import { getBlobDownloadRetrieveUrl } from "@/features/api/gen/blob/blob";
import { getRequestUrl } from "@/features/api/utils";

// Mock the external dependencies
vi.mock("@/features/api/gen/blob/blob");
vi.mock("@/features/api/utils");

describe("AttachmentHelper", () => {
    describe("getExtension", () => {
        it("should return undefined when attachment has no name", () => {
            const attachment = { name: '' } as Attachment;
            expect(AttachmentHelper.getExtension(attachment)).toBeUndefined();
        });

        it.each([
            { name: 'document.pdf', expected: 'pdf' },
            { name: 'document.pdf.zip', expected: 'zip' },
            { name: 'document', expected: undefined },
        ])("should return the correct extension from filename", ({ name, expected }) => {
            const attachment = { name } as Attachment;
            expect(AttachmentHelper.getExtension(attachment)).toBe(expected);
        });
    });

    describe("getMimeCategory", () => {
        it("should return CALC category for calc files with zip mimetype", () => {
            const attachment = { type: "application/zip", name: "spreadsheet.xlsx" } as Attachment;
            expect(AttachmentHelper.getMimeCategory(attachment)).toBe(MimeCategory.CALC);
        });

        it("should return PDF category for pdf mimetype", () => {
            const attachment = { type: "application/pdf", name: "document.pdf" } as Attachment;
            expect(AttachmentHelper.getMimeCategory(attachment)).toBe(MimeCategory.PDF);
        });

        it("should return DOC category for doc mimetype", () => {
            const attachment = {
                type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                name: "document.docx"
            } as Attachment;
            expect(AttachmentHelper.getMimeCategory(attachment)).toBe(MimeCategory.DOC);
        });
        

        it("should return IMAGE category for image mimetypes", () => {
            const attachment = { type: "image/jpeg", name: "image.jpg" } as Attachment;
            expect(AttachmentHelper.getMimeCategory(attachment)).toBe(MimeCategory.IMAGE);
        });

        it("should return AUDIO category for audio mimetypes", () => {
            const attachment = { type: "audio/mp3", name: "audio.mp3" } as Attachment;
            expect(AttachmentHelper.getMimeCategory(attachment)).toBe(MimeCategory.AUDIO);
        });

        it("should return VIDEO category for video mimetypes", () => {
            const attachment = { type: "video/mp4", name: "video.mp4" } as Attachment;
            expect(AttachmentHelper.getMimeCategory(attachment)).toBe(MimeCategory.VIDEO);
        });

        it("should return OTHER category for unknown mimetypes", () => {
            const attachment = { type: "application/unknown", name: "file.unknown" } as Attachment;
            expect(AttachmentHelper.getMimeCategory(attachment)).toBe(MimeCategory.OTHER);
        });
    });

    describe("getIcon", () => {
        it("should return mini icon when mini parameter is true", () => {
            const attachment = { type: "application/pdf", name: "document.pdf" } as Attachment;
            const result = AttachmentHelper.getIcon(attachment, true);
            expect(result).toMatchInlineSnapshot(`"/images/files/icons/mime-pdf-mini.svg"`);
        });

        it("should return regular icon when mini parameter is false", () => {
            const attachment = { type: "application/pdf", name: "document.pdf" } as Attachment;
            const result = AttachmentHelper.getIcon(attachment, false);
            expect(result).toMatchInlineSnapshot(`"/images/files/icons/mime-pdf.svg"`);
        });
    });

    describe("getFormatTranslationKey", () => {
        it("should return correct translation key for attachment category", () => {
            const attachment = { type: "application/pdf", name: "document.pdf" } as Attachment;
            const result = AttachmentHelper.getFormatTranslationKey(attachment);
            expect(result).toMatchInlineSnapshot(`"mime.pdf"`);
        });
    });

    describe("getDownloadUrl", () => {
        it("should return correct download URL", () => {
            const mockUrl = "http://example.com/api/v1.0/blob/123/download/";
            const attachment = {
                type: "application/pdf",
                name: "document.pdf",
                blobId: "123"
            } as Attachment;

            vi.mocked(getBlobDownloadRetrieveUrl).mockReturnValue(mockUrl);
            vi.mocked(getRequestUrl).mockReturnValue(mockUrl);

            const result = AttachmentHelper.getDownloadUrl(attachment);
            
            expect(getBlobDownloadRetrieveUrl).toHaveBeenCalledWith(attachment.blobId);
            expect(getRequestUrl).toHaveBeenCalledWith(mockUrl);
            expect(result).toBe(mockUrl);
        });
    });

    describe("getFormattedSize", () => {
        it("should format size in bytes", () => {
            expect(AttachmentHelper.getFormattedSize(500)).toBe("500B");
        });

        it("should format size in kilobytes", () => {
            expect(AttachmentHelper.getFormattedSize(1500)).toBe("1.5KB");
        });

        it("should format size in megabytes", () => {
            expect(AttachmentHelper.getFormattedSize(1500000)).toBe("1.5MB");
        });

        it("should format size in gigabytes", () => {
            expect(AttachmentHelper.getFormattedSize(1500000000)).toBe("1.5BB");
        });

        it("should use specified language for formatting", () => {
            // French uses comma as decimal separator
            expect(AttachmentHelper.getFormattedSize(1500, 'fr')).toBe("1,5 ko");
        });
    });

    describe("getFormattedTotalSize", () => {
        it("should calculate total size of multiple attachments", () => {
            const attachments = [
                { size: 1000 } as Attachment,
                { size: 2000 } as Attachment,
                { size: 3000 } as Attachment
            ];
            expect(AttachmentHelper.getFormattedTotalSize(attachments)).toBe("6KB");
        });

        it("should handle empty array of attachments", () => {
            expect(AttachmentHelper.getFormattedTotalSize([])).toBe("0B");
        });

        it("should use specified language for formatting", () => {
            const attachments = [
                { size: 1500 } as Attachment,
                { size: 2500 } as Attachment
            ];
            expect(AttachmentHelper.getFormattedTotalSize(attachments, 'fr')).toBe("4 ko");
        });
    });
}); 
