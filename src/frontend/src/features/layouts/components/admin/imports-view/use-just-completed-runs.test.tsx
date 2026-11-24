import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminImportRun } from "@/features/api/gen";
import { useJustCompletedRuns } from "./use-just-completed-runs";

const fetchAPI = vi.fn();

vi.mock("@/features/api/fetch-api", () => ({
    fetchAPI: (...args: unknown[]) => fetchAPI(...args),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const run = (id: string, overrides: Partial<AdminImportRun> = {}) =>
    ({
        id,
        mailbox: `mailbox-${id}`,
        status: "running",
        is_active: true,
        ...overrides,
    }) as AdminImportRun;

/** Answers GET /mailboxes/{mailbox}/imports/{id}/ with the given final runs. */
const serveFinalStates = (finalRuns: Record<string, Partial<AdminImportRun> | "missing">) => {
    fetchAPI.mockImplementation(async (url: string) => {
        const id = Object.keys(finalRuns).find((runId) => url.endsWith(`/imports/${runId}/`));
        const finalRun = id ? finalRuns[id] : "missing";
        if (finalRun === "missing") throw new Error("Not found");
        return { status: 200, data: { id, ...finalRun }, headers: new Headers() };
    });
};

let latest: ReturnType<typeof useJustCompletedRuns>;
const report = (result: ReturnType<typeof useJustCompletedRuns>) => {
    latest = result;
};

const Probe = ({ rows, scopeKey, enabled }: { rows: AdminImportRun[] | undefined; scopeKey: string; enabled: boolean }) => {
    const result = useJustCompletedRuns(rows, scopeKey, enabled);
    useEffect(() => report(result));
    return null;
};

describe("useJustCompletedRuns", () => {
    let container: HTMLDivElement;
    let root: Root;

    const render = async (rows: AdminImportRun[] | undefined, { scopeKey = "scope", enabled = true } = {}) => {
        await act(async () => {
            root.render(<Probe rows={rows} scopeKey={scopeKey} enabled={enabled} />);
        });
        // Let the final-state requests resolve and their result render.
        await act(async () => {
            await Promise.resolve();
        });
    };

    const keptIds = () => latest.completedRuns.map((kept) => kept.id);

    beforeEach(() => {
        container = document.createElement("div");
        root = createRoot(container);
        fetchAPI.mockReset();
    });

    afterEach(() => {
        act(() => root.unmount());
    });

    it("keeps a run that completed with its final state", async () => {
        serveFinalStates({ a: { status: "completed", is_active: false, success_count: 42 } });

        await render([run("a"), run("b")]);
        await render([run("b")]);

        expect(keptIds()).toEqual(["a"]);
        expect(latest.completedRuns[0].success_count).toBe(42);
        // The list fields the single-run route lacks come from the last listed row.
        expect(latest.completedRuns[0].mailbox).toBe("mailbox-a");
    });

    it("drops a run that left for any other reason", async () => {
        serveFinalStates({
            failed: { status: "failed", is_active: false },
            moved: { status: "running", is_active: true },
            polling: { status: "completed", is_active: true },
            forgotten: "missing",
        });

        await render([run("failed"), run("moved"), run("polling"), run("forgotten")]);
        await render([]);

        expect(keptIds()).toEqual([]);
    });

    it("only checks the runs that were still progressing", async () => {
        await render([run("done", { status: "completed", is_active: false }), run("failed", { status: "failed" })]);
        await render([]);

        expect(fetchAPI).not.toHaveBeenCalled();
    });

    it("keeps nothing while disabled", async () => {
        await render([run("a")], { enabled: false });
        await render([], { enabled: false });

        expect(fetchAPI).not.toHaveBeenCalled();
        expect(keptIds()).toEqual([]);
    });

    it("starts over when the scope changes", async () => {
        serveFinalStates({
            a: { status: "completed", is_active: false },
            b: { status: "completed", is_active: false },
        });
        await render([run("a")]);
        await render([]);
        expect(keptIds()).toEqual(["a"]);

        await render([run("b")], { scopeKey: "page-2" });
        expect(keptIds()).toEqual([]);

        await render([], { scopeKey: "page-2" });
        expect(keptIds()).toEqual(["b"]);
    });

    it("ignores the rows of the previous scope still on screen", async () => {
        await render([run("a")]);
        // The next page loads while the list keeps showing the current one.
        await render(undefined, { scopeKey: "page-2" });
        await render([], { scopeKey: "page-2" });

        expect(fetchAPI).not.toHaveBeenCalled();
    });

    it("dismisses a kept run", async () => {
        serveFinalStates({ a: { status: "completed", is_active: false } });
        await render([run("a")]);
        await render([]);

        await act(async () => latest.dismiss("a"));

        expect(keptIds()).toEqual([]);
    });
});
