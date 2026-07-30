import { useCallback, useRef, useState } from "react";

import { triggerHaptic } from "@/features/native/haptics";

export type LongPressPosition = { x: number; y: number };

type UseLongPressOptions = {
  /** Delay before the press is considered "long", in milliseconds. */
  delay?: number;
  /** Haptic feedback duration when the long press fires, or false to disable. */
  vibrate?: number | false;
  /** Movement (px) tolerated before the press is read as a scroll instead. */
  moveTolerance?: number;
};

type UseLongPressResult = {
  handlers: {
    onTouchStart: (event: React.TouchEvent) => void;
    onTouchEnd: () => void;
    onTouchMove: (event: React.TouchEvent) => void;
    onTouchCancel: () => void;
  };
  /** True while the finger is down and the long-press timer is still running. */
  pressing: boolean;
};

/**
 * Detects a touch long-press and reports the initial touch position so callers
 * can anchor a context menu where the finger landed. Touch coordinates are
 * captured on `touchstart` (the synthetic event is not retained) and a release
 * or a move beyond the tolerance before the delay cancels the gesture.
 */
export const useLongPress = (
  onLongPress: (position: LongPressPosition) => void,
  { delay = 500, vibrate = 50, moveTolerance = 10 }: UseLongPressOptions = {},
): UseLongPressResult => {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const origin = useRef<LongPressPosition | null>(null);
  const [pressing, setPressing] = useState(false);

  const cancel = useCallback(() => {
    origin.current = null;
    setPressing(false);
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const start = useCallback(
    (event: React.TouchEvent) => {
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
        setPressing(false);
        if (vibrate) triggerHaptic(vibrate);
        onLongPress(position);
      }, delay);
    },
    [delay, vibrate, onLongPress],
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

  return {
    handlers: {
      onTouchStart: start,
      onTouchEnd: cancel,
      onTouchMove: move,
      onTouchCancel: cancel,
    },
    pressing,
  };
};

export default useLongPress;
