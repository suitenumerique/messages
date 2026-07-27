import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Attachment } from "@/features/api/gen/models/attachment";

const attachmentItem = vi.fn();

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key, i18n: { resolvedLanguage: "en" } }),
}));

vi.mock("./attachment-item", () => ({
    AttachmentItem: (props: { canDownload: boolean; canPreview: boolean }) => {
        attachmentItem(props);
        return null;
    },
}));

import { AttachmentList } from ".";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ATTACHMENT = { name: "invoice.pdf", size: 10, created_at: "2026-01-01" } as Attachment;

describe("AttachmentList", () => {
    let container: HTMLDivElement;
    let root: Root;

    const render = (spamReport?: "message" | "thread" | null) => {
        act(() => root.render(<AttachmentList attachments={[ATTACHMENT]} spamReport={spamReport} />));
    };

    beforeEach(() => {
        vi.clearAllMocks();
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
    });

    it("lets a regular attachment be previewed and downloaded", () => {
        render();

        expect(attachmentItem).toHaveBeenCalledWith(expect.objectContaining({ canDownload: true, canPreview: true }));
        expect(container.textContent).not.toContain("reported as spam");
    });

    it.each([
        ["message", "This message has been reported as spam."],
        ["thread", "This thread has been reported as spam."],
    ] as const)("disables the attachments of a %s reported as spam", (spamReport, notice) => {
        render(spamReport);

        expect(attachmentItem).toHaveBeenCalledWith(expect.objectContaining({ canDownload: false, canPreview: false }));
        expect(container.textContent).toContain(notice);
    });
});
