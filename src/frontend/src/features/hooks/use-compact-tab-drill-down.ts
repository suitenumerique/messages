import { useCallback, useRef } from "react";

// Below this width Cunningham's tab modal collapses into a sidebar→content
// drill-down (its internal `@media (max-width: 576px)`). Callers read the same
// breakpoint so their open-time tab selection matches the layout actually shown.
export const COMPACT_TAB_MODAL_MEDIA_QUERY = "(max-width: 576px)";

/**
 * Opens Cunningham's compact tab modal directly on its preselected tab.
 *
 * In the compact layout the component keeps the sidebar↔content drill-down in
 * its own state, initialised to the sidebar and only ever advanced by a click on
 * a tab button — so passing `activeTab` preselects the tab but still lands the
 * user on the bare sidebar, unlike the wide layout which shows the content pane
 * straight away. Nothing in its props drives that view.
 *
 * Until it derives the view from `activeTab` upstream, we replay the one gesture
 * it does listen to: a click on the already-active tab button, whose handler
 * switches the view unconditionally (the caller's `onTabChange` sees an
 * unchanged id and no-ops). The returned ref must be attached to an element
 * rendered inside the modal — it fires exactly when the modal's DOM is
 * committed, which no timer can reliably predict. The click itself is deferred
 * to a microtask because react-modal only attaches its portal to the document in
 * its own `componentDidMount`, which runs after this ref, and events dispatched
 * on a detached tree never reach React.
 *
 * @param enabled whether a tab was preselected — when false the modal keeps its
 *   default behaviour of opening on the sidebar.
 * @returns a ref callback to attach to an element inside the modal.
 */
export const useCompactTabDrillDown = (enabled: boolean) => {
  const hasDrilledDownRef = useRef(false);

  return useCallback(
    (node: HTMLElement | null) => {
      if (!node || hasDrilledDownRef.current || !enabled) {
        return;
      }
      if (!window.matchMedia(COMPACT_TAB_MODAL_MEDIA_QUERY).matches) {
        return;
      }
      hasDrilledDownRef.current = true;
      queueMicrotask(() => {
        const layout = node.closest<HTMLElement>(".c__modal__tab-layout");
        const activeTabButton = layout?.querySelector<HTMLButtonElement>(
          '[role="tab"][aria-selected="true"]',
        );
        if (!layout || !activeTabButton || !node.isConnected) {
          return;
        }
        // Suppress the sliding transition for this programmatic jump so the
        // modal opens straight on the requested tab instead of animating in from
        // the sidebar, then hand the animation back for the user's own
        // navigation.
        layout.style.transition = "none";
        activeTabButton.click();
        requestAnimationFrame(() => {
          layout.style.transition = "";
        });
      });
    },
    [enabled],
  );
};
