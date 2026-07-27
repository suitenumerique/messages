import { act, ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { CunninghamProvider } from "@gouvfr-lasuite/ui-components";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const THREAD_HREF = "/mailbox/mailbox-1/thread/thread-1?has_active=1";

const navigate = vi.fn();
const onClose = vi.fn();
let isNative = false;

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/features/i18n/initI18n", () => ({
    default: { resolvedLanguage: "en-US" },
}));

vi.mock("@tanstack/react-router", () => ({
    Link: ({ children, className }: { children: ReactNode; className?: string }) => (
        <a className={className}>{children}</a>
    ),
    useRouter: () => ({
        navigate,
        buildLocation: () => ({ href: THREAD_HREF }),
    }),
}));

vi.mock("@tanstack/react-query", () => ({
    useQueryClient: () => ({ setQueryData: vi.fn() }),
}));

vi.mock("@/features/api/gen", () => ({
    getMailboxesStorageRetrieveQueryKey: (id: string) => ["storage", id],
    useMailboxesStorageRetrieve: () => ({
        isLoading: false,
        error: null,
        data: {
            status: 200,
            data: {
                total_storage: 2048,
                thread_count: 1,
                message_count: 1,
                trashed_storage: 0,
                spam_storage: 0,
                largest_threads: [
                    {
                        id: "thread-1",
                        subject: "Quarterly report",
                        message_count: 1,
                        messaged_at: null,
                        size: 2048,
                        is_unread: false,
                        abilities: { edit: true },
                    },
                ],
            },
        },
    }),
}));

vi.mock("@/features/message/use-trash", () => ({
    default: () => ({ markAsTrashed: vi.fn() }),
}));

vi.mock("@/features/quota/api/use-mailbox-entitlements", () => ({
    useMailboxEntitlements: () => ({ data: undefined }),
}));

vi.mock("@/features/native/platform", () => ({
    isNativePlatform: () => isNative,
}));

// The real ability hook drags in the auth provider; only the key is read here.
vi.mock("@/hooks/use-ability", () => ({
    Abilities: { CAN_EDIT_THREAD: "edit" },
}));

// The real helper builds the whole sidebar folder tree.
vi.mock("@/features/utils/view-helper", () => ({
    default: { getThreadFolderSearch: () => ({ has_active: "1" }) },
}));

vi.mock("@/features/ui/components/tooltip", () => ({
    Tooltip: ({ children }: { children: ReactNode }) => children,
}));

import type { Mailbox } from "@/features/api/gen";
import { MailboxSettingsStorageTab } from ".";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

const render = () => {
    act(() => {
        root.render(
            <CunninghamProvider>
                <MailboxSettingsStorageTab mailbox={{ id: "mailbox-1" } as Mailbox} onClose={onClose} />
            </CunninghamProvider>,
        );
    });
};

const openAction = () =>
    container.querySelector<HTMLElement>(
        '.mailbox-settings__storage-item-action[aria-label="Open in new tab"], .mailbox-settings__storage-item-action[aria-label="Open"]',
    );

describe("MailboxSettingsStorageTab — opening a conversation", () => {
    beforeEach(() => {
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        isNative = false;
        navigate.mockReset();
        onClose.mockReset();
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
    });

    it("opens the conversation in a new tab on the web", () => {
        render();

        const action = openAction();
        expect(action?.tagName).toBe("A");
        expect(action?.getAttribute("aria-label")).toBe("Open in new tab");
        expect(action?.getAttribute("href")).toBe(THREAD_HREF);
        expect(action?.getAttribute("target")).toBe("_blank");
        expect(action?.getAttribute("rel")).toBe("noopener noreferrer");
    });

    it("opens the conversation in place from a button in the native shell", () => {
        isNative = true;
        render();

        // A plain button: no link a long press could send to the system browser.
        const action = openAction();
        expect(action?.tagName).toBe("BUTTON");
        expect(action?.getAttribute("aria-label")).toBe("Open");
        expect(action?.hasAttribute("href")).toBe(false);
        expect(action?.hasAttribute("target")).toBe(false);

        act(() => action?.click());

        expect(navigate).toHaveBeenCalledWith({
            to: "/mailbox/$mailboxId/thread/$threadId",
            params: { mailboxId: "mailbox-1", threadId: "thread-1" },
            search: { has_active: "1" },
        });
        expect(onClose).toHaveBeenCalledTimes(1);
    });
});
