import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import {
    invalidateMaildomainMessageTemplates,
    invalidateMailboxMessageTemplates,
} from "./message-templates-cache";

const KEYS = {
    m1List: ["/api/v1.0/mailboxes/m1/message-templates/", { type: ["signature"] }],
    m1Available: ["/api/v1.0/mailboxes/m1/message-templates/available/", { type: "signature" }],
    m1Render: ["/api/v1.0/mailboxes/m1/message-templates/t1/render/", { message_id: "d1" }],
    m1Threads: ["/api/v1.0/mailboxes/m1/threads/"],
    m2Available: ["/api/v1.0/mailboxes/m2/message-templates/available/"],
    d1List: ["/api/v1.0/maildomains/d1/message-templates/"],
    d2List: ["/api/v1.0/maildomains/d2/message-templates/"],
};

const seed = () => {
    const queryClient = new QueryClient();
    Object.values(KEYS).forEach((key) => queryClient.setQueryData(key, []));
    return queryClient;
};

const invalidated = (queryClient: QueryClient) =>
    Object.fromEntries(
        Object.entries(KEYS).map(([name, key]) => [name, queryClient.getQueryState(key)?.isInvalidated]),
    );

describe("invalidateMailboxMessageTemplates", () => {
    it("invalidates the lists, the available templates and the renders of the mailbox only", async () => {
        const queryClient = seed();
        await invalidateMailboxMessageTemplates(queryClient, "m1");
        expect(invalidated(queryClient)).toEqual({
            m1List: true,
            m1Available: true,
            m1Render: true,
            m1Threads: false,
            m2Available: false,
            d1List: false,
            d2List: false,
        });
    });
});

describe("invalidateMaildomainMessageTemplates", () => {
    it("invalidates the domain templates and the template queries of every mailbox", async () => {
        const queryClient = seed();
        await invalidateMaildomainMessageTemplates(queryClient, "d1");
        expect(invalidated(queryClient)).toEqual({
            m1List: true,
            m1Available: true,
            m1Render: true,
            m1Threads: false,
            m2Available: true,
            d1List: true,
            d2List: false,
        });
    });

    it("still refreshes the mailboxes when no domain is selected", async () => {
        const queryClient = seed();
        await invalidateMaildomainMessageTemplates(queryClient, undefined);
        expect(invalidated(queryClient)).toMatchObject({ m1Available: true, d1List: false });
    });
});
