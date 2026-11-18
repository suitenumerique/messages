import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ContextMenuProvider, CunninghamProvider, ModalProvider, useModals } from "@gouvfr-lasuite/ui-components";
import { ComboBox } from ".";
import EventHelper from "@/features/utils/event-helper";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Types `text` in `input` as a user would. Assigning `input.value` directly
 * goes through the setter React tracks, which then sees no change and never
 * calls onChange.
 */
const typeInto = async (input: HTMLInputElement, text: string) => {
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(async () => {
        setValue.call(input, text);
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });
};

/**
 * jsdom has no `Touch` constructor, so the `touches` list the long press hook
 * reads is pinned onto a plain event.
 */
const touch = (target: Element, type: "touchstart" | "touchend") => {
    const event = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(event, "touches", {
        value: type === "touchstart" ? [{ clientX: 10, clientY: 10 }] : [],
    });
    act(() => {
        target.dispatchEvent(event);
    });
    return event;
};

/** The finger leaves the screen, wherever the pointer event lands. */
const pointerUp = () => {
    act(() => {
        document.body.dispatchEvent(new Event("pointerup", { bubbles: true }));
    });
};

/**
 * Long-presses `target`. With `androidFocusLoss`, replays what Android does
 * (seen on a Pixel and a Huawei P30 Pro): it takes the focus away from that
 * element as soon as it recognizes its own long press, slightly before ours
 * fires, and sends no `touchend` afterwards, only a `pointerup` on whatever
 * lies under the finger by then.
 */
const longPress = (target: Element, { androidFocusLoss }: { androidFocusLoss?: HTMLElement } = {}) => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    touch(target, "touchstart");
    if (androidFocusLoss) {
        act(() => {
            vi.advanceTimersByTime(490);
        });
        act(() => {
            androidFocusLoss.blur();
        });
        act(() => {
            vi.advanceTimersByTime(10);
        });
    } else {
        act(() => {
            vi.advanceTimersByTime(500);
        });
    }
    vi.useRealTimers();
    pointerUp();
    if (!androidFocusLoss) touch(target, "touchend");
};

/** A short tap: the touch sequence, then the click a browser derives from it. */
const tap = async (target: HTMLElement) => {
    touch(target, "touchstart");
    pointerUp();
    touch(target, "touchend");
    await act(async () => {
        target.click();
    });
};

const contextMenuItems = () =>
    Array.from(document.querySelectorAll<HTMLElement>("[data-testid^='context-menu-item-']"));

const clickContextMenuItem = async (item: HTMLElement) => {
    await act(async () => {
        for (const type of ["mousedown", "mouseup", "click"]) {
            item.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, detail: 1 }));
        }
    });
};

// Translations load over HTTP, which suspends `useTranslation` forever in
// jsdom; the labels themselves are not what is under test here.
vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

const WARNING = "Accented characters before the @: delivery is not guaranteed.";

type RenderOptions = {
    getItemWarning?: (value: string) => string | undefined;
    className?: string;
    variant?: "floating" | "inline";
    actions?: React.ReactNode;
};

const render = async ({ getItemWarning, className, variant, actions }: RenderOptions = {}) => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    await act(async () => {
        createRoot(container).render(
            <CunninghamProvider>
            <ContextMenuProvider>
                <ComboBox
                    label="To"
                    options={[]}
                    value={["josé@example.com", "john@example.com"]}
                    getItemWarning={getItemWarning}
                    className={className}
                    variant={variant}
                    actions={actions}
                />
            </ContextMenuProvider>
            </CunninghamProvider>
        );
    });
    return container;
};

describe("ComboBox chip warnings", () => {
    it("marks only the chips the warning callback flags", async () => {
        const container = await render({ getItemWarning: (value) => (value.startsWith("josé") ? WARNING : undefined) });

        const chips = Array.from(container.querySelectorAll(".c__combobox__chip"));
        expect(chips).toHaveLength(2);
        expect(chips[0].classList.contains("c__combobox__chip--warning")).toBe(true);
        expect(chips[1].classList.contains("c__combobox__chip--warning")).toBe(false);

        // The warning is exposed to assistive technologies, not only as a
        // hover tooltip: touch devices have no hover.
        const marker = chips[0].querySelector('[role="img"]');
        expect(marker?.getAttribute("aria-label")).toBe(WARNING);
        expect(chips[1].querySelector('[role="img"]')).toBeNull();
    });

    it("keeps its root classes when given a className", async () => {
        const container = await render({ className: "extra" });

        const root = container.querySelector(".c__combobox");
        expect(root).not.toBeNull();
        expect(root?.classList.contains("extra")).toBe(true);
    });

    it("puts the label in the inline grid column and renders the actions", async () => {
        const container = await render({ variant: "inline", actions: <button type="button" data-testid="cc-toggle" /> });

        const root = container.querySelector(".c__combobox");
        expect(root?.classList.contains("c__field--inline")).toBe(true);
        // The label must be a direct child of the field to land in the
        // left column of the ui-kit inline grid, and stay associated with
        // the input.
        const label = root?.querySelector(":scope > label");
        expect(label?.textContent).toBe("To");
        const input = root?.querySelector("input:not([type=hidden])");
        expect(label?.getAttribute("for")).toBe(input?.getAttribute("id"));
        expect(root?.querySelector(".labelled-box")).toBeNull();
        expect(root?.querySelector(".c__select__inner__actions [data-testid=cc-toggle]")).not.toBeNull();
    });

    it("renders plain chips without a warning callback", async () => {
        const container = await render();

        expect(container.querySelectorAll(".c__combobox__chip")).toHaveLength(2);
        expect(container.querySelector(".c__combobox__chip--warning")).toBeNull();
    });
});

describe("ComboBox option actions and fallback section", () => {
    const WITH_ACTION = "with-action@example.com";
    const OTHER = "other@example.com";
    const FALLBACK = "fallback@example.com";
    const ACTION_SELECTOR = ".c__combobox__menu__item__action";
    const MAIN_OPTIONS = [
        { label: WITH_ACTION, value: WITH_ACTION },
        { label: OTHER, value: OTHER },
    ];

    type RenderOptions = {
        onAction?: () => void | Promise<void>,
        options?: { label: string, value: string }[],
        value?: string[],
    };

    // Unmounted after each test: a menu left displayed would otherwise mix
    // its options with the next test's ones.
    const mounted: { root: Root, container: HTMLElement }[] = [];
    afterEach(() => {
        act(() => {
            mounted.splice(0).forEach(({ root, container }) => {
                root.unmount();
                container.remove();
            });
        });
    });

    const renderOpened = async ({ onAction = vi.fn(), options = MAIN_OPTIONS, value }: RenderOptions = {}) => {
        const onChange = vi.fn();
        const container = document.createElement("div");
        document.body.appendChild(container);
        const root = createRoot(container);
        mounted.push({ root, container });
        await act(async () => {
            root.render(
                <CunninghamProvider>
                <ContextMenuProvider>
                    <ComboBox
                        label="To"
                        options={options}
                        value={value}
                        fallbackSection={{
                            title: "Hidden suggestions",
                            options: [{ label: FALLBACK, value: FALLBACK }],
                        }}
                        onChange={onChange}
                        getOptionAction={(option) =>
                            option.value === WITH_ACTION
                                ? { label: "Hide", icon: <span />, onAction }
                                : undefined
                        }
                    />
                </ContextMenuProvider>
                </CunninghamProvider>
            );
        });
        const input = container.querySelector<HTMLInputElement>("input:not([type=hidden])")!;
        await act(async () => {
            input.focus();
        });
        await pressKey(input, { key: "ArrowDown" });
        return { input, onChange };
    };

    const pressKey = async (input: HTMLInputElement, init: KeyboardEventInit) => {
        await act(async () => {
            input.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init }));
        });
    };

    it("only offers the action on the options that have one", async () => {
        await renderOpened();

        const items = document.querySelectorAll(".c__combobox__menu__item");
        expect(items).toHaveLength(2);
        expect(items[0].querySelector(ACTION_SELECTOR)).not.toBeNull();
        expect(items[0].getAttribute("aria-keyshortcuts")).toBe("Shift+Delete");
        expect(items[1].querySelector(ACTION_SELECTOR)).toBeNull();
        expect(items[1].hasAttribute("aria-keyshortcuts")).toBe(false);
        expect(items[1].hasAttribute("aria-describedby")).toBe(false);
    });

    it("announces the action on the option, out of its name", async () => {
        await renderOpened();

        const option = document.querySelector('[role="option"]')!;
        const button = option.querySelector<HTMLButtonElement>(ACTION_SELECTOR)!;
        // An option cannot hold an interactive element.
        expect(button.getAttribute("aria-hidden")).toBe("true");
        expect(button.title).toBe("Hide (Shift+Delete)");
        const description = document.getElementById(option.getAttribute("aria-describedby")!);
        expect(description?.textContent).toBe("Hide (Shift+Delete)");
        // Hidden, so the option name is the contact alone.
        expect(description?.hidden).toBe(true);
    });

    it("runs the action without picking the option, then gives the focus back", async () => {
        let finishAction = () => {};
        const onAction = vi.fn(() => new Promise<void>((resolve) => { finishAction = resolve; }));
        const { input, onChange } = await renderOpened({ onAction });
        onChange.mockClear();
        // Typed text that would become a chip on blur.
        await typeInto(input, "typed@example.com");
        expect(input.value).toBe("typed@example.com");

        await act(async () => {
            document.querySelector<HTMLButtonElement>(ACTION_SELECTOR)!.click();
        });
        // Like a modal taking the focus while the action runs.
        await act(async () => {
            input.blur();
        });
        await act(async () => {
            finishAction();
        });

        expect(onAction).toHaveBeenCalledTimes(1);
        expect(onChange).not.toHaveBeenCalled();
        expect(document.activeElement).toBe(input);
    });

    it("runs the highlighted option's action with Shift+Delete only", async () => {
        const onAction = vi.fn();
        const { input, onChange } = await renderOpened({ onAction });
        onChange.mockClear();

        for (const init of [
            { key: "Delete" },
            { key: "Backspace" },
            { key: "Backspace", shiftKey: true },
        ]) {
            await pressKey(input, init);
        }
        expect(onAction).not.toHaveBeenCalled();

        await pressKey(input, { key: "Delete", shiftKey: true });
        expect(onAction).toHaveBeenCalledTimes(1);
        expect(onChange).not.toHaveBeenCalled();

        // The next option has no action: the shortcut does nothing there.
        await pressKey(input, { key: "ArrowDown" });
        await pressKey(input, { key: "Delete", shiftKey: true });
        expect(onAction).toHaveBeenCalledTimes(1);
    });

    describe("long press", () => {
        const options = () => Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'));

        const pressEscapeInContextMenu = async () => {
            await act(async () => {
                document.activeElement!.dispatchEvent(
                    new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })
                );
            });
        };

        afterEach(async () => {
            if (contextMenuItems().length > 0) await pressEscapeInContextMenu();
        });

        it("offers the action in a context menu, over the suggestions", async () => {
            const onAction = vi.fn();
            const { onChange } = await renderOpened({ onAction });
            onChange.mockClear();

            longPress(options()[0]);

            expect(contextMenuItems().map((item) => item.textContent)).toEqual(["Hide"]);
            // The menu took the focus: that blur neither picked the option
            // nor closed the suggestions.
            expect(onChange).not.toHaveBeenCalled();
            expect(options()).toHaveLength(2);

            await clickContextMenuItem(contextMenuItems()[0]);
            expect(onAction).toHaveBeenCalledTimes(1);
            expect(contextMenuItems()).toHaveLength(0);
            expect(options()).toHaveLength(2);
        });

        it("keeps the suggestions once the context menu is dismissed", async () => {
            const { input } = await renderOpened();

            longPress(options()[0]);
            await pressEscapeInContextMenu();
            expect(contextMenuItems()).toHaveLength(0);
            // Still displayed while react-aria gives the focus back.
            expect(options()).toHaveLength(2);

            await act(async () => {
                for (let frame = 0; frame < 3; frame++) {
                    await new Promise((resolve) => requestAnimationFrame(resolve));
                }
            });
            expect(document.activeElement).toBe(input);
            expect(options()).toHaveLength(2);
        });

        it("picks nothing and keeps the suggestions when Android takes the focus first", async () => {
            const { input, onChange } = await renderOpened();
            onChange.mockClear();

            longPress(options()[0], { androidFocusLoss: input });

            expect(onChange).not.toHaveBeenCalled();
            expect(options()).toHaveLength(2);
            expect(contextMenuItems().map((item) => item.textContent)).toEqual(["Hide"]);
        });

        it("picks nothing on a long press on an option without action", async () => {
            const { input, onChange } = await renderOpened();
            onChange.mockClear();

            longPress(options()[1], { androidFocusLoss: input });

            expect(contextMenuItems()).toHaveLength(0);
            expect(onChange).not.toHaveBeenCalled();
        });

        it.each([0, 1])("still picks option #%s on a tap", async (index) => {
            const { onChange } = await renderOpened();
            onChange.mockClear();

            await tap(options()[index]);

            expect(onChange).toHaveBeenLastCalledWith([[WITH_ACTION, OTHER][index]]);
            expect(contextMenuItems()).toHaveLength(0);
        });
    });

    it("uses Shift+Backspace on Apple keyboards, without moving to the last chip", async () => {
        vi.spyOn(EventHelper, "isApplePlatform").mockReturnValue(true);
        try {
            const onAction = vi.fn();
            // A chip, which a plain Backspace at the start of the input would
            // focus: downshift leaves Backspace with Shift alone.
            const { input } = await renderOpened({ onAction, value: [OTHER] });

            const option = document.querySelector('[role="option"]')!;
            expect(option.getAttribute("aria-keyshortcuts")).toBe("Shift+Backspace");
            expect(option.querySelector<HTMLButtonElement>(ACTION_SELECTOR)!.title).toBe("Hide (⇧⌫)");

            await pressKey(input, { key: "Delete", shiftKey: true });
            expect(onAction).not.toHaveBeenCalled();

            await pressKey(input, { key: "Backspace", shiftKey: true });
            expect(onAction).toHaveBeenCalledTimes(1);
            expect(document.activeElement).toBe(input);
        } finally {
            vi.restoreAllMocks();
        }
    });

    const fallbackGroup = () => document.querySelector('.c__combobox__menu [role="group"]');

    it("keeps the fallback options out while a main option is left", async () => {
        await renderOpened();

        expect(fallbackGroup()).toBeNull();
    });

    it("offers the fallback options in a titled group once no main option is left", async () => {
        await renderOpened({ options: [] });

        const group = fallbackGroup();
        const titleId = group?.getAttribute("aria-labelledby");
        expect(titleId && document.getElementById(titleId)?.textContent).toBe("Hidden suggestions");
        expect(Array.from(group?.querySelectorAll('[role="option"]') ?? []).map((o) => o.textContent))
            .toEqual([FALLBACK]);
    });

    it("counts the main options already picked as none left", async () => {
        await renderOpened({ options: [{ label: OTHER, value: OTHER }], value: [OTHER] });

        expect(document.querySelectorAll(".c__combobox__menu__item")).toHaveLength(1);
        expect(fallbackGroup()).not.toBeNull();
    });

    it("picks a fallback option like a main one", async () => {
        const { onChange } = await renderOpened({ options: [] });

        await act(async () => {
            document.querySelector<HTMLLIElement>('[role="group"] [role="option"]')!.click();
        });

        expect(onChange).toHaveBeenLastCalledWith([FALLBACK]);
    });
});

describe("ComboBox option action opening a modal", () => {
    const WITH_ACTION = "with-action@example.com";
    const OTHER = "other@example.com";

    // The modal is rendered for real: react-modal gives the focus back while
    // unmounting, during a React commit, when React drops focus events.
    const ModalActionFixture = () => {
        const modals = useModals();
        return (
            <ComboBox
                label="To"
                options={[
                    { label: WITH_ACTION, value: WITH_ACTION },
                    { label: OTHER, value: OTHER },
                ]}
                getOptionAction={(option) =>
                    option.value === WITH_ACTION
                        ? {
                            label: "Hide",
                            icon: <span />,
                            onAction: async () => { await modals.confirmationModal({ title: "Hide?" }); },
                        }
                        : undefined
                }
            />
        );
    };

    const pressKey = async (input: HTMLInputElement, init: KeyboardEventInit) => {
        await act(async () => {
            input.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init }));
        });
    };
    const menuItems = () => document.querySelectorAll(".c__combobox__menu__item");

    it.each(["the button", "Shift+Delete", "a long press"])("reopens the menu in the focused field once the modal is closed, run from %s", async (trigger) => {
        const container = document.createElement("div");
        // react-modal hides this element from assistive technologies while open.
        container.className = "c__app";
        document.body.appendChild(container);
        await act(async () => {
            createRoot(container).render(
                <CunninghamProvider>
                <ContextMenuProvider>
                    <ModalProvider>
                        <ModalActionFixture />
                    </ModalProvider>
                </ContextMenuProvider>
                </CunninghamProvider>
            );
        });
        const input = container.querySelector<HTMLInputElement>("input:not([type=hidden])")!;
        await act(async () => {
            input.focus();
        });
        await pressKey(input, { key: "ArrowDown" });

        if (trigger === "Shift+Delete") {
            await pressKey(input, { key: "Delete", shiftKey: true });
        } else if (trigger === "a long press") {
            // The context menu gives the focus back to the input right before
            // the modal takes it: no option may get picked on the way.
            longPress(document.querySelector('[role="option"]')!);
            await clickContextMenuItem(contextMenuItems()[0]);
        } else {
            await act(async () => {
                document.querySelector<HTMLButtonElement>(".c__combobox__menu__item__action")!.click();
            });
        }
        const dialog = document.querySelector('[role="dialog"]');
        expect(dialog).not.toBeNull();
        await act(async () => {
            Array.from(dialog!.querySelectorAll("button")).at(-1)!.click();
        });

        expect(document.querySelector('[role="dialog"]')).toBeNull();
        expect(document.activeElement).toBe(input);
        expect(menuItems()).toHaveLength(2);
        expect(container.querySelectorAll(".c__combobox__chip")).toHaveLength(0);

        // Typing must open it again too, the field knowing it has the focus.
        await pressKey(input, { key: "Escape" });
        expect(menuItems()).toHaveLength(0);
        await typeInto(input, "oth");
        expect(menuItems()).toHaveLength(2);
    });
});
