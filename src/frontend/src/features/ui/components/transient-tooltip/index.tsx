import { ReactNode, useEffect, useRef, useState } from "react";
import { Tooltip, TooltipProps } from "@/features/ui/components/tooltip";

const DEFAULT_DURATION_MS = 2000;

export type TransientTooltipPlacement = NonNullable<TooltipProps["placement"]>;

export type TransientTooltipProps = {
    message: string | null;
    onHide: () => void;
    duration?: number;
    placement?: TransientTooltipPlacement;
    children: ReactNode;
};

/**
 * Transient tooltip
 * Shows `message` on its trigger for `duration` ms, then calls `onHide`.
 * Unlike `Tooltip`, it is driven by the caller, not by hover or focus.
 */
export const TransientTooltip = ({
    message,
    onHide,
    duration = DEFAULT_DURATION_MS,
    placement = "bottom",
    children,
}: TransientTooltipProps) => {
    // Keeps the last message displayed while the bubble plays its exit
    // animation, after the parent has already cleared `message`.
    const [displayedMessage, setDisplayedMessage] = useState(message);
    if (message && message !== displayedMessage) setDisplayedMessage(message);
    // Latest-ref so the auto-hide timer below doesn't reset on every parent
    // re-render when `onHide` is passed as an inline arrow function.
    const onHideRef = useRef(onHide);

    useEffect(() => {
        onHideRef.current = onHide;
    });

    useEffect(() => {
        if (!message) return;
        const timer = window.setTimeout(() => onHideRef.current(), duration);
        return () => window.clearTimeout(timer);
    }, [message, duration]);

    return (
        <Tooltip
            isOpen={!!message}
            placement={placement}
            content={<span role="status" aria-live="polite">{displayedMessage}</span>}
        >
            {children}
        </Tooltip>
    );
};
