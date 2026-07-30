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
    isNativePlatform: () => false,
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

import { ContextMenuProvider } from "@gouvfr-lasuite/ui-kit";
import { CunninghamProvider } from "@gouvfr-lasuite/cunningham-react";
import { setSelectedFilters } from "../hooks/use-selected-filters";
import { DEFAULT_SELECTED_FILTERS } from "../hooks/use-thread-panel-filters";
import { THREAD_SELECTED_FILTERS_KEY } from "@/features/config/constants";
import { ThreadPanelFilter } from "./thread-panel-filter";

let container: HTMLDivElement;
let root: Root;

const storedFilters = () =>
    JSON.parse(localStorage.getItem(THREAD_SELECTED_FILTERS_KEY) ?? "null");

const menuItems = () =>
    Array.from(
        document.querySelectorAll<HTMLElement>("[data-testid^='context-menu-item-']"),
    );

/** Label of every filter currently ticked in the open menu. */
const checkedLabels = () =>
    menuItems()
        .filter((el) => el.querySelector("[data-icon='check_box']"))
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
