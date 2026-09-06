import { useCallback, useEffect, useRef, useState } from "react";

import { triggerHaptic } from "@/features/native/haptics";
import { updateVelocity } from "./use-drag-gesture";

/**
 * Which action panel a row reveals: "start" sits before the row (revealed by
 * swiping right), "end" after it (revealed by swiping left).
 */
export type SwipeSide = "start" | "end";

type UseSwipeActionsOptions = {
    /** Reveal width (px) of the leading panel; 0 disables swiping right. */
    startWidth: number;
    /** Reveal width (px) of the trailing panel; 0 disables swiping left. */
    endWidth: number;
    /**
     * Fraction of the row width past which releasing runs the leading action
     * straight away, instead of leaving the panel open.
     */
    commitRatio?: number;
    /** Runs when the release passes the commit threshold (leading side only). */
    onCommitStart?: () => void;
    enabled?: boolean;
};

type UseSwipeActionsResult = {
    /** Ref for the touch surface — the whole row. */
    containerRef: (node: HTMLElement | null) => void;
    /** Ref for the element translated by the gesture — the row content. */
    contentRef: (node: HTMLElement | null) => void;
    /** Panel currently held open, or null when the row sits at rest. */
    openSide: SwipeSide | null;
    /** True while a finger drives the row horizontally. */
    isSwiping: boolean;
    /** Snaps the row back to rest (animated). */
    close: () => void;
};

/**
 * Movement (px) before the gesture decides between scrolling and swiping.
 * Shared with the pull-to-refresh gesture, which arbitrates the same finger:
 * were the two thresholds to drift apart, a diagonal move could arm both.
 */
export const AXIS_LOCK_THRESHOLD = 8;
/** Damping applied past a panel's reveal width, so the pull feels elastic. */
const OVERSHOOT_RESISTANCE = 0.3;
/** Flick velocity (px/ms) that opens or commits regardless of distance. */
const FLICK_VELOCITY = 0.5;

/**
 * Rows opened by a swipe, so opening one closes the others. A module-level
 * registry rather than React state on purpose: closing a sibling must not
 * re-render the whole thread list on every gesture.
 */
const openRows = new Set<() => void>();

/** Closes every open row — e.g. when the list scrolls under the finger. */
export const closeSwipedRows = () => {
    openRows.forEach((close) => close());
};

/**
 * Horizontal swipe-to-reveal gesture for a list row.
 *
 * Raw touch listeners (not React handlers) because the gesture needs a
 * non-passive `touchmove` to suppress the vertical scroll once the horizontal
 * axis is locked — React attaches touch listeners passively.
 *
 * **Nothing about the moving row goes through React state.** The translation,
 * the side being pulled and the commit threshold are all written straight to
 * the DOM as inline transforms and data attributes, which the stylesheet reads.
 * A row carries a lot of markup, and re-rendering it on every frame is what
 * makes the swipe stutter on low-end devices. React only hears about the two
 * discrete transitions of the gesture — it starting, and the row settling
 * open or closed — which is what mounts and unmounts the action panels.
 */
export const useSwipeActions = ({
    startWidth,
    endWidth,
    commitRatio = 0.4,
    onCommitStart,
    enabled = true,
}: UseSwipeActionsOptions): UseSwipeActionsResult => {
    const [container, setContainer] = useState<HTMLElement | null>(null);
    const [openSide, setOpenSide] = useState<SwipeSide | null>(null);
    const [isSwiping, setIsSwiping] = useState(false);

    const containerNode = useRef<HTMLElement | null>(null);
    const contentNode = useRef<HTMLElement | null>(null);
    const offsetRef = useRef(0);
    const willCommitRef = useRef(false);

    // Mirrors of the props read inside the long-lived listeners, so the
    // listeners stay attached across renders instead of being torn down and
    // rebound mid-gesture.
    const optionsRef = useRef({ startWidth, endWidth, commitRatio, onCommitStart });
    optionsRef.current = { startWidth, endWidth, commitRatio, onCommitStart };

    /**
     * Writes the whole visual state of the row in one go. `data-side` lets the
     * stylesheet hide the panel that is *not* being pulled: both are laid out
     * across the full width, so without it a long pull uncovers the far side's
     * buttons behind the row.
     */
    const setOffset = useCallback((value: number, animate: boolean) => {
        offsetRef.current = value;
        const content = contentNode.current;
        if (content) {
            // Emptying the inline transition hands the property back to the
            // stylesheet, which owns the snap-back easing.
            content.style.transition = animate ? "" : "none";
            content.style.transform = value === 0 ? "" : `translate3d(${value}px, 0, 0)`;
        }
        const node = containerNode.current;
        if (node) {
            const side = value > 0 ? "start" : value < 0 ? "end" : "";
            if (node.dataset.side !== side) node.dataset.side = side;
        }
    }, []);

    const setWillCommit = useCallback((value: boolean) => {
        if (willCommitRef.current === value) return;
        willCommitRef.current = value;
        const node = containerNode.current;
        if (node) node.dataset.willCommit = String(value);
        if (value) triggerHaptic(10, "LIGHT");
    }, []);

    const close = useCallback(() => {
        setOffset(0, true);
        setWillCommit(false);
        setOpenSide(null);
    }, [setOffset, setWillCommit]);

    const setContainerNode = useCallback((node: HTMLElement | null) => {
        containerNode.current = node;
        setContainer(node);
    }, []);

    const setContentNode = useCallback((node: HTMLElement | null) => {
        contentNode.current = node;
        // Re-applied because the panels mounting (or a list refetch) can hand
        // us a fresh node while the row is already displaced.
        if (node && offsetRef.current !== 0) {
            node.style.transform = `translate3d(${offsetRef.current}px, 0, 0)`;
        }
    }, []);

    // A row that closes for any reason (release, sibling opening, unmount)
    // must leave the registry, otherwise `closeSwipedRows` keeps calling into
    // a dead component.
    useEffect(() => {
        if (openSide === null) return;
        openRows.add(close);
        return () => {
            openRows.delete(close);
        };
    }, [openSide, close]);

    useEffect(() => {
        if (!enabled || !container) return;

        let axis: "x" | "y" | null = null;
        let startX = 0;
        let startY = 0;
        let baseOffset = 0;
        let lastX = 0;
        let lastTime = 0;
        let velocity = 0;
        let committing = false;

        /** Clamps the raw finger travel to what each side allows to show. */
        const resolveOffset = (raw: number) => {
            const { startWidth: start, endWidth: end } = optionsRef.current;
            if (raw > 0) {
                if (start === 0) return 0;
                // The leading side stays free past its reveal width: that
                // travel is what arms the commit.
                return Math.min(raw, container.offsetWidth);
            }
            if (end === 0) return 0;
            // The trailing side only reveals its buttons, so anything past
            // them is pure elastic feedback.
            return raw < -end ? -end - (-end - raw) * OVERSHOOT_RESISTANCE : raw;
        };

        const commitDistance = () =>
            container.offsetWidth * optionsRef.current.commitRatio;

        const onTouchStart = (event: TouchEvent) => {
            if (committing || event.touches.length > 1) return;
            axis = null;
            startX = event.touches[0].clientX;
            startY = event.touches[0].clientY;
            lastX = startX;
            lastTime = event.timeStamp;
            velocity = 0;
            baseOffset = offsetRef.current;
        };

        const onTouchMove = (event: TouchEvent) => {
            if (committing || event.touches.length > 1) return;
            const { clientX, clientY } = event.touches[0];

            if (axis === null) {
                const dx = Math.abs(clientX - startX);
                const dy = Math.abs(clientY - startY);
                if (Math.max(dx, dy) < AXIS_LOCK_THRESHOLD) return;
                axis = dx > dy ? "x" : "y";
                if (axis === "y") {
                    // The finger is scrolling: give the row back and let any
                    // open panel close, as tapping elsewhere would.
                    if (offsetRef.current !== 0) close();
                    return;
                }
                // Sibling rows must not stay open behind this one.
                openRows.forEach((closeRow) => {
                    if (closeRow !== close) closeRow();
                });
                // Mounts the action panels, and only then. Keeping them in the
                // DOM of every row at rest costs hundreds of nodes the list
                // never uses.
                setIsSwiping(true);
                // The row starts moving on the *next* move, from where the
                // finger is now: sliding it before the panels have mounted
                // would uncover an empty gap for a frame, and rebasing here
                // avoids the row jumping by the lock threshold.
                startX = clientX;
                baseOffset = offsetRef.current;
                event.preventDefault();
                return;
            }
            if (axis !== "x") return;

            // Claim the gesture: without this the browser keeps scrolling the
            // list vertically on the diagonal part of the swipe.
            event.preventDefault();
            velocity = updateVelocity(velocity, clientX - lastX, event.timeStamp - lastTime);
            lastX = clientX;
            lastTime = event.timeStamp;

            const offset = resolveOffset(baseOffset + clientX - startX);
            setOffset(offset, false);
            setWillCommit(offset > commitDistance());
        };

        const onTouchEnd = () => {
            if (axis !== "x") return;
            axis = null;
            setIsSwiping(false);
            const offset = offsetRef.current;
            const { startWidth: start, endWidth: end, onCommitStart: onCommit } =
                optionsRef.current;

            if (offset > 0) {
                if (offset > commitDistance() || velocity > FLICK_VELOCITY) {
                    // Guard against a second gesture landing between the
                    // snap-back and the mutation settling.
                    committing = true;
                    close();
                    onCommit?.();
                    setTimeout(() => {
                        committing = false;
                    }, 0);
                    return;
                }
                if (offset > start / 2) {
                    setOffset(start, true);
                    setOpenSide("start");
                    return;
                }
            } else if (offset < 0 && (-offset > end / 2 || velocity < -FLICK_VELOCITY)) {
                setOffset(-end, true);
                setOpenSide("end");
                return;
            }
            close();
        };

        container.addEventListener("touchstart", onTouchStart, { passive: true });
        container.addEventListener("touchmove", onTouchMove, { passive: false });
        container.addEventListener("touchend", onTouchEnd);
        container.addEventListener("touchcancel", onTouchEnd);

        return () => {
            container.removeEventListener("touchstart", onTouchStart);
            container.removeEventListener("touchmove", onTouchMove);
            container.removeEventListener("touchend", onTouchEnd);
            container.removeEventListener("touchcancel", onTouchEnd);
        };
    }, [container, enabled, close, setOffset, setWillCommit]);

    return {
        containerRef: setContainerNode,
        contentRef: setContentNode,
        openSide,
        isSwiping,
        close,
    };
};

export default useSwipeActions;
