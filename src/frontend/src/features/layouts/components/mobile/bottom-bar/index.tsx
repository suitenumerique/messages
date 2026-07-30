import { PropsWithChildren } from "react";
import { createPortal } from "react-dom";

type MobileBottomBarProps = PropsWithChildren<{
  /** Extra class on the inner bar, to vary layout per usage. */
  className?: string;
}>;

/**
 * Floating bar pinned to the bottom of the viewport on the native app.
 *
 * Rendered through a portal to `#root` so it stays viewport-fixed regardless of
 * transformed ancestors (resizable panels, sliding left panel) while remaining
 * inside the same stacking context as the rest of the app. `#root` carries
 * `isolation: isolate` (reset.scss), so portaling to `document.body` instead
 * would lift the bar out of that context and paint it above everything — even
 * the open left-panel drawer (z-index 1001), which is nested under `#root` and
 * is meant to cover this bar (z-index 1000). `#root` has no transform, so
 * `position: fixed` still resolves against the viewport.
 */
export const MobileBottomBar = ({ children, className }: MobileBottomBarProps) => {
  return createPortal(
    <div className={`mobile-bottom-bar${className ? ` ${className}` : ""}`}>
      {children}
    </div>,
    document.getElementById("root") ?? document.body,
  );
};

export default MobileBottomBar;
