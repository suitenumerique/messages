import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Thread } from "@/features/api/gen/models/thread";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// The provider reads the route; the hook only needs its state, faked here.
const focusState = {
    focusedThreadId: null as string | null,
    ownsFocusRef: { current: false },
    lastFocusedIndexRef: { current: 0 },
    lastOpenedThreadIdRef: { current: null as string | null },
};
const setFocusedThreadId = vi.fn((next: string | null) => {
    focusState.focusedThreadId = next;
});

vi.mock("@/features/providers/thread-listbox-focus", () => ({
    useThreadListboxFocus: () => ({ ...focusState, setFocusedThreadId }),
}));

vi.mock("@/features/providers/thread-selection", () => ({
    useThreadSelection: () => ({ toggleThread: vi.fn(), selectRange: vi.fn() }),
}));

import { useThreadListbox } from "./use-thread-listbox";

const threads = ["t1", "t2", "t3"].map((id) => ({ id }) as Thread);

let container: HTMLDivElement;
let root: Root;

/**
 * The list, plus a field the focus can be parked in. `tick` only changes
 * between renders, to drive an "update" run of the restore effect.
 */
const Listbox = ({ tick }: { tick: number }) => {
    const { getItemProps, onKeyDown, onBlur } = useThreadListbox(threads);
    return (
        <>
            <input id="search" />
            <div role="listbox" data-tick={tick} onKeyDown={onKeyDown} onBlur={onBlur}>
                {threads.map((thread) => {
                    const { tabIndex, itemRef, onFocusItem } = getItemProps(thread.id);
                    return (
                        <a key={thread.id} id={thread.id} href="#" role="option" tabIndex={tabIndex} ref={itemRef} onFocus={onFocusItem}>
                            {thread.id}
                        </a>
                    );
                })}
            </div>
        </>
    );
};

const mount = () =>
    act(() => {
        root.render(<Listbox tick={0} />);
    });

const update = () =>
    act(() => {
        root.render(<Listbox tick={1} />);
    });

const byId = (id: string) => document.getElementById(id)!;

beforeEach(() => {
    focusState.focusedThreadId = null;
    focusState.ownsFocusRef.current = false;
    focusState.lastFocusedIndexRef.current = 0;
    focusState.lastOpenedThreadIdRef.current = null;
    setFocusedThreadId.mockClear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => {
        root.unmount();
    });
    document.body.innerHTML = "";
});

describe("useThreadListbox focus restoration", () => {
    it("takes the focus back on mount when nobody holds it, on the row of the thread just closed", () => {
        // Desktop split view: the Close button blurred the list (flag down),
        // then went away with the thread view.
        focusState.focusedThreadId = "t1";
        focusState.lastOpenedThreadIdRef.current = "t2";
        (document.activeElement as HTMLElement | null)?.blur();

        mount();

        expect(document.activeElement).toBe(byId("t2"));
        expect(focusState.ownsFocusRef.current).toBe(true);
        expect(setFocusedThreadId).toHaveBeenCalledWith("t2");
    });

    it("falls back on the focused row, then the first one, when the closed thread left the list", () => {
        focusState.focusedThreadId = "t3";
        focusState.lastOpenedThreadIdRef.current = "gone";
        mount();
        expect(document.activeElement).toBe(byId("t3"));

        act(() => root.unmount());
        root = createRoot(container);
        focusState.focusedThreadId = null;
        focusState.ownsFocusRef.current = false;
        (document.activeElement as HTMLElement | null)?.blur();
        mount();
        expect(document.activeElement).toBe(byId("t1"));
    });

    it("leaves a focus parked elsewhere alone, on mount and on updates", () => {
        focusState.focusedThreadId = "t1";
        focusState.lastOpenedThreadIdRef.current = "t1";
        mount();
        byId("search").focus();
        // A deliberate move out of the list: the flag is down.
        focusState.ownsFocusRef.current = false;

        update();

        expect(document.activeElement).toBe(byId("search"));
    });

    it("does not reclaim a focus dropped on <body> on updates unless it owned it", () => {
        focusState.focusedThreadId = "t1";
        mount();
        expect(document.activeElement).toBe(byId("t1"));

        // The user moved on (say, clicked a non-focusable area): the flag is
        // down and the focus sits on <body>. A list re-render must not grab it.
        (document.activeElement as HTMLElement | null)?.blur();
        focusState.ownsFocusRef.current = false;
        update();
        expect(document.activeElement).toBe(document.body);

        // Owned focus lost to a re-render is restored, as before.
        focusState.ownsFocusRef.current = true;
        update();
        expect(document.activeElement).toBe(byId("t1"));
    });
});
