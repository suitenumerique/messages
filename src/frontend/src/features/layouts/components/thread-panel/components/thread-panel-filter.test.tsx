import { act, useSyncExternalStore } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let searchStr = "";
const listeners = new Set<() => void>();

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/features/providers/mailbox", () => ({
    useMailboxContext: () => ({ threads: { results: [{ id: "1" }] } }),
}));

vi.mock("@/features/native/platform", () => ({
    isNativePlatform: vi.fn(() => false),
}));

vi.mock("@/hooks/use-url-search-params", async () => {
    const { useSyncExternalStore: subscribeToSearch } = await import("react");
    return {
        useUrlSearchParams: () =>
            new URLSearchParams(
                subscribeToSearch(
                    (cb: () => void) => {
                        listeners.add(cb);
                        return () => listeners.delete(cb);
                    },
                    () => searchStr,
                ),
            ),
    };
});

vi.mock("@/hooks/use-safe-router-push", () => ({
    useSafeRouterPush: () => (params: URLSearchParams) => {
        searchStr = params.toString();
        listeners.forEach((cb) => cb());
    },
}));

vi.mock("@/features/ui/components/icon", () => ({
    Icon: ({ name }: { name?: string }) => <span data-icon={name ?? "svg"} />,
}));

import { ContextMenuProvider } from "@gouvfr-lasuite/ui-components";
import { CunninghamProvider } from "@gouvfr-lasuite/ui-components";
import { setSelectedFilters } from "../hooks/use-selected-filters";
import { DEFAULT_SELECTED_FILTERS } from "../hooks/use-thread-panel-filters";
import { THREAD_SELECTED_FILTERS_KEY } from "@/features/config/constants";
import { isNativePlatform } from "@/features/native/platform";
import { ThreadPanelFilter } from "./thread-panel-filter";

let container: HTMLDivElement;
let root: Root;

const storedFilters = () =>
    JSON.parse(localStorage.getItem(THREAD_SELECTED_FILTERS_KEY) ?? "null");

const menuItems = () =>
    Array.from(
        document.querySelectorAll<HTMLElement>("[data-testid^='context-menu-item-']"),
    );

/**
 * Label of every filter currently ticked in the open menu. The ui-components
 * menu marks a checked item with a decorative checkmark only — no
 * `aria-checked` to key off — so its class is the sole ticked/unticked signal.
 */
const checkedLabels = () =>
    menuItems()
        .filter((el) => el.querySelector(".c__dropdown-menu-item__check"))
        .map((el) => el.textContent);

const openMenu = () => {
    const trigger = document.querySelector<HTMLElement>(
        "[data-testid='context-menu-trigger']",
    )!;
    act(() => {
        trigger.dispatchEvent(
            new MouseEvent("contextmenu", {
                bubbles: true,
                cancelable: true,
                clientX: 10,
                clientY: 10,
            }),
        );
    });
};

const filterButton = () =>
    document.querySelector<HTMLElement>("[aria-label='Filter threads']")!;

/**
 * Touch sequence of the native app. jsdom has no `Touch` constructor, so the
 * `touches` list the hook reads is pinned onto a plain event.
 */
const touch = (type: "touchstart" | "touchend") => {
    const event = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(event, "touches", {
        value: type === "touchstart" ? [{ clientX: 10, clientY: 10 }] : [],
    });
    act(() => {
        filterButton().dispatchEvent(event);
    });
};

const nativeContextMenu = (target: EventTarget) => {
    const event = new MouseEvent("contextmenu", {
        bubbles: true,
        cancelable: true,
    });
    act(() => {
        target.dispatchEvent(event);
    });
    return event;
};

const clickItem = (label: string) => {
    const item = menuItems().find((el) => el.textContent?.includes(label))!;
    const fire = (type: string) =>
        item.dispatchEvent(
            new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, detail: 1 }),
        );
    act(() => {
        fire("mousedown");
        fire("mouseup");
        fire("click");
    });
};

/**
 * Mirrors the app: the filter is rendered inside a subtree the router may
 * rebuild when the search params change, so a toggle can remount it.
 */
const RemountingHost = () => {
    const key = useSyncExternalStore(
        (cb: () => void) => {
            listeners.add(cb);
            return () => listeners.delete(cb);
        },
        () => searchStr,
    );
    return (
        <CunninghamProvider>
            <ContextMenuProvider>
                <ThreadPanelFilter key={key} />
            </ContextMenuProvider>
        </CunninghamProvider>
    );
};

describe("ThreadPanelFilter", () => {
    beforeEach(() => {
        (
            globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }
        ).IS_REACT_ACT_ENVIRONMENT = true;
        localStorage.clear();
        setSelectedFilters(DEFAULT_SELECTED_FILTERS);
        searchStr = "has_unread=1";
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
        listeners.clear();
        vi.mocked(isNativePlatform).mockReturnValue(false);
        vi.useRealTimers();
    });

    it("keeps the previous picks when several filters are toggled in a row", () => {
        act(() => {
            root.render(<RemountingHost />);
        });
        openMenu();

        expect(checkedLabels()).toEqual(["Unread"]);

        clickItem("Starred");
        expect(checkedLabels()).toEqual(["Unread", "Starred"]);
        expect(storedFilters()).toEqual(["has_unread", "has_starred"]);

        clickItem("Mentioned");
        expect(checkedLabels()).toEqual(["Unread", "Starred", "Mentioned"]);
        expect(storedFilters()).toEqual([
            "has_unread",
            "has_starred",
            "has_mention",
        ]);

        clickItem("Assigned to me");
        expect(checkedLabels()).toEqual([
            "Unread",
            "Starred",
            "Mentioned",
            "Assigned to me",
        ]);
        expect(new URLSearchParams(searchStr).getAll("has_mention")).toEqual(["1"]);
    });

    // Android fires its own `contextmenu` for a long press, on a system delay
    // (a Samsung accessibility setting) that can land after the menu opened.
    // The provider closes any open menu on a captured `contextmenu`: the one
    // the press emits must never reach it (`useLongPress` swallows it).
    it("keeps the menu a long press opened when the browser's own contextmenu lands after it", () => {
        vi.mocked(isNativePlatform).mockReturnValue(true);
        vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
        act(() => {
            root.render(<RemountingHost />);
        });

        touch("touchstart");
        act(() => {
            vi.advanceTimersByTime(500);
        });
        expect(menuItems()).toHaveLength(4);

        const late = nativeContextMenu(filterButton());
        expect(late.defaultPrevented).toBe(true);
        expect(menuItems()).toHaveLength(4);
        touch("touchend");
        expect(menuItems()).toHaveLength(4);

        // Only the trigger is shielded: a right-click anywhere else still
        // closes the menu, as the provider intends.
        nativeContextMenu(document.body);
        expect(menuItems()).toHaveLength(0);
    });

    it("falls back to the default selection when the last filter is unticked", () => {
        act(() => {
            root.render(<RemountingHost />);
        });
        openMenu();

        clickItem("Starred");
        clickItem("Unread");
        expect(checkedLabels()).toEqual(["Starred"]);

        clickItem("Starred");
        expect(checkedLabels()).toEqual(["Unread"]);
        expect(storedFilters()).toEqual(DEFAULT_SELECTED_FILTERS);
    });
});
