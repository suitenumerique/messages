import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const submit = vi.fn();
const reset = vi.fn();
let search = { query: "", isSearching: false };

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/features/native/platform", () => ({
    isNativePlatform: () => true,
}));

vi.mock("@/features/message/use-compose-message", () => ({
    useComposeMessage: () => ({
        canWriteMessages: true,
        goToNewMessage: vi.fn(),
        selectedMailbox: { id: "mailbox-1" },
    }),
}));

vi.mock("@/features/layouts/components/thread-panel/components/thread-panel-filter", () => ({
    ThreadPanelFilter: () => <span data-testid="thread-panel-filter" />,
}));

vi.mock("@/features/forms/components/search-input/use-search-query", () => ({
    useSearchQuery: () => ({ ...search, submit, reset }),
}));

vi.mock("@/features/forms/components/search-filters-modal", () => ({
    SearchFiltersModal: ({ isOpen, onSubmit }: { isOpen: boolean; onSubmit: (query: string) => void }) =>
        isOpen ? (
            <div data-testid="search-filters-modal">
                <button type="button" data-testid="search-filters-submit" onClick={() => onSubmit("from:bob")} />
            </div>
        ) : null,
}));

vi.mock("@/features/ui/components/icon", () => ({
    Icon: ({ name }: { name?: string }) => <span data-icon={name ?? "svg"} />,
}));

import { MobileFilterComposeBar } from ".";

let container: HTMLDivElement;
let root: Root;

const render = () => {
    act(() => {
        root.render(<MobileFilterComposeBar />);
    });
};

const summary = () =>
    document.querySelector<HTMLElement>(".mobile-filter-compose-bar__search-summary");

const click = (el: HTMLElement) => {
    act(() => {
        el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
};

beforeEach(() => {
    search = { query: "", isSearching: false };
    submit.mockClear();
    reset.mockClear();
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

describe("MobileFilterComposeBar", () => {
    it("keeps the bar to the filter and compose controls outside of a search", () => {
        render();

        expect(document.querySelector("[data-testid='thread-panel-filter']")).not.toBeNull();
        expect(document.querySelector("[aria-label='New message']")).not.toBeNull();
        expect(summary()).toBeNull();
    });

    it("shows the raw query once a search is applied", () => {
        search = { query: "from:alice budget", isSearching: true };
        render();

        expect(summary()?.textContent).toBe("from:alice budget");
        // The quick filter stays: it combines with the search.
        expect(document.querySelector("[data-testid='thread-panel-filter']")).not.toBeNull();
    });

    it("leaves search mode in a single tap", () => {
        search = { query: "from:alice", isSearching: true };
        render();

        click(document.querySelector<HTMLElement>("[aria-label='Reset']")!);

        expect(reset).toHaveBeenCalledTimes(1);
    });

    it("opens the search form from the summary and submits through it", () => {
        search = { query: "from:alice", isSearching: true };
        render();
        expect(document.querySelector("[data-testid='search-filters-modal']")).toBeNull();

        click(summary()!);
        expect(document.querySelector("[data-testid='search-filters-modal']")).not.toBeNull();

        click(document.querySelector<HTMLElement>("[data-testid='search-filters-submit']")!);
        expect(submit).toHaveBeenCalledWith("from:bob");
        expect(document.querySelector("[data-testid='search-filters-modal']")).toBeNull();
    });
});
