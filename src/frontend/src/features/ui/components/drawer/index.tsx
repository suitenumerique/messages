import { Button, IconSize } from "@gouvfr-lasuite/ui-components";
import clsx from "clsx";
import { useCallback, useId, useRef, useState } from "react";
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { useTranslation } from "react-i18next";

import { Icon } from "@/features/ui/components/icon";
import { useDragGesture } from "@/hooks/use-drag-gesture";

type DrawerSheetProps = {
    title: string;
    /** Id of the title element, so a host dialog can label itself with it. */
    titleId: string;
    /**
     * Landmark role of the sheet root, labelled by the title. Left out when a
     * host dialog already carries the semantics.
     */
    role?: "group";
    /**
     * Called once the drawer has finished animating out — whatever triggered
     * the closing (close button, swipe down, host dismissal). The parent
     * unmounts it then.
     */
    onClose: () => void;
    /**
     * Set by a host to start the exit animation from outside the sheet
     * (Escape, scrim tap…): the sheet then closes exactly as it does from its
     * own controls.
     */
    isClosing?: boolean;
    children: React.ReactNode;
    className?: string;
};

/**
 * The sheet chrome shared by both drawers: grab handle, title row with a
 * close button, swipe-down-to-close (distance or flick velocity) and the
 * enter/exit animations. Carries no landmark role — the exported wrappers
 * decide what the sheet is to assistive technologies.
 *
 * The drag gesture is bound to the header only, so the body content stays
 * free to scroll vertically.
 */
const DrawerSheet = ({
    title,
    titleId,
    role,
    onClose,
    isClosing: isClosingFromHost = false,
    children,
    className,
}: DrawerSheetProps) => {
    const { t } = useTranslation();
    const sheetRef = useRef<HTMLDivElement>(null);
    const [isClosingFromSheet, setIsClosingFromSheet] = useState(false);
    const isClosing = isClosingFromSheet || isClosingFromHost;

    const requestClose = useCallback(() => setIsClosingFromSheet(true), []);

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
        <div
            className={clsx("drawer", className)}
            role={role}
            aria-labelledby={role ? titleId : undefined}
        >
            <div
                ref={sheetRef}
                className={clsx("drawer__sheet", {
                    "drawer__sheet--closing": isClosing,
                })}
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
                        <h2 id={titleId} className="drawer__title">
                            {title}
                        </h2>
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

type DrawerProps = Omit<DrawerSheetProps, "titleId" | "role" | "isClosing">;

/**
 * Non-modal bottom drawer. Renders in place — the parent decides the
 * positioning (fixed bar, portal…); the drawer only provides the sheet
 * chrome, the gesture and the animations.
 *
 * Deliberately NOT a dialog: its consumers (the composer's mobile toolbar
 * panels) replace the on-screen keyboard and rely on the focus staying in the
 * editor — the selection a command applies to lives there, and the toolbar
 * folds as soon as the focus moves elsewhere. Moving or trapping the focus
 * here would break both. A drawer that must block the page behind it is a
 * `ModalDrawer`.
 */
export const Drawer = (props: DrawerProps) => {
    const titleId = useId();
    return <DrawerSheet {...props} titleId={titleId} role="group" />;
};

type ModalDrawerProps = DrawerProps & {
    /**
     * Class of the overlay element (scrim + viewport-bottom host), for
     * consumers that need to style the sheet content.
     */
    overlayClassName?: string;
};

/**
 * Modal bottom drawer: portaled to `<body>` behind a scrim, pinned to the
 * bottom of the viewport. A real dialog for assistive technologies — focus
 * moves in on open, stays trapped inside, Escape and a tap on the scrim
 * close, and the focus returns to the opener once it is gone. Scrolling of
 * the page behind is locked meanwhile.
 *
 * Every way out (Escape, scrim, close button, swipe) plays the sheet's exit
 * animation: the react-aria dismissal only flags the sheet as closing, and
 * `onClose` fires once the slide-out has ended, as for the sheet's own
 * controls. The overlay stays open until then — the parent unmounts it.
 */
export const ModalDrawer = ({ overlayClassName, ...props }: ModalDrawerProps) => {
    const titleId = useId();
    const [isClosing, setIsClosing] = useState(false);

    return (
        <ModalOverlay
            className={clsx("modal-drawer", overlayClassName)}
            isOpen
            isDismissable
            onOpenChange={(isOpen) => {
                if (!isOpen) setIsClosing(true);
            }}
        >
            <Modal className="modal-drawer__modal">
                <Dialog className="modal-drawer__dialog" aria-labelledby={titleId}>
                    <DrawerSheet {...props} titleId={titleId} isClosing={isClosing} />
                </Dialog>
            </Modal>
        </ModalOverlay>
    );
};
