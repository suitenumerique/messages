import { useEffect, useRef } from "react";
import { useCombobox } from "downshift";
import clsx from "clsx";

type SuggestionInputProps<T> = {
    /** Render as input or textarea */
    as?: "input" | "textarea";
    /** Current value of the input */
    value: string;
    /** onChange handler for the input */
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
    /** Additional keydown handler — called when the popover doesn't consume the event */
    onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
    placeholder?: string;
    rows?: number;
    inputRef?: React.RefObject<HTMLInputElement | HTMLTextAreaElement | null>;
    inputClassName?: string;

    /** Items to display in the suggestion popover */
    items: T[];
    /** Whether the popover is open (controlled) */
    isOpen: boolean;
    /** Called when the popover should close (click outside, Escape, item selection) */
    onOpenChange?: (isOpen: boolean) => void;
    /** Filter text passed to downshift's inputValue */
    inputValue: string;
    /** Called when an item is selected from the popover */
    onSelect: (item: T) => void;
    /** Convert item to string (used by downshift internally) */
    itemToString: (item: T | null) => string;
    /** Render each popover item */
    renderItem: (item: T, highlighted: boolean) => React.ReactNode;
    /** Extract a unique key from an item */
    keyExtractor: (item: T) => string;

    /** Container className (merged with "suggestion-input") */
    className?: string;
};

/**
 * Generic input (text or textarea) with a suggestion popover.
 * Handles keyboard navigation, ARIA attributes, and click-outside via downshift.
 * The parent controls open/close state and provides items.
 */
export function SuggestionInput<T>({
    as: inputAs = "textarea",
    value,
    onChange,
    onKeyDown,
    placeholder,
    rows,
    inputRef: externalInputRef,
    inputClassName,
    items,
    isOpen,
    onOpenChange,
    inputValue,
    onSelect,
    itemToString,
    renderItem,
    keyExtractor,
    className,
}: SuggestionInputProps<T>) {
    const internalInputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
    const inputRef = externalInputRef ?? internalInputRef;
    const popoverRef = useRef<HTMLUListElement>(null);

    // Stable ref to avoid re-creating click-outside listener on every render
    const onOpenChangeRef = useRef(onOpenChange);
    onOpenChangeRef.current = onOpenChange;

    const {
        getInputProps,
        getMenuProps,
        getItemProps,
        highlightedIndex,
        selectItem,
    } = useCombobox<T>({
        items,
        inputValue,
        isOpen,
        itemToString,
      onSelectedItemChange: ({ selectedItem }) => {
            if (selectedItem != null) {
                onSelect(selectedItem);
                // Reset so the same item can be re-selected after deletion
                selectItem(null as T);
            }
        },
        onIsOpenChange: ({ isOpen: newIsOpen }) => {
            if (!newIsOpen) {
                onOpenChangeRef.current?.(false);
            }
        },
        defaultHighlightedIndex: 0,
        stateReducer: (state, actionAndChanges) => {
            const { type, changes } = actionAndChanges;
            switch (type) {
                // Keep our controlled isOpen — we open/close based on parent's detection logic
                case useCombobox.stateChangeTypes.InputChange:
                    return { ...changes, isOpen: state.isOpen };
                default:
                    return changes;
            }
        },
    });

    // We only use downshift's input props for ARIA attributes and keyboard delegation.
    // We don't spread them directly because downshift's onKeyDown unconditionally
    // prevents default on ArrowDown/ArrowUp, breaking normal cursor navigation
    // when the popover is closed.
    const downshiftInputProps = getInputProps({ suppressRefError: true });

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
        if (isOpen) {
            const isNavKey = e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Escape";
            const isSelectKey = e.key === "Enter" && !e.shiftKey && highlightedIndex >= 0;

            if (isNavKey || isSelectKey) {
                (downshiftInputProps.onKeyDown as React.KeyboardEventHandler<HTMLElement>)(e);
                return;
            }
        }

        onKeyDown?.(e);
    };

    // Close popover on click outside input and popover
    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            const target = e.target as Node;
            if (
                popoverRef.current && !popoverRef.current.contains(target) &&
                inputRef.current && !inputRef.current.contains(target)
            ) {
                onOpenChangeRef.current?.(false);
            }
        };
        if (isOpen) {
            document.addEventListener("mousedown", handleClickOutside);
        }
        return () => {
            document.removeEventListener("mousedown", handleClickOutside);
        };
    }, [isOpen, inputRef]);

    const menuProps = getMenuProps({ ref: popoverRef });

    const sharedInputProps = {
        className: clsx("suggestion-input__input", inputClassName),
        value,
        onChange,
        onKeyDown: handleKeyDown,
        placeholder,
        role: "combobox" as const,
        "aria-autocomplete": "list" as const,
        "aria-controls": downshiftInputProps["aria-controls"],
        "aria-expanded": isOpen,
        "aria-activedescendant": downshiftInputProps["aria-activedescendant"],
    };

    return (
        <div className={clsx("suggestion-input", className)} data-value={value}>
            {inputAs === "input" ? (
                <input
                    ref={inputRef as React.RefObject<HTMLInputElement>}
                    {...sharedInputProps}
                />
            ) : (
                <textarea
                    ref={inputRef as React.RefObject<HTMLTextAreaElement>}
                    {...sharedInputProps}
                    rows={rows}
                />
            )}
            <ul
                {...menuProps}
                className={clsx("suggestion-input__popover", {
                    "suggestion-input__popover--open": isOpen,
                })}
            >
                {isOpen && items.map((item, index) => (
                    <li
                        key={keyExtractor(item)}
                        {...getItemProps({ item, index })}
                        className={clsx("suggestion-input__item", {
                            "suggestion-input__item--highlighted": highlightedIndex === index,
                        })}
                    >
                        {renderItem(item, highlightedIndex === index)}
                    </li>
                ))}
            </ul>
        </div>
    );
}
