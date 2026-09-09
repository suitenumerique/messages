import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import { IconSize, UserAvatar } from "@gouvfr-lasuite/ui-kit";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { ComposeWindowDescriptor } from "@/features/providers/compose-windows/types";
import { Icon } from "@/features/ui/components/icon";
import MailboxHelper from "@/features/utils/mailbox-helper";
import { Edit } from "@gouvfr-lasuite/ui-kit/icons";
import { useComposeSender } from "../use-compose-sender";

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
    // The bar always shows the latest window's title, pile included, so it
    // carries that window's sender avatar too — the same as the desktop tab.
    const getSender = useComposeSender();
    const senderMailbox = latest ? getSender(latest.mailboxId) : undefined;

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
    // The avatar is decorative, so the sender reaches assistive tech through
    // the bar's accessible name only, the title kept as suffix so voice
    // control can still target the visible text.
    const titleWithSender = senderMailbox ? `${senderMailbox.email} — ${title}` : title;

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
                        : titleWithSender
                }
                onClick={open}
            >
                {senderMailbox ? (
                    <span
                        className="compose-mobile-stack__avatar"
                        data-shared={!senderMailbox.is_identity}
                        aria-hidden="true"
                    >
                        <UserAvatar fullName={MailboxHelper.getLabel(senderMailbox)} size="xsmall" />
                    </span>
                ) : (
                    <Icon icon={Edit} size={IconSize.SMALL} />
                )}
                <span className="compose-mobile-stack__title">{title}</span>
            </button>
        </div>
    );
};
