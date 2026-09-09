import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useLongPress } from "./use-long-press";

// Opts this file into React's act() support; without it every render logs
// "the current testing environment is not configured to support act(...)".
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Api = ReturnType<typeof useLongPress>;

let container: HTMLDivElement;
let root: Root;
const onLongPress = vi.fn();
const onTap = vi.fn();

/**
 * Handle on the hook under test. Published from an effect rather than during
 * render: writing to module scope while rendering is a side effect the lint
 * rules reject.
 */
const hook: { current: Api | null } = { current: null };
const api = () => hook.current!;

const Harness = ({ tap }: { tap?: () => void }) => {
    const value = useLongPress(onLongPress, {
        delay: 350,
        vibrate: false,
        moveTolerance: 10,
        onTap: tap,
    });
    useEffect(() => {
        hook.current = value;
    });
    return null;
};

const render = (tap?: () => void) =>
    act(() => {
        root.render(<Harness tap={tap} />);
    });

/** Minimal stand-in for the synthetic touch event the handlers read. */
const touchEvent = (x: number, y: number) =>
    ({ touches: [{ clientX: x, clientY: y }] }) as unknown as React.TouchEvent;

const preventDefault = vi.fn();
const endEvent = () =>
    ({ cancelable: true, preventDefault }) as unknown as React.TouchEvent;

const touchStart = (x = 0, y = 0) =>
    act(() => {
        api().handlers.onTouchStart(touchEvent(x, y));
    });

const touchMove = (x: number, y: number) =>
    act(() => {
        api().handlers.onTouchMove(touchEvent(x, y));
    });

const touchEnd = () =>
    act(() => {
        api().handlers.onTouchEnd(endEvent());
    });

const wait = (ms: number) =>
    act(() => {
        vi.advanceTimersByTime(ms);
    });

beforeEach(() => {
    vi.useFakeTimers();
    onLongPress.mockClear();
    onTap.mockClear();
    preventDefault.mockClear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    render(onTap);
});

afterEach(() => {
    act(() => {
        root.unmount();
    });
    container.remove();
    vi.useRealTimers();
});

describe("useLongPress", () => {
    it("fires after the delay when the finger stays down", () => {
        touchStart(20, 40);
        wait(349);
        expect(onLongPress).not.toHaveBeenCalled();
        wait(1);
        expect(onLongPress).toHaveBeenCalledWith({ x: 20, y: 40 });
    });

    it("survives the small drift of a finger holding still", () => {
        touchStart(20, 40);
        touchMove(24, 44);
        wait(350);
        expect(onLongPress).toHaveBeenCalledTimes(1);
    });

    it("aborts once the move reads as a scroll", () => {
        touchStart(20, 40);
        touchMove(20, 80);
        wait(350);
        expect(onLongPress).not.toHaveBeenCalled();
    });

    it("does not re-arm when the finger comes back after a scroll", () => {
        touchStart(20, 40);
        touchMove(20, 80);
        touchMove(20, 41);
        wait(350);
        expect(onLongPress).not.toHaveBeenCalled();
    });

    it("does not fire again for a move made after the press", () => {
        touchStart(20, 40);
        wait(350);
        touchMove(20, 200);
        wait(350);
        expect(onLongPress).toHaveBeenCalledTimes(1);
    });

    describe("tap handling", () => {
        it("taps when the finger leaves before the delay", () => {
            touchStart(20, 40);
            wait(100);
            touchEnd();
            expect(onTap).toHaveBeenCalledTimes(1);
            expect(onLongPress).not.toHaveBeenCalled();
        });

        it("does not tap when the release ends a long press", () => {
            touchStart(20, 40);
            wait(350);
            touchEnd();
            expect(onLongPress).toHaveBeenCalledTimes(1);
            expect(onTap).not.toHaveBeenCalled();
        });

        it("does not tap when the press ended up being a scroll", () => {
            touchStart(20, 40);
            touchMove(20, 80);
            touchEnd();
            expect(onTap).not.toHaveBeenCalled();
        });

        it("drops the compatibility click of a resolved gesture", () => {
            touchStart(20, 40);
            wait(100);
            touchEnd();
            expect(preventDefault).toHaveBeenCalledTimes(1);
            expect(api().isTouchHandled()).toBe(true);
        });

        it("stops claiming clicks once the compatibility window is over", () => {
            touchStart(20, 40);
            wait(100);
            touchEnd();
            wait(700);
            expect(api().isTouchHandled()).toBe(false);
        });

        it("leaves the release alone when no tap handler is given", () => {
            render(undefined);
            touchStart(20, 40);
            wait(100);
            touchEnd();
            expect(preventDefault).not.toHaveBeenCalled();
            expect(onTap).not.toHaveBeenCalled();
        });
    });

    it("does not carry over the timer of a press whose release never came", () => {
        touchStart(20, 40);
        wait(300);
        // No touchend: the menu the press opened swallowed the rest of the
        // sequence. The next, short, press must not inherit that timer.
        touchStart(20, 40);
        wait(100);
        touchEnd();
        expect(onLongPress).not.toHaveBeenCalled();
        expect(onTap).toHaveBeenCalledTimes(1);
    });
});
