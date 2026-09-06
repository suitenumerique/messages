import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import { IconSize } from "@gouvfr-lasuite/ui-kit";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { ComposeWindowDescriptor } from "@/features/providers/compose-windows/types";
import { Icon } from "@/features/ui/components/icon";
import { Edit } from "@gouvfr-lasuite/ui-kit/icons";

type ComposeMobileStackProps = {
    /** Minimized windows, MRU order (most recent last). */
    windows: readonly ComposeWindowDescriptor[];
    /** Asked when several windows are stacked: opens the overview. */
    onOpenOverview: () => void;
};

/**
 * Mobile dock: minimized compose windows collapse into a bar pinned to the
 * bottom of the viewport. A single window reopens on tap; several stack into
 * a pile whose tap opens the exploded overview (like Apple Mail).
 */
export const ComposeMobileStack = ({ windows, onOpenOverview }: ComposeMobileStackProps) => {
    const { t } = useTranslation();
    const { focusWindow } = useComposeWindows();
    const rootRef = useRef<HTMLDivElement>(null);
    const latest = windows[windows.length - 1];
    const isPile = windows.length > 1;

    const open = () => {
        if (!latest) return;
        if (isPile) {
            onOpenOverview();
        } else {
            focusWindow(latest.windowId);
        }
    };

    // The bar sits over the bottom of the viewport: publish its measured
    // height so the layout can reserve the space (bottom bars lift above it,
    // scrollable views pad for it) instead of having their CTAs covered.
    useEffect(() => {
        const element = rootRef.current;
        const root = document.documentElement;
        if (!element) return;
        const apply = () => root.style.setProperty("--compose-stack-height", `${element.offsetHeight}px`);
        apply();
        const observer = new ResizeObserver(apply);
        observer.observe(element);
        root.classList.add("has-compose-stack");
        return () => {
            observer.disconnect();
            root.classList.remove("has-compose-stack");
            root.style.removeProperty("--compose-stack-height");
        };
    }, []);

    if (!latest) return null;

    const title = latest.title?.trim() || t("New message");

    return (
        <div
            ref={rootRef}
            className={clsx("compose-mobile-stack", { "compose-mobile-stack--pile": isPile })}
        >
            {/* Inverted corners rounding the bottom of whatever sits above
                the dark seam (main view or lifted bottom bar). */}
            <div className="compose-mobile-stack__corners" aria-hidden="true" />
            <button
                type="button"
                className="compose-mobile-stack__bar"
                aria-label={
                    isPile
                        ? t("{{count}} compose window", { count: windows.length })
                        : title
                }
                onClick={open}
            >
                <Icon icon={Edit} size={IconSize.SMALL} />
                <span className="compose-mobile-stack__title">{title}</span>
            </button>
        </div>
    );
};
