import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReadMessageTemplate } from "@/features/api/gen";
import { RECENT_MESSAGE_TEMPLATES_KEY } from "@/features/config/constants";
import { MessageTemplatePicker } from "./message-template-picker";
import { MAX_RECENT_MESSAGE_TEMPLATES } from "./use-recent-message-templates";

// The real hook suspends until the translation resources are loaded.
vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@gouvfr-lasuite/ui-components", () => ({
    IconSize: {},
    IconType: {},
}));

vi.mock("@/features/ui/components/icon", () => ({
    Icon: () => null,
}));

vi.mock("./message-template-preview", () => ({
    MessageTemplatePreview: ({ templateId }: { templateId: string }) => (
        <div className="preview-stub">{templateId}</div>
    ),
}));

const buildTemplate = (id: string, name: string) => ({ id, name }) as ReadMessageTemplate;

const templates = [
    buildTemplate("refund", "Remboursement"),
    buildTemplate("welcome", "Accueil"),
    buildTemplate("closing", "Clôture du ticket"),
];

const MAILBOX_ID = "mailbox-1";

const readRecents = () =>
    JSON.parse(sessionStorage.getItem(RECENT_MESSAGE_TEMPLATES_KEY) ?? "{}")[MAILBOX_ID];

describe("MessageTemplatePicker", () => {
    let container: HTMLDivElement;
    let root: Root;

    beforeEach(() => {
        (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
        // jsdom lacks CSS.escape, used by react-aria to scroll to a focused row.
        vi.stubGlobal("CSS", { escape: (value: string) => value });
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
        sessionStorage.clear();
        vi.unstubAllGlobals();
    });

    const renderPicker = (onSelect = vi.fn()) => {
        act(() => {
            root.render(
                <MessageTemplatePicker
                    mailboxId={MAILBOX_ID}
                    templates={templates}
                    onSelect={onSelect}
                    showPreview
                />,
            );
        });
        const rows = Array.from(container.querySelectorAll<HTMLElement>(".message-template-picker__item"));
        const headers = Array.from(container.querySelectorAll(".message-template-picker__section-header"));
        return {
            keys: rows.map((row) => row.dataset.key),
            rows: Object.fromEntries(rows.map((row) => [row.dataset.key, row])),
            headers: headers.map((header) => header.textContent),
            preview: container.querySelector(".message-template-picker__preview")?.textContent,
        };
    };

    it("lists templates by name without section when nothing was used yet", () => {
        const { keys, headers } = renderPicker();
        expect(keys).toEqual(["welcome", "closing", "refund"]);
        expect(headers).toEqual([]);
    });

    it("invites to hover a template until one is focused", () => {
        const { preview } = renderPicker();
        expect(preview).toBe("PreviewHover a template to preview it");
    });

    it("lists the recently used templates first and skips the ones that no longer exist", () => {
        sessionStorage.setItem(
            RECENT_MESSAGE_TEMPLATES_KEY,
            JSON.stringify({ [MAILBOX_ID]: ["refund", "deleted"], "other-mailbox": ["welcome"] }),
        );
        const { keys, headers } = renderPicker();
        expect(keys).toEqual(["refund", "welcome", "closing"]);
        expect(headers).toEqual(["Recently used", "Other templates"]);
    });

    it("ignores a corrupted storage value", () => {
        sessionStorage.setItem(RECENT_MESSAGE_TEMPLATES_KEY, "not json");
        const { keys } = renderPicker();
        expect(keys).toEqual(["welcome", "closing", "refund"]);
    });

    it("selects a template and remembers it as the most recent one", () => {
        sessionStorage.setItem(
            RECENT_MESSAGE_TEMPLATES_KEY,
            JSON.stringify({ [MAILBOX_ID]: ["welcome", "refund"] }),
        );
        const onSelect = vi.fn();
        const { rows } = renderPicker(onSelect);

        act(() => rows.refund.click());

        expect(onSelect).toHaveBeenCalledWith("refund");
        expect(readRecents()).toEqual(["refund", "welcome"]);
    });

    it("keeps a bounded number of recent templates", () => {
        const previous = Array.from({ length: MAX_RECENT_MESSAGE_TEMPLATES }, (_, index) => `old-${index}`);
        sessionStorage.setItem(RECENT_MESSAGE_TEMPLATES_KEY, JSON.stringify({ [MAILBOX_ID]: previous }));
        const { rows } = renderPicker();

        act(() => rows.closing.click());

        expect(readRecents()).toEqual(["closing", ...previous.slice(0, MAX_RECENT_MESSAGE_TEMPLATES - 1)]);
    });
});
