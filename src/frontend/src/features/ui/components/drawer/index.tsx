import { Icon, IconSize } from "@gouvfr-lasuite/ui-kit";
import clsx from "clsx";
import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useDragGesture } from "@/hooks/use-drag-gesture";
import { Button } from "@gouvfr-lasuite/cunningham-react";

type DrawerProps = {
    title: string;
    /**
     * Called once the drawer has finished animating out — whatever triggered
     * the closing (close button, swipe down). The parent unmounts it then.
     */
    onClose: () => void;
    children: React.ReactNode;
    className?: string;
};

/**
 * Generic bottom drawer: grab handle, title row with a close button, and
 * swipe-down-to-close (distance or flick velocity). Renders in place — the
 * parent decides the positioning (fixed bar, portal…); the drawer only
 * provides the sheet chrome, the gesture and the enter/exit animations.
 *
 * The drag gesture is bound to the header only, so the body content stays
 * free to scroll vertically.
 */
export const Drawer = ({ title, onClose, children, className }: DrawerProps) => {
    const { t } = useTranslation();
    const sheetRef = useRef<HTMLDivElement>(null);
    const [isClosing, setIsClosing] = useState(false);

    const requestClose = useCallback(() => setIsClosing(true), []);

    const {
        handlers: dragHandlers,
        offset: dragOffset,
        isDragging,
    } = useDragGesture({
        axis: "y",
        direction: "positive",
        commitDistance: () =>
            Math.max(60, (sheetRef.current?.offsetHeight ?? 0) / 3),
        onCommit: requestClose,
        // Taps on the header's controls (close button) are not drags.
        excludeSelector: "button",
        disabled: isClosing,
    });

    return (
        <div className={clsx("drawer", className)}>
            <div
                ref={sheetRef}
                className={clsx("drawer__sheet", {
                    "drawer__sheet--closing": isClosing,
                })}
                role="region"
                aria-label={title}
                style={
                    isClosing
                        ? undefined
                        : {
                              transform: dragOffset
                                  ? `translateY(${dragOffset}px)`
                                  : undefined,
                              transition: isDragging ? "none" : undefined,
                          }
                }
                onTransitionEnd={(event) => {
                    if (
                        isClosing &&
                        event.target === sheetRef.current &&
                        event.propertyName === "transform"
                    ) {
                        onClose();
                    }
                }}
            >
                <div className="drawer__header" {...dragHandlers}>
                    <div className="drawer__grab-handle" aria-hidden="true" />
                    <div className="drawer__title-row">
                        <span className="drawer__title">{title}</span>
                        <Button
                            aria-label={t("Close")}
                            onClick={requestClose}
                            icon={<Icon name="close" size={IconSize.MEDIUM} />}
                            color="neutral"
                            variant="tertiary"
                            size="small"
                        />
                    </div>
                </div>
                <div className="drawer__body">{children}</div>
            </div>
        </div>
    );
};
