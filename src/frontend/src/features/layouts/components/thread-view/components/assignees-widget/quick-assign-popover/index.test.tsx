import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QuickAssignPopover } from "./index";

const roster = [
    { id: "editor", full_name: "Editor", email: "editor@example.com", custom_attributes: {}, can_post_comments: true, can_be_assigned: true },
    { id: "me", full_name: "Me", email: "me@example.com", custom_attributes: {}, can_post_comments: true, can_be_assigned: true },
    { id: "viewer", full_name: "Viewer", email: "viewer@example.com", custom_attributes: {}, can_post_comments: true, can_be_assigned: false },
];

let assignedUserIds = new Set<string>();
const unassignUser = vi.fn();

vi.mock("@/features/api/gen", () => ({
    useThreadsUsersList: () => ({ data: { data: roster }, isLoading: false }),
}));

vi.mock("@/features/auth", () => ({
    useAuth: () => ({ user: { id: "me" } }),
}));

vi.mock("@/features/message/use-thread-assignment", () => ({
    useThreadAssignment: () => ({
        assignedUserIds,
        mutatingUserIds: new Set(),
        assignUser: vi.fn(),
        unassignUser,
    }),
}));

// The real hook suspends until the translation resources are loaded.
vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

// The overlay does not render under jsdom; the menu inside it is what we test.
vi.mock("react-aria-components", async (importOriginal) => ({
    ...(await importOriginal<typeof import("react-aria-components")>()),
    Popover: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@gouvfr-lasuite/ui-components", () => ({
    IconSize: {},
    IconType: {},
    Spinner: () => null,
    UserAvatar: () => null,
}));

vi.mock("@/features/ui/components/icon", () => ({
    Icon: () => null,
}));

describe("QuickAssignPopover", () => {
    let container: HTMLDivElement;
    let root: Root;

    beforeEach(() => {
        (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
        // jsdom lacks CSS.escape, used by react-aria to scroll to a selected row.
        vi.stubGlobal("CSS", { escape: (value: string) => value });
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
        assignedUserIds = new Set();
        unassignUser.mockReset();
        vi.unstubAllGlobals();
    });

    const renderRows = () => {
        const trigger = document.createElement("button");
        document.body.appendChild(trigger);
        act(() => {
            root.render(
                <QuickAssignPopover
                    isOpen
                    triggerRef={{ current: trigger }}
                    onOpenChange={() => {}}
                    threadId="thread-1"
                />,
            );
        });
        const rows = Array.from(document.body.querySelectorAll<HTMLElement>(".quick-assign-popover__row"));
        return Object.fromEntries(rows.map((row) => [row.dataset.key, row]));
    };

    it("lists only assignable users, current user first", () => {
        const rows = renderRows();
        expect(Object.keys(rows)).toEqual(["me", "editor"]);
    });

    it("keeps an assigned user who lost edit rights so they can be unassigned", () => {
        assignedUserIds = new Set(["viewer"]);
        const rows = renderRows();
        expect(Object.keys(rows)).toEqual(["me", "editor", "viewer"]);
        expect(rows.viewer.getAttribute("aria-checked")).toBe("true");

        act(() => rows.viewer.click());
        expect(unassignUser).toHaveBeenCalledWith("viewer");
    });
});
