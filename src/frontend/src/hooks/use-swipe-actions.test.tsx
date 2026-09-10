import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useSwipeActions, closeSwipedRows } from "./use-swipe-actions";

// Opts this file into React's act() support; without it every render logs
// "the current testing environment is not configured to support act(...)".
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ROW_WIDTH = 400;
const START_WIDTH = 76;
const END_WIDTH = 152;
/** ROW_WIDTH × the default commitRatio. */
const COMMIT_DISTANCE = 160;

type Api = ReturnType<typeof useSwipeActions>;

let container: HTMLDivElement;
let root: Root;
const onCommitStart = vi.fn();

const hook: { current: Api | null } = { current: null };
const api = () => hook.current!;

const Harness = ({ enabled = true }: { enabled?: boolean }) => {
    const value = useSwipeActions({
        startWidth: START_WIDTH,
        endWidth: END_WIDTH,
        onCommitStart,
        enabled,
    });
    useEffect(() => {
        hook.current = value;
    });
    return (
        <div ref={value.containerRef} data-testid="row">
            <div ref={value.contentRef} data-testid="content" />
        </div>
    );
};

const row = () => container.querySelector<HTMLElement>('[data-testid="row"]')!;
const content = () => container.querySelector<HTMLElement>('[data-testid="content"]')!;

/**
 * jsdom ships no TouchEvent constructor, and the gesture only reads `touches`
 * and `timeStamp` off the event — a plain Event carrying those is enough.
 * Every event shares a timestamp so the velocity stays at 0 and the assertions
 * are about travelled distance only.
 */
const dispatchTouch = (type: string, points: { x: number; y: number }[]) => {
    const event = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(event, "touches", {
        value: points.map(({ x, y }) => ({ clientX: x, clientY: y })),
    });
    Object.defineProperty(event, "timeStamp", { value: 0 });
    act(() => {
        row().dispatchEvent(event);
    });
    return event;
};

/**
 * The move that locks the axis only arms the gesture — it mounts the action
 * panels and rebases the travel on the finger's current position — so a swipe
 * needs one move to arm and the following ones to travel.
 */
const swipe = (points: { x: number; y: number }[]) => {
    dispatchTouch("touchstart", [points[0]]);
    points.slice(1).forEach((point) => dispatchTouch("touchmove", [point]));
};

const release = () => dispatchTouch("touchend", []);

const translateX = () => content().style.transform;
const side = () => row().dataset.side;
const willCommit = () => row().dataset.willCommit;

const render = (enabled = true) => {
    act(() => {
        root.render(<Harness enabled={enabled} />);
    });
    // Rows are measured against their own width; jsdom reports 0 for every
    // layout box, which would put the commit threshold at 0 px.
    Object.defineProperty(row(), "offsetWidth", { value: ROW_WIDTH, configurable: true });
};

beforeEach(() => {
    onCommitStart.mockClear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    render();
});

afterEach(() => {
    act(() => {
        root.unmount();
    });
    container.remove();
});

describe("useSwipeActions", () => {
    it("opens the trailing panel when the swipe passes half its width", () => {
        swipe([
            { x: 300, y: 100 },
            { x: 290, y: 100 },
            { x: 190, y: 100 },
        ]);
        expect(translateX()).toBe("translate3d(-100px, 0, 0)");
        release();
        expect(api().openSide).toBe("end");
        expect(translateX()).toBe(`translate3d(-${END_WIDTH}px, 0, 0)`);
    });

    it("springs back when the swipe stays short of half the panel", () => {
        swipe([
            { x: 300, y: 100 },
            { x: 290, y: 100 },
            { x: 270, y: 100 },
        ]);
        release();
        expect(api().openSide).toBeNull();
        expect(translateX()).toBe("");
    });

    it("waits for the move after the axis lock before displacing the row", () => {
        swipe([
            { x: 300, y: 100 },
            { x: 280, y: 100 },
        ]);
        // The panels mount on this state change; sliding already would uncover
        // an empty gap for a frame.
        expect(api().isSwiping).toBe(true);
        expect(translateX()).toBe("");
        // Travel is rebased on the lock position, so the row does not jump by
        // the threshold it just crossed.
        dispatchTouch("touchmove", [{ x: 250, y: 100 }]);
        expect(translateX()).toBe("translate3d(-30px, 0, 0)");
    });

    it("leaves vertical moves to the list scroll", () => {
        dispatchTouch("touchstart", [{ x: 300, y: 100 }]);
        const move = dispatchTouch("touchmove", [{ x: 295, y: 160 }]);
        expect(move.defaultPrevented).toBe(false);
        expect(api().isSwiping).toBe(false);
        expect(translateX()).toBe("");
        release();
        expect(api().openSide).toBeNull();
    });

    it("claims the gesture once the horizontal axis is locked", () => {
        dispatchTouch("touchstart", [{ x: 300, y: 100 }]);
        const move = dispatchTouch("touchmove", [{ x: 340, y: 105 }]);
        expect(move.defaultPrevented).toBe(true);
    });

    it("publishes the side being pulled, so only its panel shows", () => {
        expect(side()).toBeFalsy();
        swipe([
            { x: 300, y: 100 },
            { x: 290, y: 100 },
            { x: 190, y: 100 },
        ]);
        expect(side()).toBe("end");
        dispatchTouch("touchmove", [{ x: 350, y: 100 }]);
        expect(side()).toBe("start");
        release();
    });

    it("runs the leading action when the row is pulled past the commit threshold", () => {
        swipe([
            { x: 50, y: 100 },
            { x: 60, y: 100 },
            { x: 60 + COMMIT_DISTANCE + 10, y: 100 },
        ]);
        expect(willCommit()).toBe("true");
        release();
        expect(onCommitStart).toHaveBeenCalledTimes(1);
        // Read/unread does not remove the row: it snaps back into place.
        expect(api().openSide).toBeNull();
        expect(translateX()).toBe("");
        expect(willCommit()).toBe("false");
    });

    it("only reveals the leading action when the pull stops short", () => {
        swipe([
            { x: 50, y: 100 },
            { x: 60, y: 100 },
            { x: 110, y: 100 },
        ]);
        // Never armed, so the attribute was never written at all.
        expect(willCommit()).not.toBe("true");
        release();
        expect(onCommitStart).not.toHaveBeenCalled();
        expect(api().openSide).toBe("start");
        expect(translateX()).toBe(`translate3d(${START_WIDTH}px, 0, 0)`);
    });

    it("damps the pull past the trailing panel instead of following the finger", () => {
        swipe([
            { x: 300, y: 100 },
            { x: 290, y: 100 },
            { x: 90, y: 100 },
        ]);
        // 200px of travel for a 152px panel: the extra 48px are resisted.
        expect(translateX()).toBe("translate3d(-166.4px, 0, 0)");
    });

    it("closes on demand, whatever left it open", () => {
        swipe([
            { x: 300, y: 100 },
            { x: 290, y: 100 },
            { x: 190, y: 100 },
        ]);
        release();
        expect(api().openSide).toBe("end");
        act(() => closeSwipedRows());
        expect(api().openSide).toBeNull();
        expect(translateX()).toBe("");
        expect(side()).toBe("");
    });

    it("ignores touches when disabled", () => {
        render(false);
        swipe([
            { x: 300, y: 100 },
            { x: 290, y: 100 },
            { x: 190, y: 100 },
        ]);
        release();
        expect(api().openSide).toBeNull();
        expect(translateX()).toBe("");
    });
});
