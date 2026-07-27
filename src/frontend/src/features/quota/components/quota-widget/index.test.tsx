import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { CunninghamProvider } from "@gouvfr-lasuite/ui-components";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MailboxEntitlements } from "@/features/api/gen/models/mailbox_entitlements";

const GB = 1000 ** 3;

const openModal = vi.fn();
const useMailboxEntitlements = vi.fn();
let entitlements: MailboxEntitlements | undefined;
let canManageAccesses = false;

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/features/providers/mailbox", () => ({
    useMailboxContext: () => ({
        selectedMailbox: { abilities: { manage_accesses: canManageAccesses } },
    }),
}));

vi.mock("@/features/providers/modal-store", () => ({
    useModalStore: () => ({ openModal }),
}));

// Only the modal id is needed; the real module drags in the whole settings modal.
vi.mock("@/features/layouts/components/mailbox-settings/modal-mailbox-settings", () => ({
    MODAL_MAILBOX_SETTINGS_ID: "modal-mailbox-settings",
}));

vi.mock("../../api/use-mailbox-entitlements", () => ({
    useMailboxEntitlements: (mailboxId: string | undefined) => {
        useMailboxEntitlements(mailboxId);
        return { data: entitlements ? { status: 200, data: entitlements } : undefined };
    },
}));

import { MODAL_MAILBOX_SETTINGS_ID } from "@/features/layouts/components/mailbox-settings/modal-mailbox-settings";
import { QuotaWidget } from ".";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

const render = (mailboxId: string | undefined = "mailbox-1") => {
    act(() => {
        root.render(
            <CunninghamProvider>
                <QuotaWidget mailboxId={mailboxId} />
            </CunninghamProvider>,
        );
    });
};

const widget = () => container.querySelector<HTMLElement>(".quota-widget");
const gauge = () => container.querySelector<HTMLButtonElement>(".c__storage-gauge");
const meter = () => container.querySelector<HTMLElement>('[role="meter"]');
const label = () => container.querySelector<HTMLElement>(".c__storage-gauge__label");
const lockedLabel = () => container.querySelector<HTMLElement>(".quota-widget__locked-label");

beforeEach(() => {
    entitlements = undefined;
    canManageAccesses = false;
    openModal.mockClear();
    useMailboxEntitlements.mockClear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => root.unmount());
    container.remove();
});

describe("QuotaWidget", () => {
    it("fetches the entitlements of the given mailbox", () => {
        render("mailbox-42");
        expect(useMailboxEntitlements).toHaveBeenCalledWith("mailbox-42");
    });

    it("renders nothing while the entitlements are not loaded", () => {
        render();
        expect(widget()).toBeNull();
    });

    describe("without an account limit", () => {
        it.each([
            ["unknown (null)", null],
            ["unlimited (0)", 0],
        ])("renders nothing when the account limit is %s", (_, max_storage) => {
            entitlements = {
                account: { storage_used: 2 * GB, max_storage },
                organization: null,
            };
            render();
            expect(widget()).toBeNull();
        });

        it("renders nothing while the organization still has room", () => {
            entitlements = {
                account: { storage_used: 2 * GB, max_storage: null },
                organization: { storage_used: 10 * GB, max_storage: 100 * GB },
            };
            render();
            expect(widget()).toBeNull();
        });

        it("shows only the organization warning, never a zero-total gauge, once the organization is full", () => {
            entitlements = {
                account: { storage_used: 2 * GB, max_storage: null },
                organization: { storage_used: 100 * GB, max_storage: 100 * GB },
            };
            render();
            expect(gauge()?.classList.contains("c__storage-gauge--locked")).toBe(true);
            expect(lockedLabel()?.textContent).toBe("Domain's storage exceeded");
            expect(meter()).toBeNull();
            expect(label()).toBeNull();
        });
    });

    describe("with an account limit", () => {
        it("gauges the account usage in gigabytes", () => {
            entitlements = {
                account: { storage_used: 2.5 * GB, max_storage: 5 * GB },
                organization: null,
            };
            render();
            expect(gauge()?.classList.contains("c__storage-gauge--locked")).toBe(false);
            expect(meter()?.getAttribute("aria-valuenow")).toBe("2.5");
            expect(meter()?.getAttribute("aria-valuemax")).toBe("5");
            // The library separates the parts with non-breaking spaces.
            expect(label()?.textContent).toBe("2.50\u00a0/\u00a05\u00a0GB");
            expect(lockedLabel()).toBeNull();
        });

        it("locks the gauge once the organization is full, even if the account has room", () => {
            entitlements = {
                account: { storage_used: 1 * GB, max_storage: 5 * GB },
                organization: { storage_used: 120 * GB, max_storage: 100 * GB },
            };
            render();
            expect(gauge()?.classList.contains("c__storage-gauge--locked")).toBe(true);
            expect(lockedLabel()).not.toBeNull();
            expect(meter()).toBeNull();
        });
    });

    describe("opening the storage settings", () => {
        beforeEach(() => {
            entitlements = {
                account: { storage_used: 1 * GB, max_storage: 5 * GB },
                organization: null,
            };
        });

        it("opens the storage tab for mailbox admins", () => {
            canManageAccesses = true;
            render();
            expect(gauge()?.classList.contains("quota-widget__gauge--clickable")).toBe(true);
            act(() => gauge()?.click());
            expect(openModal).toHaveBeenCalledExactlyOnceWith(MODAL_MAILBOX_SETTINGS_ID, { initialTab: "storage" });
        });

        it("is informational only for other members", () => {
            render();
            expect(gauge()?.classList.contains("quota-widget__gauge--clickable")).toBe(false);
            act(() => gauge()?.click());
            expect(openModal).not.toHaveBeenCalled();
        });
    });
});
