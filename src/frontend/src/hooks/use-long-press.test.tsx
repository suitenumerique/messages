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

/**
 * Handle on the hook under test. Published from an effect rather than during
 * render: writing to module scope while rendering is a side effect the lint
 * rules reject.
 */
const hook: { current: Api | null } = { current: null };
const api = () => hook.current!;

const Harness = () => {
    const value = useLongPress(onLongPress, {
        delay: 350,
        vibrate: false,
        moveTolerance: 10,
    });
    useEffect(() => {
        hook.current = value;
    });
    return null;
};

/** Minimal stand-in for the synthetic touch event the handlers read. */
const touchEvent = (x: number, y: number) =>
    ({ touches: [{ clientX: x, clientY: y }] }) as unknown as React.TouchEvent;

const touchStart = (x = 0, y = 0) =>
    act(() => {
        api().handlers.onTouchStart(touchEvent(x, y));
    });

const touchMove = (x: number, y: number) =>
    act(() => {
        api().handlers.onTouchMove(touchEvent(x, y));
    });

const wait = (ms: number) =>
    act(() => {
        vi.advanceTimersByTime(ms);
    });

beforeEach(() => {
    vi.useFakeTimers();
    onLongPress.mockClear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
        root.render(<Harness />);
    });
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
});
