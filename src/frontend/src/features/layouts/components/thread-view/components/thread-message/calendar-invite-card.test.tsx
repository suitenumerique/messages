import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Attachment } from "@/features/api/gen/models/attachment";

const attachmentList = vi.fn();
let inviteThrows = false;

vi.mock("@/features/utils/errors", () => ({ handle: vi.fn() }));
vi.mock("../calendar-invite", () => ({
    CalendarInvite: () => {
        // What an invalid invite date does when formatted.
        if (inviteThrows) new Date("garbage").toISOString();
        return <p>invite card</p>;
    },
}));
vi.mock("../thread-attachment-list", () => ({
    AttachmentList: (props: { attachments: Attachment[]; spamReport: string | null }) => {
        attachmentList(props);
        return <p>attachment {props.attachments[0].name}</p>;
    },
}));

import { CalendarInviteCard } from "./calendar-invite-card";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const INVITE = { name: "invite.ics", size: 10, blobId: "b1" } as Attachment;

describe("CalendarInviteCard", () => {
    let container: HTMLDivElement;
    let root: Root;

    const render = (spamReport: "message" | null = null) =>
        act(() => root.render(<CalendarInviteCard attachment={INVITE} spamReport={spamReport} />));

    beforeEach(() => {
        vi.clearAllMocks();
        vi.spyOn(console, "error").mockImplementation(() => {});
        inviteThrows = false;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
        vi.restoreAllMocks();
    });

    it("renders the invite card when it works", () => {
        render();
        expect(container.textContent).toBe("invite card");
        expect(attachmentList).not.toHaveBeenCalled();
    });

    it("falls back to the file as an attachment when the invite throws", () => {
        inviteThrows = true;
        render();
        expect(container.textContent).toBe("attachment invite.ics");
        expect(attachmentList).toHaveBeenCalledWith({ attachments: [INVITE], spamReport: null });
    });

    it("keeps the spam lock on the fallback", () => {
        inviteThrows = true;
        render("message");
        expect(attachmentList).toHaveBeenCalledWith({ attachments: [INVITE], spamReport: "message" });
    });
});
