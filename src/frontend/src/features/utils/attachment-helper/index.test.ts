import { describe, it, expect, vi } from 'vitest';
import { Attachment } from "@/features/api/gen/models";
import { DriveFile } from "@/features/forms/components/message-form/drive-attachment-picker";
import { AttachmentHelper } from "./index";
import { MimeCategory } from "./constants";
import { getBlobDownloadRetrieveUrl, getBlobPreviewRetrieveUrl } from "@/features/api/gen/blob/blob";
import { getRequestUrl } from "@/features/api/utils";
import { toNativeMediaUrl } from "@/features/native/media-url";

// Mock the external dependencies
vi.mock("@/features/api/gen/blob/blob");
vi.mock("@/features/api/utils");
vi.mock("@/features/native/media-url");

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

    describe("getIdentity", () => {
        it("should namespace blob and drive identities so they cannot collide", () => {
            const attachment = { blobId: "123", name: "a.pdf" } as Attachment;
            const driveFile = { id: "123", url: "https://drive/123", name: "a.pdf" } as DriveFile;

            expect(AttachmentHelper.getIdentity(attachment)).toBe("blob:123");
            expect(AttachmentHelper.getIdentity(driveFile)).toBe("drive:123");
            expect(AttachmentHelper.getIdentity(attachment)).not.toBe(AttachmentHelper.getIdentity(driveFile));
        });
    });

    describe("dedupe", () => {
        it("should keep the first entry when the same blob is attached twice", () => {
            const first = { blobId: "123", name: "a.pdf", created_at: "2024-01-01T00:00:00Z" } as Attachment;
            const again = { blobId: "123", name: "a.pdf", created_at: "2024-01-02T00:00:00Z" } as Attachment;
            const other = { blobId: "456", name: "b.pdf", created_at: "2024-01-03T00:00:00Z" } as Attachment;

            expect(AttachmentHelper.dedupe([first, again, other])).toEqual([first, other]);
        });

        it("should promote an existing entry to inline when the same blob is pasted in the body", () => {
            const plain = { blobId: "123", name: "a.png", cid: null, created_at: "2024-01-01T00:00:00Z" } as Attachment;
            const inline = { blobId: "123", name: "a.png", cid: "123", created_at: "2024-01-02T00:00:00Z" } as Attachment;

            expect(AttachmentHelper.dedupe([plain, inline])).toEqual([{ ...plain, cid: "123" }]);
            expect(AttachmentHelper.dedupe([inline, plain])).toEqual([inline]);
        });

        it("should keep the first entry when the same drive file is picked twice", () => {
            const first = { id: "d1", url: "https://drive/d1", name: "a.pdf" } as DriveFile;
            const again = { id: "d1", url: "https://drive/d1", name: "a.pdf" } as DriveFile;
            const attachment = { blobId: "d1", name: "a.pdf" } as Attachment;

            expect(AttachmentHelper.dedupe([first, again, attachment])).toEqual([first, attachment]);
        });
    });

    describe("toFilePreviewType", () => {
        it("should route only the preview url through the native media rewrite", () => {
            const attachment = {
                type: "image/jpeg",
                name: "photo.jpg",
                blobId: "123",
                size: 42,
            } as Attachment;
            vi.mocked(getBlobPreviewRetrieveUrl).mockReturnValue("/blob/123/preview/");
            vi.mocked(getBlobDownloadRetrieveUrl).mockReturnValue("/blob/123/download/");
            vi.mocked(getRequestUrl).mockImplementation((path) => `http://api.test${path}`);
            vi.mocked(toNativeMediaUrl).mockImplementation((url) => `native:${url}`);

            const result = AttachmentHelper.toFilePreviewType(attachment);

            expect(result).toEqual({
                id: "123",
                size: 42,
                title: "photo.jpg",
                mimetype: "image/jpeg",
                url_preview: "native:http://api.test/blob/123/preview/",
                url: "http://api.test/blob/123/download/",
                isSuspicious: false,
            });
        });
    });

    describe("getFormattedSize", () => {
        it("should format size in bytes", () => {
            expect(AttachmentHelper.getFormattedSize(500)).toBe("500B");
        });

        it("should format size in kilobytes", () => {
            expect(AttachmentHelper.getFormattedSize(1500)).toBe("1.5kB");
        });

        it("should format size in megabytes", () => {
            expect(AttachmentHelper.getFormattedSize(1500*1024)).toBe("1.5MB");
        });

        it("should format size in gigabytes", () => {
            expect(AttachmentHelper.getFormattedSize(1500*1024*1024)).toBe("1.5GB");
        });

        it("should use specified language for formatting", () => {
            // French uses comma as decimal separator
            expect(AttachmentHelper.getFormattedSize(1500, 'fr')).toBe("1,5ko");
        });
    });

    describe("getFormattedTotalSize", () => {
        it("should calculate total size of multiple attachments", () => {
            const attachments = [
                { size: 1024 } as Attachment,
                { size: 2*1024 } as Attachment,
                { size: 3*1024 } as Attachment
            ];
            expect(AttachmentHelper.getFormattedTotalSize(attachments)).toBe("6kB");
        });

        it("should handle empty array of attachments", () => {
            expect(AttachmentHelper.getFormattedTotalSize([])).toBe("0B");
        });

        it("should use specified language for formatting", () => {
            const attachments = [
                { size: 1*1024 } as Attachment,
                { size: 3*1024 } as Attachment
            ];
            expect(AttachmentHelper.getFormattedTotalSize(attachments, 'fr')).toBe("4ko");
        });
    });
}); 
