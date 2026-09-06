import { useCallback, useRef, useState } from "react";

type UseDragGestureOptions = {
  /** Which pointer axis the gesture tracks. */
  axis: "x" | "y";
  /**
   * Movement sign that arms the gesture: "positive" (down/right), "negative"
   * (up/left), or undefined to track both ways.
   */
  direction?: "positive" | "negative";
  /**
   * Distance (px) beyond which releasing commits. Evaluated at release time
   * so it can depend on layout (e.g. a third of an element's height).
   */
  commitDistance: number | (() => number);
  /**
   * Flick velocity (px/ms along the axis, low-pass filtered over the last
   * frames) beyond which releasing commits even under the distance.
   */
  commitVelocity?: number;
  /** Called when the release passes either threshold. */
  onCommit: () => void;
  /** Called when the release stays below the thresholds (spring back). */
  onCancel?: () => void;
  /**
   * Drags starting on elements matching this selector are ignored, so their
   * taps keep working (e.g. a close button inside the drag zone).
   */
  excludeSelector?: string;
  disabled?: boolean;
};

type DragDirection = "positive" | "negative" | undefined;

/** Clamps a movement to the allowed sign for the gesture. */
export const clampToDirection = (value: number, direction: DragDirection) => {
  if (direction === "positive") return Math.max(0, value);
  if (direction === "negative") return Math.min(0, value);
  return value;
};

/**
 * Low-pass filtered velocity (px/ms): the flick decision reflects the last
 * few frames rather than one noisy sample.
 */
export const updateVelocity = (
  previous: number,
  delta: number,
  elapsed: number,
) => (elapsed > 0 ? 0.8 * (delta / elapsed) + 0.2 * previous : previous);

/** Release decision: commit past the distance threshold or on a flick. */
export const resolveDragEnd = ({
  offset,
  velocity,
  direction,
  commitDistance,
  commitVelocity,
}: {
  offset: number;
  velocity: number;
  direction: DragDirection;
  commitDistance: number;
  commitVelocity: number;
}): "commit" | "cancel" => {
  if (Math.abs(offset) > commitDistance) return "commit";
  const flicked =
    direction === "positive"
      ? velocity > commitVelocity
      : direction === "negative"
        ? velocity < -commitVelocity
        : Math.abs(velocity) > commitVelocity;
  return flicked ? "commit" : "cancel";
};

type UseDragGestureResult = {
  handlers: {
    onPointerDown: (event: React.PointerEvent<HTMLElement>) => void;
    onPointerMove: (event: React.PointerEvent<HTMLElement>) => void;
    onPointerUp: (event: React.PointerEvent<HTMLElement>) => void;
    onPointerCancel: (event: React.PointerEvent<HTMLElement>) => void;
  };
  /**
   * Signed movement (px) along the axis since the drag started, clamped by
   * `direction`. Follows the pointer live, and resets to 0 on release —
   * consumers drive their exit animation from their own state, not from the
   * last offset.
   */
  offset: number;
  /** True while a pointer is dragging. */
  isDragging: boolean;
};

/**
 * Single-axis pointer drag with distance + flick thresholds. Shared by
 * swipe-to-close surfaces (Drawer), and meant for the upcoming
 * pull-to-refresh and thread-item swipe gestures. The bound element should
 * set `touch-action: none` (or the axis-appropriate variant) so the browser
 * doesn't claim the moves for scrolling.
 */
export const useDragGesture = ({
  axis,
  direction,
  commitDistance,
  commitVelocity = 0.5,
  onCommit,
  onCancel,
  excludeSelector,
  disabled = false,
}: UseDragGestureOptions): UseDragGestureResult => {
  const [offset, setOffset] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const dragRef = useRef<{
    pointerId: number;
    start: number;
    last: number;
    lastTime: number;
    velocity: number;
  } | null>(null);

  const readPosition = useCallback(
    (event: React.PointerEvent<HTMLElement>) =>
      axis === "y" ? event.clientY : event.clientX,
    [axis],
  );

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      if (disabled || dragRef.current) return;
      if (
        excludeSelector &&
        event.target instanceof Element &&
        event.target.closest(excludeSelector)
      ) {
        return;
      }
      const position = readPosition(event);
      dragRef.current = {
        pointerId: event.pointerId,
        start: position,
        last: position,
        lastTime: event.timeStamp,
        velocity: 0,
      };
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // Pointer already gone (or synthetic): tracking still works through
        // bubbling events.
      }
      setIsDragging(true);
    },
    [disabled, excludeSelector, readPosition],
  );

  const onPointerMove = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      const drag = dragRef.current;
      if (!drag || event.pointerId !== drag.pointerId) return;
      const position = readPosition(event);
      drag.velocity = updateVelocity(
        drag.velocity,
        position - drag.last,
        event.timeStamp - drag.lastTime,
      );
      drag.last = position;
      drag.lastTime = event.timeStamp;
      setOffset(clampToDirection(position - drag.start, direction));
    },
    [direction, readPosition],
  );

  const onPointerEnd = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      const drag = dragRef.current;
      if (!drag || event.pointerId !== drag.pointerId) return;
      dragRef.current = null;
      setIsDragging(false);
      setOffset(0);
      if (event.type === "pointercancel") {
        onCancel?.();
        return;
      }
      const outcome = resolveDragEnd({
        offset: clampToDirection(drag.last - drag.start, direction),
        velocity: drag.velocity,
        direction,
        commitDistance:
          typeof commitDistance === "function" ? commitDistance() : commitDistance,
        commitVelocity,
      });
      if (outcome === "commit") onCommit();
      else onCancel?.();
    },
    [commitDistance, commitVelocity, direction, onCommit, onCancel],
  );

  return {
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp: onPointerEnd,
      onPointerCancel: onPointerEnd,
    },
    offset,
    isDragging,
  };
};

export default useDragGesture;
