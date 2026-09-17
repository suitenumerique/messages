import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

import { DraftSaveStatus } from "./draft-save-status";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
const now = new Date("2026-09-15T10:00:00Z");

const render = (savedAt: string | undefined, isSaving = false) =>
    act(() => {
        root.render(<DraftSaveStatus savedAt={savedAt} isSaving={isSaving} now={now} />);
    });

const liveRegion = () => container.querySelector<HTMLElement>("[role='status']")!;

beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => {
        root.unmount();
    });
    document.body.innerHTML = "";
    vi.useRealTimers();
});

describe("DraftSaveStatus", () => {
    it("shows the saving spinner with a name, and the last save time", () => {
        render("2026-09-15T09:59:00Z", true);

        expect(container.querySelector("[role='img']")?.getAttribute("aria-label")).toBe("Saving...");
        expect(container.textContent).toContain("Last saved {{relativeTime}}");
    });

    it("announces a save once it lands, then goes quiet for the next one", () => {
        render("2026-09-15T09:59:00Z");
        expect(liveRegion().getAttribute("aria-live")).toBe("polite");
        expect(liveRegion().textContent).toBe("");

        render("2026-09-15T09:59:30Z");
        expect(liveRegion().textContent).toBe("Draft saved");

        act(() => {
            vi.advanceTimersByTime(3000);
        });
        expect(liveRegion().textContent).toBe("");

        render("2026-09-15T10:00:00Z");
        expect(liveRegion().textContent).toBe("Draft saved");
    });

    it("stays silent on mount and on the first save: the creation toast covers it", () => {
        render(undefined);
        expect(liveRegion().textContent).toBe("");

        render("2026-09-15T09:59:00Z");
        expect(liveRegion().textContent).toBe("");
    });

    it("does not announce the clock ticking on an unchanged save", () => {
        render("2026-09-15T09:59:00Z");
        render("2026-09-15T09:59:00Z");
        expect(liveRegion().textContent).toBe("");
    });
});
