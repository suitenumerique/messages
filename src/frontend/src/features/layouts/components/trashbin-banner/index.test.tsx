import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const emptyFolder = vi.fn();
let cutoffDays = 0;
let folder = { isTrashbinView: true, isEmptying: false, canEmptyFolder: true };

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@gouvfr-lasuite/ui-components", async (importOriginal) => ({
    ...(await importOriginal<typeof import("@gouvfr-lasuite/ui-components")>()),
    useResponsive: () => ({ isMobile: false }),
}));

vi.mock("@/features/providers/config", () => ({
    useConfig: () => ({ TRASHBIN_CUTOFF_DAYS: cutoffDays }),
}));

vi.mock("@/hooks/use-current-view", () => ({
    useCurrentView: () => ({ isSpamView: false }),
}));

vi.mock("@/features/message/use-empty-trash", () => ({
    useEmptyCurrentFolder: () => ({
        ...folder,
        emptyFolder,
        emptyFolderLabel: "Empty trash",
        isPending: false,
    }),
}));

vi.mock("@/features/ui/components/icon", () => ({
    Icon: () => <span />,
}));

import { CunninghamProvider } from "@gouvfr-lasuite/ui-components";
import { TrashbinBanner } from ".";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const RETENTION_MESSAGE = "Messages that have been in the trash for more than {{count}} days are automatically and permanently deleted.";

describe("TrashbinBanner", () => {
    let container: HTMLDivElement;
    let root: Root;

    const render = () => {
        act(() => {
            root.render(
                <CunninghamProvider>
                    <TrashbinBanner />
                </CunninghamProvider>,
            );
        });
    };

    const banner = () => container.querySelector(".c__header-banner");
    const emptyButton = () => container.querySelector("button");

    beforeEach(() => {
        cutoffDays = 0;
        folder = { isTrashbinView: true, isEmptying: false, canEmptyFolder: true };
        vi.clearAllMocks();
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
    });

    it("stays hidden without a retention policy, emptying being left to the thread list actions", () => {
        render();

        expect(banner()).toBeNull();
    });

    it("still tells a folder is being emptied without a retention policy", () => {
        folder = { isTrashbinView: true, isEmptying: true, canEmptyFolder: false };

        render();

        expect(banner()?.textContent).toContain("Trash is being emptied…");
        expect(emptyButton()).toBeNull();
    });

    it("announces the retention policy and offers to empty the folder", () => {
        cutoffDays = 30;

        render();

        expect(banner()?.textContent).toContain(RETENTION_MESSAGE);
        act(() => emptyButton()?.click());
        expect(emptyFolder).toHaveBeenCalled();
    });

    it("announces the retention policy alone when the folder cannot be emptied", () => {
        cutoffDays = 30;
        folder = { isTrashbinView: true, isEmptying: false, canEmptyFolder: false };

        render();

        expect(banner()?.textContent).toContain(RETENTION_MESSAGE);
        expect(emptyButton()).toBeNull();
    });

    it("stays hidden outside the trashbin folders", () => {
        cutoffDays = 30;
        folder = { isTrashbinView: false, isEmptying: false, canEmptyFolder: false };

        render();

        expect(banner()).toBeNull();
    });
});
