import { Button, Option, useContextMenuContext } from "@gouvfr-lasuite/ui-components";
import { KeyboardEvent, ReactNode, RefObject, TouchEvent, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import EventHelper from "@/features/utils/event-helper";
import { useLongPress } from "@/hooks/use-long-press";

export type ComboBoxOptionAction = {
    label: string,
    icon: ReactNode,
    onAction: () => void | Promise<void>,
};

/**
 * Where an option action stands. Every phase but `idle` and
 * `closingContextMenu` has the focus out of the input, whose blur is then
 * ignored: it would otherwise pick the highlighted option, the one acted
 * upon, turn what is typed into a chip and close the menu.
 * - `contextMenu`: opened by a long press, react-aria moved the focus into it.
 * - `closingContextMenu`: closed without picking the action. react-aria gives
 *   the focus back to the input a frame later, the suggestions stay displayed
 *   until then rather than blink.
 * - `running`: the action runs, maybe in a modal.
 */
type Phase = "idle" | "contextMenu" | "closingContextMenu" | "running";

type UseOptionActionsParams = {
    getOptionAction?: (option: Option) => ComboBoxOptionAction | undefined,
    /** The options of the menu, in the order downshift numbers them. */
    menuItems: Option[],
    inputRef: RefObject<HTMLInputElement | null>,
    /** Opens the menu again once an action is done. */
    openMenu: () => void,
};

/**
 * Offers the action of a combobox option to every kind of user: a button for
 * a mouse, a shortcut for the keyboard and assistive technologies, a context
 * menu opened by a long press for a finger.
 */
export const useOptionActions = ({
    getOptionAction,
    menuItems,
    inputRef,
    openMenu,
}: UseOptionActionsParams) => {
    const { t } = useTranslation();
    const { open: openContextMenu } = useContextMenuContext();
    // Read by the blur handlers, which run before the next render.
    const phaseRef = useRef<Phase>("idle");
    const [phase, setPhaseState] = useState<Phase>("idle");
    const setPhase = (next: Phase) => {
        phaseRef.current = next;
        setPhaseState(next);
    };
    const pressedOptionRef = useRef<Option | null>(null);
    const descriptionIdPrefix = useId();
    // Apple keyboards mostly lack a forward delete key, so Shift+Delete would
    // take Fn there: their ⌫ key, raising Backspace, is used instead.
    const shortcut = useMemo(
        () => EventHelper.isApplePlatform()
            ? { key: "Backspace", ariaKeyShortcuts: "Shift+Backspace", label: "⇧⌫" }
            : { key: "Delete", ariaKeyShortcuts: "Shift+Delete", label: t("Shift+Delete") },
        [t]
    );

    const runAction = async (action: ComboBoxOptionAction) => {
        // Also hides the suggestions, which would otherwise show above a modal
        // the action opens: react-aria raises its popovers to a z-index of
        // 100000.
        setPhase("running");
        try {
            await action.onAction();
        } finally {
            setPhase("idle");
        }
        // Back to browsing the suggestions, as before the action.
        inputRef.current?.focus();
        openMenu();
    };

    // Armed on every option, an action or not: Android takes the focus away
    // from the input as soon as it recognizes its own long press, slightly
    // before ours fires, and that blur is ignored while a finger is down.
    const { handlers, pressing, isTouching } = useLongPress((position) => {
        const item = pressedOptionRef.current;
        const action = item && getOptionAction?.(item);
        if (!action) return false;
        setPhase("contextMenu");
        openContextMenu({
            position,
            items: [{ label: action.label, icon: action.icon, callback: () => runAction(action) }],
            // Called on every close, the action picked or not.
            onBlur: () => {
                if (phaseRef.current !== "contextMenu") return;
                setPhase("closingContextMenu");
                requestAnimationFrame(() => requestAnimationFrame(() => {
                    if (phaseRef.current === "closingContextMenu") setPhase("idle");
                }));
            },
        });
    });

    const ignoresInputBlur = () =>
        phaseRef.current === "contextMenu" || phaseRef.current === "running" || isTouching();

    // Without the focus in the input, while a finger waits for the long press
    // to fire, then under the context menu.
    const keepsMenuDisplayed = pressing || phase === "contextMenu" || phase === "closingContextMenu";

    const menuTouchHandlers = {
        onTouchStart: (event: TouchEvent) => {
            const pressed = (event.target as Element).closest<HTMLElement>("[data-option-index]");
            const item = pressed ? menuItems[Number(pressed.dataset.optionIndex)] : undefined;
            pressedOptionRef.current = item ?? null;
            if (item) handlers.onTouchStart(event);
        },
        onTouchMove: handlers.onTouchMove,
        onTouchEnd: handlers.onTouchEnd,
        onTouchCancel: handlers.onTouchCancel,
    };

    /** Runs the highlighted option's action on its shortcut. */
    const handleKeyDown = (
        event: KeyboardEvent,
        { isMenuOpen, highlightedIndex }: { isMenuOpen: boolean, highlightedIndex: number },
    ) => {
        if (event.key !== shortcut.key || !event.shiftKey || !isMenuOpen) return;
        const item = menuItems[highlightedIndex];
        const action = item && getOptionAction?.(item);
        if (!action) return;
        event.preventDefault();
        void runAction(action);
    };

    /** What an option gets for its action: attributes, and the button. */
    const getOptionActionProps = (item: Option, index: number) => {
        const action = getOptionAction?.(item);
        if (!action) return { itemProps: {}, button: null };
        const hint = `${action.label} (${shortcut.label})`;
        const descriptionId = `${descriptionIdPrefix}-${index}`;
        return {
            itemProps: {
                "aria-keyshortcuts": shortcut.ariaKeyShortcuts,
                "aria-describedby": descriptionId,
            },
            button: (
                <OptionActionButton
                    action={action}
                    hint={hint}
                    descriptionId={descriptionId}
                    onRun={() => void runAction(action)}
                />
            ),
        };
    };

    return { ignoresInputBlur, keepsMenuDisplayed, menuTouchHandlers, handleKeyDown, getOptionActionProps };
};

type OptionActionButtonProps = {
    action: ComboBoxOptionAction,
    hint: string,
    descriptionId: string,
    onRun: () => void,
};

const OptionActionButton = ({ action, hint, descriptionId, onRun }: OptionActionButtonProps) => (
    <>
        {/* Hidden, so it stays out of the option name. */}
        <span id={descriptionId} hidden>{hint}</span>
        {/* An option cannot hold an interactive element: the button is for a
            mouse, the keyboard and assistive technologies go through the
            shortcut, a finger through a long press. */}
        <Button
            variant="tertiary"
            size="nano"
            type="button"
            tabIndex={-1}
            aria-hidden="true"
            className="c__combobox__menu__item__action"
            title={hint}
            icon={action.icon}
            onClick={(event) => {
                // Would otherwise pick the option.
                event.stopPropagation();
                onRun();
            }}
        />
    </>
);
