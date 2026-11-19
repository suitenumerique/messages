import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { focusManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
    getLabelsStatsRetrieveUrl,
    getThreadsStatsRetrieveUrl,
    LabelsStatsRetrieveStatsFieldsItem,
    ThreadsStatsRetrieveStatsFieldsItem,
} from "@/features/api/gen";
import { invalidateLabelsStats, LABELS_STATS_STALE_TIME, useLabelStats } from "./use-labels-stats";
import { getThreadsStatsQueryKey, useFolderStats } from "./use-threads-stats";

const fetchAPI = vi.fn();

vi.mock("@/features/api/fetch-api", () => ({
    fetchAPI: (...args: unknown[]) => fetchAPI(...args),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const MAILBOX_ID = "mailbox-1";
const INBOX = { filter: { has_active: "1" } };

let labelsUnread: Record<string, number> = {};
let inboxUnread = 0;

const LabelBadge = ({ mailboxId, labelId }: { mailboxId: string; labelId: string }) => {
    const stats = useLabelStats({
        mailboxId,
        labelId,
        fields: [LabelsStatsRetrieveStatsFieldsItem.all_unread],
    });
    return <span data-testid={labelId}>{stats?.all_unread ?? 0}</span>;
};

const InboxBadge = () => {
    const { count } = useFolderStats(MAILBOX_ID, INBOX);
    return <span data-testid="inbox">{count ?? 0}</span>;
};

const labelsStatsCalls = () =>
    fetchAPI.mock.calls.filter(([url]) => String(url).includes("/labels/stats/")).length;
const threadsStatsCalls = () =>
    fetchAPI.mock.calls.filter(([url]) => String(url).includes("/threads/stats/")).length;

describe("useLabelStats", () => {
    let container: HTMLDivElement;
    let root: Root;
    let queryClient: QueryClient;

    // React Query starts refetches on timers; once they settle, rendering
    // their result takes a further timer. The extra millisecond only ever
    // delays the deadlines the tests observe.
    const advance = async (ms: number) => {
        await act(async () => {
            await vi.advanceTimersByTimeAsync(ms);
            await Promise.allSettled(queryClient.getQueryCache().getAll().map((query) => query.promise));
        });
        await act(() => vi.advanceTimersByTimeAsync(1));
    };

    const render = async (labelIds: string[], { withInbox = false, mailboxId = MAILBOX_ID } = {}) => {
        await act(async () => {
            root.render(
                <QueryClientProvider client={queryClient}>
                    {withInbox && <InboxBadge />}
                    {labelIds.map((labelId) => (
                        <LabelBadge key={labelId} mailboxId={mailboxId} labelId={labelId} />
                    ))}
                </QueryClientProvider>,
            );
        });
        await advance(0);
    };

    const invalidate = async () => {
        await act(async () => {
            await invalidateLabelsStats(queryClient);
        });
        await advance(0);
    };

    const badge = (testId: string) =>
        container.querySelector(`[data-testid="${testId}"]`)?.textContent;

    beforeEach(() => {
        vi.useFakeTimers();
        labelsUnread = { "label-1": 3, "label-2": 1 };
        inboxUnread = 5;
        fetchAPI.mockReset();
        fetchAPI.mockImplementation(async (url: string) => ({
            status: 200,
            data: url.includes("/labels/stats/")
                ? Object.fromEntries(
                    Object.entries(labelsUnread).map(([id, all_unread]) => [id, { all_unread }]),
                )
                : { all_unread: inboxUnread },
            headers: new Headers(),
        }));
        queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
        queryClient.clear();
        focusManager.setFocused(undefined);
        vi.useRealTimers();
    });

    it("fetches the counts of every label in a single request", async () => {
        await render(["label-1", "label-2", "label-3"]);

        expect(labelsStatsCalls()).toBe(1);
        expect(badge("label-1")).toBe("3");
        expect(badge("label-2")).toBe("1");
        // Labels without threads are absent from the response.
        expect(badge("label-3")).toBe("0");
    });

    it("reuses fresh counts when more labels mount", async () => {
        await render(["label-1"]);
        await advance(LABELS_STATS_STALE_TIME / 2);

        await render(["label-1", "label-2"]);

        expect(labelsStatsCalls()).toBe(1);
        expect(badge("label-2")).toBe("1");
    });

    it("refetches within a second when invalidated after the stale time", async () => {
        await render(["label-1"]);
        await advance(LABELS_STATS_STALE_TIME);
        labelsUnread = { "label-1": 2 };

        await invalidate();
        await advance(1_000);

        expect(labelsStatsCalls()).toBe(2);
        expect(badge("label-1")).toBe("2");
    });

    it("defers the refetch of a hidden tab until it is visible again", async () => {
        await render(["label-1"]);
        await advance(LABELS_STATS_STALE_TIME);
        act(() => focusManager.setFocused(false));
        labelsUnread = { "label-1": 2 };

        await invalidate();
        await advance(LABELS_STATS_STALE_TIME * 2);
        expect(labelsStatsCalls()).toBe(1);

        act(() => focusManager.setFocused(true));
        await advance(1_000);

        expect(labelsStatsCalls()).toBe(2);
        expect(badge("label-1")).toBe("2");
    });

    it("throttles invalidations to one request per stale time", async () => {
        await render(["label-1"]);
        await advance(5_000);

        labelsUnread = { "label-1": 2 };
        await invalidate();
        await advance(5_000);
        labelsUnread = { "label-1": 1 };
        await invalidate();
        await advance(5_000);
        labelsUnread = { "label-1": 0 };
        await invalidate();

        expect(labelsStatsCalls()).toBe(1);
        expect(badge("label-1")).toBe("3");

        // The trailing request carries the last change.
        await advance(LABELS_STATS_STALE_TIME - 15_000);
        expect(labelsStatsCalls()).toBe(2);
        expect(badge("label-1")).toBe("0");

        // Without new change, the counts are not polled.
        await advance(LABELS_STATS_STALE_TIME * 3);
        expect(labelsStatsCalls()).toBe(2);
    });

    it("refreshes the counts of a mailbox left before the invalidation landed", async () => {
        await render(["label-1"]);
        await render(["label-1"], { mailboxId: "mailbox-2" });
        expect(labelsStatsCalls()).toBe(2);

        // A change made in the first mailbox settles once the second is shown.
        labelsUnread = { "label-1": 2 };
        await invalidate();
        await render(["label-1"]);

        expect(labelsStatsCalls()).toBe(3);
        expect(badge("label-1")).toBe("2");
    });

    it("is left out of folder stats invalidations", async () => {
        await render(["label-1"], { withInbox: true });
        inboxUnread = 4;

        await act(async () => {
            await queryClient.invalidateQueries({ queryKey: getThreadsStatsQueryKey(MAILBOX_ID) });
        });
        await advance(LABELS_STATS_STALE_TIME * 2);

        expect(threadsStatsCalls()).toBe(2);
        expect(badge("inbox")).toBe("4");
        expect(labelsStatsCalls()).toBe(1);
    });
});

describe("stats_fields serialization", () => {
    // The backend reads a single comma-separated value: a repeated parameter
    // would only count its last field.
    it.each([
        ["labels", () => getLabelsStatsRetrieveUrl({
            mailbox_id: MAILBOX_ID,
            stats_fields: [LabelsStatsRetrieveStatsFieldsItem.all, LabelsStatsRetrieveStatsFieldsItem.all_unread],
        })],
        ["threads", () => getThreadsStatsRetrieveUrl({
            mailbox_id: MAILBOX_ID,
            stats_fields: [ThreadsStatsRetrieveStatsFieldsItem.all, ThreadsStatsRetrieveStatsFieldsItem.all_unread],
        })],
    ])("sends the %s stats fields as one comma-separated value", (_, getUrl) => {
        const params = new URL(getUrl(), "http://localhost").searchParams;
        expect(params.getAll("stats_fields")).toEqual(["all,all_unread"]);
    });
});
