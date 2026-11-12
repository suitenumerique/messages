import { useCallback, useSyncExternalStore } from "react";

// Kept outside React state: the layout holding the banners is remounted when
// navigating between the mailbox, the admin and the home page, and a dismissed
// banner must not come back until the next page load.
const dismissedBanners = new Set<string>();
const listeners = new Set<() => void>();

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};

/**
 * Track whether the banner identified by `bannerId` has been dismissed during
 * the current page session.
 */
export const useBannerDismissal = (bannerId: string | undefined) => {
  const isDismissed = useSyncExternalStore(
    subscribe,
    () => bannerId !== undefined && dismissedBanners.has(bannerId),
  );

  const dismiss = useCallback(() => {
    if (!bannerId) return;
    dismissedBanners.add(bannerId);
    listeners.forEach((listener) => listener());
  }, [bannerId]);

  return { isDismissed, dismiss };
};
