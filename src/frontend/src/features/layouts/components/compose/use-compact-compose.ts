import { useResponsive } from "@gouvfr-lasuite/ui-components";
import { isNativePlatform } from "@/features/native/platform";

/**
 * Whether compose windows use the mobile presentation: the expanded window
 * as a full-screen sheet, minimized ones stacked in the bottom bar.
 *
 * On mobile widths, and always in the native app: a phone turned sideways is
 * wider than the mobile breakpoint, but a desktop window docked in a corner
 * of such a short screen leaves no room to write. The orientation can't be
 * read from the viewport there, as the native shells shrink the web view to
 * make room for the on-screen keyboard.
 */
export const useCompactCompose = (): boolean => {
    const { isMobile } = useResponsive();
    return isMobile || isNativePlatform();
};
