import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MessageTemplatePreview } from "./message-template-preview";

const renderedBodies: Record<string, string> = {
    template: "<p>Bonjour,</p>",
    signature: "<p>--<br/>User 1</p>",
};

const renderRetrieve = vi.fn(
    (_mailboxId: string, id: string, _params: unknown, options: { query: { enabled?: boolean } }) => {
        if (options.query.enabled === false) {
            return { data: undefined, isLoading: false, isError: false };
        }
        return { data: { data: { html_body: renderedBodies[id] } }, isLoading: false, isError: false };
    },
);

vi.mock("@/features/api/gen", () => ({
    useMailboxesMessageTemplatesRenderRetrieve: (...args: Parameters<typeof renderRetrieve>) => renderRetrieve(...args),
}));

vi.mock("@/features/blocknote/image-block/use-html-with-object-urls", () => ({
    useHtmlWithObjectUrls: (html: string | null) => html,
}));

vi.mock("@gouvfr-lasuite/ui-components", () => ({
    Spinner: () => null,
}));

// The real hook suspends until the translation resources are loaded.
vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

describe("MessageTemplatePreview", () => {
    let container: HTMLDivElement;
    let root: Root;

    beforeEach(() => {
        (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
        renderRetrieve.mockClear();
    });

    const renderPreview = (signatureId: string | null) => {
        act(() => {
            root.render(
                <MessageTemplatePreview
                    mailboxId="mailbox-1"
                    templateId="template"
                    signatureId={signatureId}
                    messageId="draft-1"
                />,
            );
        });
        return container.querySelector(".message-template-preview")?.innerHTML;
    };

    it("appends the template signature, rendered for the draft", () => {
        expect(renderPreview("signature")).toBe("<p>Bonjour,</p><p>--<br>User 1</p>");
        expect(renderRetrieve).toHaveBeenCalledWith(
            "mailbox-1",
            "signature",
            { message_id: "draft-1" },
            expect.objectContaining({ query: expect.objectContaining({ enabled: true }) }),
        );
    });

    it("renders the template alone when it has no signature", () => {
        expect(renderPreview(null)).toBe("<p>Bonjour,</p>");
    });
});
