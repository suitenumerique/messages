import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/features/ui/components/icon", () => ({
    Icon: ({ name }: { name?: string }) => <span data-icon={name ?? "svg"} />,
}));

import { Drawer, ModalDrawer } from ".";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
const onClose = vi.fn();

const Items = () => (
    <div className="drawer-list">
        <button type="button" className="drawer-list__item" aria-label="First" />
        <button type="button" className="drawer-list__item" aria-label="Second" />
    </div>
);

/** A trigger owning a modal drawer, as the mobile thread toolbar does. */
const ModalHost = () => {
    const [isOpen, setIsOpen] = useState(false);
    return (
        <>
            <button type="button" aria-label="Open" onClick={() => setIsOpen(true)} />
            {isOpen && (
                <ModalDrawer
                    title="More options"
                    onClose={() => {
                        onClose();
                        setIsOpen(false);
                    }}
                >
                    <Items />
                </ModalDrawer>
            )}
        </>
    );
};

const render = (element: React.ReactNode) => {
    act(() => {
        root.render(element);
    });
};

const click = (el: Element) => {
    act(() => {
        el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
};

const keyDown = (el: Element, key: string) => {
    act(() => {
        el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
    });
};

/** A tap: pointer down, up, then the click a finger produces. */
const tap = (el: Element) => {
    const Ctor = typeof PointerEvent === "undefined" ? MouseEvent : PointerEvent;
    const [down, up] =
        Ctor === MouseEvent ? ["mousedown", "mouseup"] : ["pointerdown", "pointerup"];
    act(() => {
        el.dispatchEvent(new Ctor(down, { bubbles: true, button: 0 }));
        el.dispatchEvent(new Ctor(up, { bubbles: true, button: 0 }));
        el.dispatchEvent(new MouseEvent("click", { bubbles: true, button: 0 }));
    });
};

/**
 * react-aria defers focus moves to the next frame: the dialog's initial focus
 * when the opener was activated without a pointer (keyboard, or a synthetic
 * click as here), and the restoration on close. The frame has to elapse
 * before the focus can be asserted.
 */
const nextFrame = () =>
    act(
        () =>
            new Promise<void>((resolve) => {
                requestAnimationFrame(() => resolve());
            }),
    );

/** The end of the sheet's slide-out, which is what fires `onClose`. */
const endSlideOut = () => {
    const sheet = document.querySelector(".drawer__sheet")!;
    const event = new Event("transitionend", { bubbles: true });
    Object.defineProperty(event, "propertyName", { value: "transform" });
    act(() => {
        sheet.dispatchEvent(event);
    });
};

const dialog = () => document.querySelector<HTMLElement>("[role='dialog']");
const sheet = () => document.querySelector<HTMLElement>(".drawer__sheet");
const trigger = () => container.querySelector<HTMLElement>("button")!;

const openModal = async () => {
    render(<ModalHost />);
    // A synthetic click does not focus the button as a real one would, and
    // the focus restoration targets whatever was focused at opening time.
    act(() => trigger().focus());
    click(trigger());
    await nextFrame();
};

beforeEach(() => {
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

describe("Drawer", () => {
    it("is a labelled group, not a dialog: the focus must stay where it is", () => {
        const before = document.activeElement;
        render(
            <Drawer title="Format" onClose={onClose}>
                <Items />
            </Drawer>,
        );

        const group = container.querySelector<HTMLElement>("[role='group']")!;
        const title = container.querySelector<HTMLElement>("h2")!;
        expect(group.getAttribute("aria-labelledby")).toBe(title.id);
        expect(title.textContent).toBe("Format");
        expect(dialog()).toBeNull();
        expect(document.activeElement).toBe(before);
    });

    it("closes from its own button once the slide-out has ended", () => {
        render(
            <Drawer title="Format" onClose={onClose}>
                <Items />
            </Drawer>,
        );

        click(container.querySelector("[aria-label='Close']")!);
        expect(sheet()!.classList.contains("drawer__sheet--closing")).toBe(true);
        expect(onClose).not.toHaveBeenCalled();

        endSlideOut();
        expect(onClose).toHaveBeenCalledTimes(1);
    });
});

describe("ModalDrawer", () => {
    it("is a dialog labelled by its title, rendered outside the opener's tree", async () => {
        await openModal();

        const modal = dialog()!;
        expect(container.contains(modal)).toBe(false);
        const title = modal.querySelector<HTMLElement>("h2")!;
        expect(modal.getAttribute("aria-labelledby")).toBe(title.id);
        expect(title.textContent).toBe("More options");
    });

    it("moves the focus inside on open and keeps Tab cycling within", async () => {
        await openModal();

        const modal = dialog()!;
        expect(modal.contains(document.activeElement)).toBe(true);

        const items = modal.querySelectorAll<HTMLElement>(".drawer-list__item");
        const last = items[items.length - 1];
        act(() => last.focus());
        keyDown(last, "Tab");

        expect(modal.contains(document.activeElement)).toBe(true);
        expect(document.activeElement).not.toBe(last);
    });

    it("closes on Escape through the slide-out, then hands the focus back to the opener", async () => {
        await openModal();

        keyDown(document.activeElement!, "Escape");
        expect(sheet()!.classList.contains("drawer__sheet--closing")).toBe(true);
        expect(dialog()).not.toBeNull();
        expect(onClose).not.toHaveBeenCalled();

        endSlideOut();
        expect(onClose).toHaveBeenCalledTimes(1);
        expect(dialog()).toBeNull();
        await nextFrame();
        expect(document.activeElement).toBe(trigger());
    });

    it("closes on a tap on the scrim", async () => {
        await openModal();

        tap(document.querySelector(".modal-drawer")!);
        expect(sheet()!.classList.contains("drawer__sheet--closing")).toBe(true);

        endSlideOut();
        expect(onClose).toHaveBeenCalledTimes(1);
        expect(dialog()).toBeNull();
    });

    it("ignores taps inside the sheet", async () => {
        await openModal();

        tap(dialog()!.querySelector(".drawer-list__item")!);
        expect(sheet()!.classList.contains("drawer__sheet--closing")).toBe(false);
    });
});
