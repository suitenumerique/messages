import { useCallback, useEffect, useRef, useState } from "react";

import { AXIS_LOCK_THRESHOLD } from "@/hooks/use-swipe-actions";

type UsePullToRefreshOptions = {
    /** Triggered when the pull crosses the threshold; its promise drives the spinner. */
    onRefresh: () => Promise<unknown>;
    /** When false the gesture is never attached (e.g. non-native platforms). */
    enabled?: boolean;
    /** Distance in px the user must pull past to trigger a refresh. */
    threshold?: number;
};

type UsePullToRefreshResult = {
    /**
     * Callback ref to set on the scrollable container. Using a callback ref
     * (rather than reading a ref object in the effect) guarantees the listeners
     * are attached as soon as the element actually mounts — the container is
     * rendered behind a loading state, so an effect keyed on stable deps would
     * otherwise never see it.
     */
    containerRef: (node: HTMLElement | null) => void;
    /** Callback ref for the indicator; the gesture drives it directly. */
    indicatorRef: (node: HTMLElement | null) => void;
};

/** Damping applied to the raw finger travel so the pull feels elastic. */
const RESISTANCE = 0.5;
/** Hard cap on the visual pull distance. */
const MAX_PULL = 120;

/**
 * Attach a top pull-to-refresh gesture to a scrollable element.
 *
 * The gesture only arms when the container is scrolled to the very top, so it
 * never competes with normal scrolling or bottom infinite-scroll. It relies on
 * raw touch events with a non-passive `touchmove` listener so the browser's
 * native overscroll can be prevented while pulling.
 *
 * **The pull never goes through React state.** It used to, and every frame of
 * the gesture re-rendered the thread panel — and with it every row of the list,
 * none of which are memoised. Even a plain tap paid for one, since arming the
 * gesture was a state change too. The indicator is now driven straight through
 * its DOM node: its height, a `--pull-progress` custom property for the
 * spinner, and two state classes.
 */
export const usePullToRefresh = ({
    onRefresh,
    enabled = true,
    threshold = 70,
}: UsePullToRefreshOptions): UsePullToRefreshResult => {
    const [container, setContainer] = useState<HTMLElement | null>(null);
    const indicator = useRef<HTMLElement | null>(null);

    // Refs mirror the values read inside the long-lived event handlers, so the
    // listeners stay stable and don't capture stale state.
    const startXRef = useRef(0);
    const startYRef = useRef(0);
    const armedRef = useRef(false);
    const distanceRef = useRef(0);
    const refreshingRef = useRef(false);
    const onRefreshRef = useRef(onRefresh);
    onRefreshRef.current = onRefresh;

    const setDistance = useCallback(
        (value: number) => {
            distanceRef.current = value;
            const node = indicator.current;
            if (!node) return;
            node.style.height = `${value}px`;
            node.style.setProperty("--pull-progress", String(Math.min(value / threshold, 1)));
        },
        [threshold]
    );

    /** Toggles a state class without touching React. */
    const setFlag = useCallback((flag: "active" | "refreshing", on: boolean) => {
        indicator.current?.classList.toggle(`pull-to-refresh--${flag}`, on);
    }, []);

    const setIndicatorNode = useCallback((node: HTMLElement | null) => {
        indicator.current = node;
    }, []);

    useEffect(() => {
        if (!enabled || !container) {
            return;
        }
        const el = container;

        const onTouchStart = (event: TouchEvent) => {
            if (refreshingRef.current || el.scrollTop > 0) {
                armedRef.current = false;
                return;
            }
            startXRef.current = event.touches[0].clientX;
            startYRef.current = event.touches[0].clientY;
            armedRef.current = true;
        };

        const onTouchMove = (event: TouchEvent) => {
            if (!armedRef.current || refreshingRef.current) {
                return;
            }
            const delta = event.touches[0].clientY - startYRef.current;
            const deltaX = event.touches[0].clientX - startXRef.current;
            // A row swipe starts at the top of the list just as often as a
            // pull does: leave the gesture to whichever axis the finger
            // committed to, otherwise both react to the same diagonal move.
            // Below the lock threshold the two deltas are just jitter and
            // decide nothing.
            if (
                Math.max(Math.abs(deltaX), Math.abs(delta)) >= AXIS_LOCK_THRESHOLD &&
                Math.abs(deltaX) > Math.abs(delta)
            ) {
                armedRef.current = false;
                setFlag("active", false);
                if (distanceRef.current !== 0) setDistance(0);
                return;
            }
            if (delta <= 0) {
                if (distanceRef.current !== 0) setDistance(0);
                return;
            }
            // Claim the gesture: stop the native rubber-band so the indicator
            // follows the finger smoothly.
            event.preventDefault();
            // Deferred to here rather than to `touchstart`: the pull is only
            // real once the finger has actually moved down, and flagging every
            // touch would freeze the release animation on plain taps.
            setFlag("active", true);
            setDistance(Math.min(delta * RESISTANCE, MAX_PULL));
        };

        const onTouchEnd = () => {
            setFlag("active", false);
            if (!armedRef.current) {
                return;
            }
            armedRef.current = false;
            if (distanceRef.current < threshold) {
                setDistance(0);
                return;
            }
            refreshingRef.current = true;
            setFlag("refreshing", true);
            setDistance(threshold);
            void Promise.resolve(onRefreshRef.current()).finally(() => {
                refreshingRef.current = false;
                setFlag("refreshing", false);
                setDistance(0);
            });
        };

        el.addEventListener("touchstart", onTouchStart, { passive: true });
        el.addEventListener("touchmove", onTouchMove, { passive: false });
        el.addEventListener("touchend", onTouchEnd);
        el.addEventListener("touchcancel", onTouchEnd);

        return () => {
            el.removeEventListener("touchstart", onTouchStart);
            el.removeEventListener("touchmove", onTouchMove);
            el.removeEventListener("touchend", onTouchEnd);
            el.removeEventListener("touchcancel", onTouchEnd);
        };
    }, [container, enabled, threshold, setDistance, setFlag]);

    return { containerRef: setContainer, indicatorRef: setIndicatorNode };
};
