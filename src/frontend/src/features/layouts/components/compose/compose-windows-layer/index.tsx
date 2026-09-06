import { useState } from "react";
import { useResponsive } from "@gouvfr-lasuite/ui-kit";
import { Portal } from "@/features/ui/components/portal";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { ComposeWindow } from "../compose-window";
import { ComposeDockOverflow } from "../compose-dock-overflow";
import { ComposeMobileStack } from "../compose-mobile-stack";
import { ComposeOverview } from "../compose-overview";

/** How many minimized tabs the dock shows before folding into "+X". */
const getVisibleTabCap = (isDesktop: boolean) => (isDesktop ? 3 : 1);

/**
 * Global floating layer hosting every compose window, docked at the bottom
 * right of the viewport. The windows array is MRU-ordered so the flex row
 * naturally puts the most recent tabs (and the expanded window, always last)
 * on the right; older tabs beyond the responsive cap collapse into a "+X"
 * dropdown while staying mounted, so their unsaved content survives.
 *
 * On mobile the expanded window renders as a full-screen sheet and every
 * minimized window is folded (hidden, still mounted) behind a bottom bar:
 * one window reopens on tap, several open the exploded overview.
 */
export const ComposeWindowsLayer = () => {
    const { windows, activeWindow, requestCloseWindow } = useComposeWindows();
    const { isMobile, isDesktop } = useResponsive();
    const [isOverviewOpen, setIsOverviewOpen] = useState(false);

    if (windows.length === 0) return null;

    const minimizedWindows = windows.filter((window) => window.isMinimized);
    // The cap counts every visible window, expanded included: a tab expands
    // in place and keeps its slot. Oldest windows fold into "+X" — never the
    // expanded one, which must stay visible wherever it sits. Mobile folds
    // every minimized window behind the stack bar instead.
    const overflowCount = isMobile
        ? minimizedWindows.length
        : Math.max(0, windows.length - getVisibleTabCap(isDesktop));
    const overflowIds = new Set<string>();
    for (const window of windows) {
        if (overflowIds.size >= overflowCount) break;
        if (window.isMinimized) overflowIds.add(window.windowId);
    }
    const overflowWindows = windows.filter((window) => overflowIds.has(window.windowId));

    // Portal into #root rather than document.body: #root is an isolated
    // stacking context (`isolation: isolate`), so a body-level layer would
    // paint above everything inside it — including Cunningham modals
    // (.ReactModalPortal, z-index 999999). Inside #root, our z-index keeps
    // the windows above the app chrome but below the modals.
    const container = document.getElementById("root") ?? undefined;

    return (
            <Portal container={container}>
                <div className="compose-windows-layer">
                    {!isMobile && overflowWindows.length > 0 && (
                        <ComposeDockOverflow windows={overflowWindows} />
                    )}
                    {isMobile && !activeWindow && minimizedWindows.length > 0 && (
                        <ComposeMobileStack
                            windows={minimizedWindows}
                            onOpenOverview={() => setIsOverviewOpen(true)}
                        />
                    )}
                    {windows.map((descriptor) => (
                        <ComposeWindow
                            key={descriptor.windowId}
                            descriptor={descriptor}
                            isOverflowed={overflowIds.has(descriptor.windowId)}
                        />
                    ))}
                    {isMobile && isOverviewOpen && !activeWindow && (
                        <ComposeOverview
                            windows={windows}
                            onClose={() => setIsOverviewOpen(false)}
                            onRequestCloseWindow={requestCloseWindow}
                        />
                    )}
                </div>
            </Portal>
    );
};
