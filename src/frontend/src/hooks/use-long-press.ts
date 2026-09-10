import { useCallback, useEffect, useRef, useState } from "react";

import { triggerHaptic } from "@/features/native/haptics";

export type LongPressPosition = { x: number; y: number };

type UseLongPressOptions = {
  /** Delay before the press is considered "long", in milliseconds. */
  delay?: number;
  /** Haptic feedback duration when the long press fires, or false to disable. */
  vibrate?: number | false;
  /** Movement (px) tolerated before the press is read as a scroll instead. */
  moveTolerance?: number;
  /**
   * Short tap handler. Passing it makes the hook decide tap vs long press from
   * the touch sequence itself, rather than leaving the tap to the compatibility
   * click — a click an overlay opened by the long press may swallow.
   */
  onTap?: () => void;
};

type UseLongPressResult = {
  handlers: {
    onTouchStart: (event: React.TouchEvent) => void;
    onTouchEnd: (event: React.TouchEvent) => void;
    onTouchMove: (event: React.TouchEvent) => void;
    onTouchCancel: () => void;
  };
  /** True while the finger is down and the long-press timer is still running. */
  pressing: boolean;
  /**
   * True when a click firing right now is the tail of a touch the hook already
   * resolved. Only useful next to an `onTap`, as a fallback for the browsers
   * that emit the compatibility click despite the `preventDefault`.
   */
  isTouchHandled: () => boolean;
};

/**
 * How long a compatibility click stays attributable to the touch that just
 * ended. Browsers emit it within ~350 ms; anything later is a new gesture.
 */
const COMPATIBILITY_CLICK_WINDOW_MS = 700;

/**
 * Detects a touch long-press and reports the initial touch position so callers
 * can anchor a context menu where the finger landed. Touch coordinates are
 * captured on `touchstart` (the synthetic event is not retained) and a release
 * or a move beyond the tolerance before the delay cancels the gesture.
 */
export const useLongPress = (
  onLongPress: (position: LongPressPosition) => void,
  {
    delay = 500,
    vibrate = 50,
    moveTolerance = 10,
    onTap,
  }: UseLongPressOptions = {},
): UseLongPressResult => {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const origin = useRef<LongPressPosition | null>(null);
  const fired = useRef(false);
  const handledUntil = useRef(0);
  const [pressing, setPressing] = useState(false);

  const cancel = useCallback(() => {
    origin.current = null;
    setPressing(false);
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  // A press whose `touchend` never came back — the long press opened an overlay
  // that captured the rest of the sequence — must not leave its timer armed:
  // it would fire in the middle of the next, short, press.
  useEffect(() => cancel, [cancel]);

  const start = useCallback(
    (event: React.TouchEvent) => {
      cancel();
      fired.current = false;
      const touch = event.touches[0];
      const position: LongPressPosition = touch
        ? { x: touch.clientX, y: touch.clientY }
        : { x: 0, y: 0 };
      origin.current = position;
      setPressing(true);
      timer.current = setTimeout(() => {
        // Cleared so a move past the delay no longer looks like a running
        // press it could cancel.
        timer.current = null;
        fired.current = true;
        setPressing(false);
        if (vibrate) triggerHaptic(vibrate);
        onLongPress(position);
      }, delay);
    },
    [cancel, delay, vibrate, onLongPress],
  );

  // A finger never holds perfectly still: aborting on the first `touchmove`
  // made the long press practically unreachable. Only a move past the
  // tolerance — a scroll — aborts it.
  const move = useCallback(
    (event: React.TouchEvent) => {
      const from = origin.current;
      if (!from || !timer.current) return;
      const touch = event.touches[0];
      if (!touch) return;
      const distance = Math.hypot(touch.clientX - from.x, touch.clientY - from.y);
      if (distance > moveTolerance) cancel();
    },
    [moveTolerance, cancel],
  );

  const end = useCallback(
    (event: React.TouchEvent) => {
      // The timer still running means the finger left before the delay: a tap.
      const isTap = timer.current !== null;
      const wasResolved = isTap || fired.current;
      fired.current = false;
      cancel();
      if (!onTap || !wasResolved) return;
      handledUntil.current = Date.now() + COMPATIBILITY_CLICK_WINDOW_MS;
      // The gesture is settled here, so the compatibility mouse events are
      // dropped: a click firing on top of it would run the tap action twice.
      if (event.cancelable) event.preventDefault();
      if (isTap) onTap();
    },
    [cancel, onTap],
  );

  const isTouchHandled = useCallback(
    () => Date.now() < handledUntil.current,
    [],
  );

  return {
    handlers: {
      onTouchStart: start,
      onTouchEnd: end,
      onTouchMove: move,
      onTouchCancel: cancel,
    },
    pressing,
    isTouchHandled,
  };
};

export default useLongPress;
