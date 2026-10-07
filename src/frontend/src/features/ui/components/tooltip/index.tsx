import { Children, cloneElement, isValidElement, ReactElement, ReactNode, Ref, useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { mergeProps, mergeRefs, useOverlayPosition, useTooltip, useTooltipTrigger } from "react-aria";
import { useTooltipTriggerState } from "react-stately";
import { Portal } from "@/features/ui/components/portal";

// Must match the `--animation-duration` of Cunningham's `.c__tooltip`.
const ANIMATION_DURATION_MS = 200;
// Portaled to <body>, so it must stay above every overlay, including the
// react-modal portal (see `.ReactModalPortal` in globals.scss). It overrides
// the inline z-index `useOverlayPosition` sets.
const Z_INDEX = 1000000;

export type TooltipProps = {
    placement?: "top" | "bottom" | "left" | "right";
    content: ReactNode;
    closeDelay?: number;
    className?: string;
    /** Controlled mode: the tooltip no longer opens on hover/focus by itself. */
    isOpen?: boolean;
    onOpenChange?: (isOpen: boolean) => void;
    children: ReactNode;
};

type TriggerElement = ReactElement<{ ref?: Ref<HTMLElement> }>;

/**
 * Drop-in replacement for the `Tooltip` of `@gouvfr-lasuite/ui-components`.
 * Upstream renders the bubble inline, next to its trigger, so any ancestor
 * with `overflow: hidden|auto` (resizable panels, scroll areas, modals…)
 * clips it. This one renders it through a portal, keeping Cunningham's
 * `.c__tooltip` markup and styles.
 */
export const Tooltip = ({
    placement = "bottom",
    content,
    closeDelay = 150,
    className,
    isOpen,
    onOpenChange,
    children,
}: TooltipProps) => {
    const triggerRef = useRef<HTMLElement>(null);
    const overlayRef = useRef<HTMLSpanElement>(null);
    const arrowRef = useRef<HTMLSpanElement>(null);
    const state = useTooltipTriggerState({ delay: 0, closeDelay, isOpen, onOpenChange });
    // Derived from `state.isOpen` rather than `onOpenChange`, which does not
    // fire when a controlled `isOpen` changes.
    const [wasOpen, setWasOpen] = useState(state.isOpen);
    const [isEntering, setIsEntering] = useState(false);
    const [isExiting, setIsExiting] = useState(false);
    if (state.isOpen !== wasOpen) {
        setWasOpen(state.isOpen);
        setIsEntering(state.isOpen);
        setIsExiting(!state.isOpen);
    }
    const { triggerProps, tooltipProps: triggerTooltipProps } = useTooltipTrigger({}, state, triggerRef);
    const { tooltipProps } = useTooltip(triggerTooltipProps, state);
    const { overlayProps, arrowProps, placement: resolvedPlacement } = useOverlayPosition({
        targetRef: triggerRef,
        overlayRef,
        arrowRef,
        placement,
        isOpen: state.isOpen,
        // The bubble no longer moves with its trigger once portaled out of the
        // scrolled container, so close it rather than leave it floating.
        onClose: () => state.close(true),
    });
    const trigger = Children.toArray(children)[0];

    useEffect(() => {
        if (!isEntering && !isExiting) return;
        const timer = window.setTimeout(() => {
            setIsEntering(false);
            setIsExiting(false);
        }, ANIMATION_DURATION_MS);
        return () => window.clearTimeout(timer);
    }, [isEntering, isExiting]);

    if (!isValidElement(trigger)) return <>{children}</>;
    const triggerElement = trigger as TriggerElement;
    const isVertical = resolvedPlacement === "top" || resolvedPlacement === "bottom";

    return (
        <>
            {cloneElement(triggerElement, {
                ...mergeProps(triggerElement.props, triggerProps),
                ref: mergeRefs(triggerElement.props.ref, triggerRef),
            })}
            {(state.isOpen || isExiting) && (
                <Portal>
                    <span
                        {...tooltipProps}
                        ref={overlayRef}
                        className={clsx(
                            "c__tooltip",
                            {
                                "c__tooltip--entering": isEntering,
                                "c__tooltip--exiting": isExiting,
                            },
                            className,
                        )}
                        data-placement={resolvedPlacement ?? undefined}
                        style={{ ...overlayProps.style, zIndex: Z_INDEX }}
                    >
                        <span
                            {...arrowProps}
                            ref={arrowRef}
                            className="react-aria-OverlayArrow"
                            data-placement={resolvedPlacement ?? undefined}
                            style={{
                                position: "absolute",
                                transform: isVertical ? "translateX(-50%)" : "translateY(-50%)",
                                ...(resolvedPlacement && { [resolvedPlacement]: "100%" }),
                                ...arrowProps.style,
                            }}
                        >
                            <svg width={16} height={16} viewBox="0 0 16 16">
                                <path d="M0 0 L8 8 L16 0" />
                            </svg>
                        </span>
                        <span className="c__tooltip__content">{content}</span>
                    </span>
                </Portal>
            )}
        </>
    );
};
