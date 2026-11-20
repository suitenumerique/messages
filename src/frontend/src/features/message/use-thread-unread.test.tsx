import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { Thread } from "@/features/api/gen/models";

vi.mock("@tanstack/react-router", () => ({ useParams: () => ({ mailboxId: "mailbox-1" }) }));

import useThreadUnread from "./use-thread-unread";

const isUnread = (messagedAt: string, readAt: string | null): boolean => {
    const thread = {
        messaged_at: messagedAt,
        accesses: [{ mailbox: { id: "mailbox-1" }, read_at: readAt }],
    } as unknown as Thread;
    let result: boolean | undefined;
    const Probe = () => {
        result = useThreadUnread(thread);
        return null;
    };
    const root = createRoot(document.createElement("div"));
    act(() => root.render(<Probe />));
    act(() => root.unmount());
    return result!;
};

describe("useThreadUnread", () => {
    it("compares below the millisecond, as the backend does", () => {
        // The backend's ``read_at < messaged_at``, with both in the same ms.
        expect(isUnread("2026-11-17T12:00:00.123456Z", "2026-11-17T12:00:00.123455Z")).toBe(true);
        expect(isUnread("2026-11-17T12:00:00.123456Z", "2026-11-17T12:00:00.123456Z")).toBe(false);
    });
});
