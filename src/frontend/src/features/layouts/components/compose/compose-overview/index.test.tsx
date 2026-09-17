import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ComposeWindowDescriptor } from "@/features/providers/compose-windows/types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const focusWindow = vi.fn();
const onClose = vi.fn();
const onRequestCloseWindow = vi.fn();

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/features/ui/components/icon", () => ({
    Icon: ({ name }: { name?: string }) => <span data-icon={name ?? "svg"} />,
}));

vi.mock("@/features/providers/compose-windows", () => ({
    useComposeWindows: () => ({ focusWindow }),
}));

vi.mock("../use-compose-sender", () => ({
    useComposeSender: () => () => ({ email: "alice@example.local" }),
}));

import { ComposeOverview } from ".";

const windows: ComposeWindowDescriptor[] = ["w1", "w2"].map((windowId) => ({
    windowId,
    mailboxId: "m1",
    mode: "new",
    presentation: "docked",
    isMinimized: true,
    openedOnExistingDraft: false,
    focusTick: 0,
    title: `Draft ${windowId}`,
}));

let container: HTMLDivElement;
let root: Root;

/** The stack bar opens the overview and keeps existing behind it. */
const Host = ({ open }: { open: boolean }) => (
    <>
        <button type="button" id="stack-bar" />
        {open && (
            <ComposeOverview windows={windows} onClose={onClose} onRequestCloseWindow={onRequestCloseWindow} />
        )}
    </>
);

const render = (open: boolean) =>
    act(() => {
        root.render(<Host open={open} />);
    });

const openOverview = () => {
    render(false);
    document.getElementById("stack-bar")!.focus();
    render(true);
};

const keyDown = (key: string, shiftKey = false) => {
    const event = new KeyboardEvent("keydown", { key, shiftKey, bubbles: true, cancelable: true });
    act(() => {
        document.activeElement!.dispatchEvent(event);
    });
    return event;
};

const dialog = () => document.querySelector<HTMLElement>("[role='dialog']")!;
const cards = () => Array.from(dialog().querySelectorAll<HTMLElement>(".compose-overview__card"));

beforeEach(() => {
    focusWindow.mockClear();
    onClose.mockClear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => {
        root.unmount();
    });
    document.body.innerHTML = "";
});

describe("ComposeOverview", () => {
    it("is a modal dialog that takes the focus on its first card", () => {
        openOverview();

        expect(dialog().getAttribute("aria-modal")).toBe("true");
        expect(document.activeElement).toBe(cards()[0]);
    });

    it("keeps Tab inside: the last close button wraps to the first card", () => {
        openOverview();
        const closeButtons = dialog().querySelectorAll<HTMLElement>("[aria-label='Close']");
        closeButtons[closeButtons.length - 1].focus();

        const event = keyDown("Tab");

        expect(event.defaultPrevented).toBe(true);
        expect(document.activeElement).toBe(cards()[0]);
    });

    it("closes on Escape from inside, then hands the focus back to the stack bar", async () => {
        openOverview();

        keyDown("Escape");
        expect(onClose).toHaveBeenCalledTimes(1);

        render(false);
        await act(() => Promise.resolve());
        expect(document.activeElement).toBe(document.getElementById("stack-bar"));
    });

    it("resumes a window from its card", () => {
        openOverview();

        act(() => {
            cards()[1].dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });

        expect(focusWindow).toHaveBeenCalledWith("w2");
        expect(onClose).toHaveBeenCalledTimes(1);
    });
});
