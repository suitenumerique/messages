import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const handle = vi.fn();
vi.mock("@/features/utils/errors", () => ({ handle: (error: Error) => handle(error) }));

import ErrorBoundary from "./error-boundary";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const Throws = () => {
    // What an invalid invite date does in the calendar card.
    new Date("garbage").toISOString();
    return null;
};

describe("ErrorBoundary", () => {
    let container: HTMLDivElement;
    let root: Root;

    beforeEach(() => {
        vi.spyOn(console, "error").mockImplementation(() => {});
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(() => {
        act(() => root.unmount());
        container.remove();
        vi.restoreAllMocks();
    });

    it("renders the fallback in place of a child that throws, and leaves its siblings", () => {
        act(() =>
            root.render(
                <>
                    <ErrorBoundary fallback={<a href="/download">invite.ics</a>}>
                        <Throws />
                    </ErrorBoundary>
                    <p>message body</p>
                </>,
            ),
        );

        expect(container.querySelector("a")?.textContent).toBe("invite.ics");
        expect(container.textContent).toContain("message body");
        expect(handle).toHaveBeenCalledWith(expect.any(RangeError));
    });

    it("renders the child when it does not throw", () => {
        act(() =>
            root.render(
                <ErrorBoundary fallback={<a href="/download">invite.ics</a>}>
                    <p>invite card</p>
                </ErrorBoundary>,
            ),
        );

        expect(container.textContent).toBe("invite card");
    });
});
