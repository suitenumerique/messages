import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const emptyFolder = vi.fn();
let cutoffDays = 0;
let canEmptyFolder = true;

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@gouvfr-lasuite/ui-components", async (importOriginal) => ({
    ...(await importOriginal<typeof import("@gouvfr-lasuite/ui-components")>()),
    useResponsive: () => ({ isDesktop: true, isMobile: false }),
}));

vi.mock("@/features/providers/config", () => ({
    useConfig: () => ({ TRASHBIN_CUTOFF_DAYS: cutoffDays }),
}));

vi.mock("@/features/providers/mailbox", () => ({
    useMailboxContext: () => ({
        mailboxes: [],
        threads: { count: 2, results: [{ id: "thread-1" }, { id: "thread-2" }] },
        selectedMailbox: { id: "mailbox-1" },
        unselectThreadIfAmong: vi.fn(),
    }),
}));

vi.mock("@/features/message/use-empty-trash", () => ({
    default: () => ({ emptyTrashbin: vi.fn(), isPending: false }),
    useEmptyCurrentFolder: () => ({
        canEmptyFolder,
        emptyFolder,
        emptyFolderLabel: "Empty trash",
        isPending: false,
    }),
}));

vi.mock("@/features/message/use-read", () => ({ default: () => ({ markAsReadAt: vi.fn() }) }));
vi.mock("@/features/message/use-archive", () => ({ default: () => ({ markAsArchived: vi.fn(), markAsUnarchived: vi.fn() }) }));
vi.mock("@/features/message/use-spam", () => ({ default: () => ({ markAsSpam: vi.fn(), markAsNotSpam: vi.fn() }) }));
vi.mock("@/features/message/use-trash", () => ({ default: () => ({ markAsTrashed: vi.fn(), markAsUntrashed: vi.fn() }) }));
vi.mock("@/features/message/use-delete-drafts", () => ({ default: () => ({ deleteDrafts: vi.fn() }) }));
vi.mock("@/features/message/use-starred", () => ({ default: () => ({ markAsStarred: vi.fn(), markAsUnstarred: vi.fn() }) }));
vi.mock("@/features/message/use-can-edit-threads", () => ({ default: () => true }));

vi.mock("@/hooks/use-url-search-params", () => ({
    useUrlSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/hooks/use-current-folder-name", () => ({ useCurrentFolderName: () => "Trash" }));
vi.mock("@/hooks/use-current-view", () => ({
    useCurrentView: () => ({ isTrashedView: true, isSpamView: false, isArchivedView: false, isSentView: false, isDraftsView: false }),
}));
vi.mock("@/hooks/use-ability", () => ({
    default: () => true,
    Abilities: { CAN_EMPTY_TRASH: "empty_trash", CAN_MANAGE_MAILBOX_LABELS: "manage_labels" },
}));

vi.mock("@/features/native/platform", () => ({ isNativePlatform: () => false }));
vi.mock("@/features/layouts/components/labels-widget", () => ({ LabelsWidget: () => null }));
vi.mock("./thread-panel-filter", () => ({ ThreadPanelFilter: () => null }));
vi.mock("../hooks/use-thread-panel-filters", () => ({
    useThreadPanelFilters: () => ({ activeFilters: {} }),
}));
vi.mock("@/features/providers/thread-selection", () => ({
    SelectionReadStatus: { READ: "read", MIXED: "mixed", NONE: "none" },
    SelectionStarredStatus: { MIXED: "mixed", STARRED: "starred", UNSTARRED: "unstarred", NONE: "none" },
}));
vi.mock("@/features/ui/components/icon", () => ({
    Icon: () => <span />,
}));

import { CunninghamProvider } from "@gouvfr-lasuite/ui-components";
import { SelectionReadStatus, SelectionStarredStatus } from "@/features/providers/thread-selection";
import ThreadPanelHeader from "./thread-panel-header";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("ThreadPanelHeader empty folder action", () => {
    let container: HTMLDivElement;
    let root: Root;

    const render = (isSelectionMode = false) => {
        act(() => {
            root.render(
                <CunninghamProvider>
                    <ThreadPanelHeader
                        selectedThreadIds={new Set<string>()}
                        isAllSelected={false}
                        isSomeSelected={false}
                        isSelectionMode={isSelectionMode}
                        selectionReadStatus={SelectionReadStatus.NONE}
                        selectionStarredStatus={SelectionStarredStatus.NONE}
                        onSelectAll={vi.fn()}
                        onDeselectAll={vi.fn()}
                        onClearSelection={vi.fn()}
                        onEnableSelectionMode={vi.fn()}
                        onDisableSelectionMode={vi.fn()}
                    />
                </CunninghamProvider>,
            );
        });
    };

    const emptyButton = () => container.querySelector<HTMLButtonElement>('button[aria-label="Empty trash"]');

    beforeEach(() => {
        cutoffDays = 0;
        canEmptyFolder = true;
        vi.clearAllMocks();
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
    });

    it("offers to empty the folder when no retention policy banner does", () => {
        render();

        act(() => emptyButton()?.click());

        expect(emptyFolder).toHaveBeenCalled();
    });

    it("leaves it to the banner when there is a retention policy", () => {
        cutoffDays = 30;

        render();

        expect(emptyButton()).toBeNull();
    });

    it("leaves room for the selection's own delete action in selection mode", () => {
        render(true);

        expect(emptyButton()).toBeNull();
    });

    it("hides it when the folder cannot be emptied", () => {
        canEmptyFolder = false;

        render();

        expect(emptyButton()).toBeNull();
    });
});
