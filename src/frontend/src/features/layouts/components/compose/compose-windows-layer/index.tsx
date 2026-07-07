import { Portal } from "@/features/ui/components/portal";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { ComposeWindow } from "../compose-window";

/**
 * Global floating layer hosting every compose window, docked at the bottom
 * right of the viewport. Minimized windows collapse into pills inside the
 * same flex row, which therefore doubles as the dock.
 */
export const ComposeWindowsLayer = () => {
    const { windows } = useComposeWindows();

    if (windows.length === 0) return null;

    // Portal into #root rather than document.body: #root is an isolated
    // stacking context (`isolation: isolate`), so a body-level layer would
    // paint above everything inside it — including Cunningham modals
    // (.ReactModalPortal, z-index 999999). Inside #root, our z-index keeps
    // the windows above the app chrome but below the modals.
    const container = document.getElementById("root") ?? undefined;

    return (
        <Portal container={container}>
            <div className="compose-windows-layer">
                {windows.map((descriptor) => (
                    <ComposeWindow key={descriptor.windowId} descriptor={descriptor} />
                ))}
            </div>
        </Portal>
    );
};
