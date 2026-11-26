import { Field, LabelledBox, SelectProps } from "@gouvfr-lasuite/ui-components";
import clsx from "clsx";
import { useCombobox, useMultipleSelection } from "downshift"
import { ReactNode, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Chip } from "./chip";
import { Button, Option } from "@gouvfr-lasuite/ui-components";
import { Icon } from "@/features/ui/components/icon";
import { useTranslation } from "react-i18next";
import { Popover } from "react-aria-components";
import { ComboBoxOptionAction, useOptionActions } from "./use-option-actions";
import { isEmulatedChipDrag, useChipTouchDrag } from "./use-chip-touch-drag";
import { CHIP_DRAGGING_CLASS, followMouseChipDrag } from "./chip-drag-preview";

export type { ComboBoxOptionAction } from "./use-option-actions";

// react-aria lowers it to the room left on the side the menu opens to.
const MENU_MAX_HEIGHT = 320;

const chipDragType = (group: string) => `application/x-combobox-chip-${group}`;

/** Where a dragged chip would land: its index, and the caret's box. */
type ChipDropSpot = { index: number, left: number, top: number, height: number };

/**
 * The chip being dragged in this page, if any. The dragged value can only be
 * read on drop: this tells a field beforehand that the chip comes from
 * itself, to be reordered there, and tells the source on `dragend` that
 * another field took the chip. The drop effect cannot tell the latter: an
 * editor accepts any drag, so a chip dropped in the message body would read
 * as moved.
 */
let draggedChip: { sourceId: string, value: string, dropped: boolean } | null = null;

/** Tells whether a drag, React's or the DOM's, carries a chip of `group`. */
export const isComboBoxChipDrag = (event: { dataTransfer: DataTransfer | null }, group: string) =>
    !!event.dataTransfer?.types.includes(chipDragType(group));

export type ComboBoxProps =  {
    onInputChange?: (value: string) => void;
    value?: string[],
    defaultValue?: string[],
    onChange?: (value: string[]) => void,
    valueValidator?: (value: string) => boolean,
    /**
     * Canonicalizes a value on its way in, from any source: typed, pasted,
     * picked from the menu, or restored from a saved draft. Applied before
     * the chip is built so what the user sees is what gets submitted.
     */
    valueTransformer?: (value: string) => string,
    /**
     * Short warning to attach to a selected value (rendered on its chip).
     * Return nothing for values that need no warning.
     */
    getItemWarning?: (value: string) => string | undefined,
    /**
     * Action offered on a menu option: a button for a mouse, a shortcut for
     * the keyboard, a context menu opened by a long press for a finger (see
     * `useOptionActions`). The focus goes back to the input once it resolves,
     * as it may have opened a modal. Return nothing for options without
     * action.
     */
    getOptionAction?: (option: Option) => ComboBoxOptionAction | undefined,
    /**
     * Options offered under their own title once no main option is left to
     * offer, and picked the same way.
     */
    fallbackSection?: { title: string, options: Option[] },
    /**
     * Lets the chips be dragged with a mouse, to reorder them, or into
     * another combobox of the same group, which moves them there, also when
     * it belongs to another form of the page. From another browser window,
     * the chip is copied: that window cannot learn of the drop.
     */
    dragGroup?: string,
    /**
     * "floating" (default) animates the label above the value, like the
     * ui-kit Select. "inline" puts the label in a left column and the field
     * in a right column, on the same grid as the ui-kit inline fields.
     */
    variant?: "floating" | "inline",
    /**
     * Controls rendered at the end of the value row, after the clear
     * button (e.g. buttons toggling related fields).
     */
    actions?: ReactNode,
    autoFocus?: boolean,
} & Omit<SelectProps, 'value' | 'defaultValue' | 'onChange' | 'variant'>;

export const ComboBox = (props: ComboBoxProps) => {
    const { t } = useTranslation();
    const [inputValue, setInputValue] = useState('');
    const [inputFocused, setInputFocused] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);
    const wrapperRef = useRef<HTMLDivElement>(null);
    // `className` is merged into the root classes below instead of being
    // spread, otherwise a caller-provided value would replace `c__combobox`.
    const { valueTransformer, getItemWarning, getOptionAction, fallbackSection, dragGroup, className, variant = "floating", actions, ...fieldProps } = props;
    const fallbackSectionTitleId = useId();
    const comboboxId = useId();
    const chipPointerTypeRef = useRef("");
    const valueRowRef = useRef<HTMLDivElement>(null);
    // Where a chip dragged over the field would land, shown by a caret.
    const [chipDropSpot, setChipDropSpot] = useState<ChipDropSpot | null>(null);
    const inline = variant === "inline";
    const canonical = useCallback(
        (value: string): string => (valueTransformer ? valueTransformer(value) : value),
        [valueTransformer]
    );
    const toOption = useCallback((value: string): Option => {
        const transformed = canonical(value);
        return { label: transformed, value: transformed };
    }, [canonical]);
    /**
     * Keep one option per canonical value, first occurrence wins.
     *
     * Two entries that differ only by what the transformer folds (case, say)
     * become the same value once transformed, so without this a restored
     * value renders duplicate chips and `onChange` reports the same value
     * twice.
     */
    const dedupe = useCallback((options: Option[]): Option[] => {
        const seen = new Set<string>();
        return options.filter((option) => {
            const key = option.value ?? option.label;
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
        });
    }, []);
    const { getSelectedItemProps, getDropdownProps, removeSelectedItem, addSelectedItem, selectedItems, setSelectedItems } = useMultipleSelection<Option>({
        initialSelectedItems: dedupe((props.value || props.defaultValue || []).map(toOption)),
        stateReducer: (state, { type, changes}) => {
            if(type === useMultipleSelection.stateChangeTypes.DropdownKeyDownBackspace) {
                // Give focus to the last selected item instead of deleting it when pressing backspace
                return {
                    ...state,
                    activeIndex: state.selectedItems.length > 0 ? state.selectedItems.length - 1 : -1,
                }
            }
            if(type === useMultipleSelection.stateChangeTypes.SelectedItemKeyDownBackspace) {
                // Update the activeIndex to the previous item when deleting a chip that is not the last one
                // otherwise the focus is lost and the user is no more able to navigate through the chip list
                return { ...state, ...changes, activeIndex: changes.activeIndex ? state.activeIndex - 1 : -1 }
            }
            if (type === useMultipleSelection.stateChangeTypes.SelectedItemKeyDownNavigationPrevious) {
                // Loop to the last selected item when user wants to navigate
                // to the previous item and the first one is already focused
                return {
                    ...changes,
                    activeIndex: state.activeIndex === 0 ? state.selectedItems.length - 1 : changes.activeIndex,
                }
            }
            return { ...state, ...changes }
        },
    })
    const withoutSelected = useCallback((options: Option[]): Option[] => {
        // Compare on the transformed value: an option whose value differs
        // only by what the transformer canonicalizes (e.g. case) would
        // otherwise stay offered after being picked, and could be added a
        // second time.
        const selected = new Set(selectedItems.map(item => item.value || item.label));
        return options.filter(option => {
            const raw = option.value ?? option.label;
            // An option carrying no string value cannot match a selected chip.
            return typeof raw !== 'string' || !selected.has(canonical(raw));
        });
    }, [selectedItems, canonical]);
    // Limit the options list to 10 items to avoid performance issues
    const filteredOptions = useMemo(
        () => withoutSelected(props.options).slice(0, 10),
        [props.options, withoutSelected]
    );
    // Judged after the selected chips are left out: main options that are all
    // picked already leave nothing to offer.
    const fallbackOptions = useMemo(
        () => filteredOptions.length > 0 ? [] : withoutSelected(fallbackSection?.options ?? []),
        [filteredOptions.length, fallbackSection?.options, withoutSelected]
    );
    // Downshift numbers the options of both sections in a single sequence.
    const menuItems = useMemo(
        () => [...filteredOptions, ...fallbackOptions],
        [filteredOptions, fallbackOptions]
    );
    const showLabelAsPlaceholder = useMemo(
        () => selectedItems.length === 0 && !inputValue && !inputFocused,
        [inputFocused, inputValue, selectedItems]
    )
    const extractNewItemsIfNeeded = () => {
        const [newItems, rest] = parseInputValue(inputValue);
        if (newItems.length > 0) {
            setSelectedItems(dedupe([...selectedItems, ...newItems]));
            setInputValue(rest);
            return true;
        }
        return false;
    }

    // Before useCombobox, whose state reducer asks it whether to ignore a
    // blur, also while React replays that reducer during render. `openMenu`
    // only runs once an action is done, long after this render.
    const optionActions = useOptionActions({
        getOptionAction,
        menuItems,
        inputRef,
        openMenu: () => openMenu(),
    });
    const chipTouchDrag = useChipTouchDrag({ enabled: !!dragGroup && !props.disabled });
    // Android takes the focus away from the input on its own long press, on
    // a chip as on an option.
    const ignoresInputBlur = () => optionActions.ignoresInputBlur() || chipTouchDrag.isTouchingChip();

    const {
        getInputProps,
        getItemProps,
        getMenuProps,
        getLabelProps,
        isOpen,
        highlightedIndex,
        selectedItem,
        closeMenu,
        openMenu,
    } = useCombobox(
        {
            items: menuItems,
            itemToString: (item) => item?.label || item?.value || '',
            stateReducer: (state, { type, changes }) => {
                if (type === useCombobox.stateChangeTypes.InputBlur && ignoresInputBlur()) {
                    return state;
                }
                return changes;
            },
            onSelectedItemChange: ({ selectedItem }) => {
                if (!selectedItem) return;
                // An option may carry a value the transformer would change
                // (e.g. its case): canonicalize it like typed input.
                addSelectedItem(toOption(selectedItem.value || selectedItem.label));
                setInputValue('');
            },
            defaultHighlightedIndex: 0,
            onInputValueChange: ({ inputValue: newInputValue }) => {
                const isPasted = newInputValue.length - inputValue?.length > 1
                const [newItems, rest] = parseInputValue(newInputValue);
                if (isPasted && newItems.length > 0) {
                    setSelectedItems(dedupe([...selectedItems, ...newItems]));
                    setInputValue(rest);
                    props.onInputChange?.(rest);
                    return;
                }
                setInputValue(newInputValue)
                props.onInputChange?.(newInputValue);
            }
        }
    );
    const inputProps = getInputProps(
        {
            ...getDropdownProps({
                ref: inputRef,
                size: 4,
                value: inputValue,
                onBlurCapture: () => {
                    if (!ignoresInputBlur()) extractNewItemsIfNeeded();
                },
                onChange: (e) => {
                    // Synchronously update the input value
                    // This is important to avoid cursor jumping to the end of the input
                    // https://dev.to/kwirke/solving-caret-jumping-in-react-inputs-36ic
                    setInputValue((e.target as HTMLInputElement).value);
                },
                onKeyDown: (e) => {
                    // The focus stays in the input, out of reach of the
                    // action buttons: the highlighted option's action gets a
                    // shortcut, announced on the option.
                    optionActions.handleKeyDown(e, { isMenuOpen: isOpen, highlightedIndex });
                    if (e.key === 'Tab') {
                        if (inputValue.length > 0) {
                            const created = extractNewItemsIfNeeded();
                            if (created) {
                                e.preventDefault();
                                e.stopPropagation();
                            }
                        }
                    }
                },
            }),
        }
    );

    const parseInputValue = useCallback((value: string): [Option[], string] => {
        const values = value.split(/[,; ]/).filter(item => item.trim().length > 0);
        let validValues: string[] = values;
        let invalidValues: string[] = [];

        if (props.valueValidator) {
            validValues = values.filter(item => props.valueValidator!(item));
            invalidValues = values.filter(item => !validValues.includes(item));
        }

        // Rejected values go back into the input verbatim so the user can fix
        // what they actually typed; only accepted ones are canonicalized.
        return [validValues.map(toOption),
            validValues.length === 0 ? value : invalidValues.join(', ')];
    }, [props.valueValidator, toOption]);

    useEffect(() => {
        props.onChange?.(selectedItems.map(item => item.value || item.label));
    }, [selectedItems]);

    // Native listeners, as React drops the focus events fired during a commit:
    // react-modal gives the focus back to the input while unmounting, so a
    // React onFocus would never tell the menu it may open again.
    useEffect(() => {
        const input = inputRef.current;
        if (!input) return;
        const handleFocus = () => setInputFocused(true);
        const handleBlur = () => setInputFocused(false);
        input.addEventListener("focus", handleFocus);
        input.addEventListener("blur", handleBlur);
        return () => {
            input.removeEventListener("focus", handleFocus);
            input.removeEventListener("blur", handleBlur);
        };
    }, []);

    useEffect(() => {
        if (props.autoFocus) inputRef.current?.focus();
    }, [props.autoFocus]);

    const acceptsChipDrop = (event: React.DragEvent) =>
        !!dragGroup
        && !props.disabled
        && isComboBoxChipDrag(event, dragGroup);
    /** The index of the dragged chip, when it comes from this field. */
    const ownDraggedChipIndex = () => (
        draggedChip?.sourceId === comboboxId
            ? selectedItems.findIndex((item) => (item.value || item.label) === draggedChip?.value)
            : -1
    );
    /**
     * Where a chip dropped at the pointer would land: before the first chip
     * the pointer is before, on its line or above it, else after the last
     * one. The caret sits halfway in the gap, positioned rather than laid
     * out: it would otherwise push the chips around, and the spot with them.
     */
    const locateChipDrop = (event: React.DragEvent): ChipDropSpot | null => {
        const row = valueRowRef.current;
        if (!row) return null;
        const rowRect = row.getBoundingClientRect();
        const gap = parseFloat(getComputedStyle(row).columnGap) || 0;
        const chips = Array.from(row.querySelectorAll(".c__combobox__chip"));
        for (let index = 0; index < chips.length; index++) {
            const rect = chips[index].getBoundingClientRect();
            const isAboveLine = event.clientY < rect.top;
            const isBeforeMiddle = event.clientY <= rect.bottom && event.clientX < rect.left + rect.width / 2;
            if (isAboveLine || isBeforeMiddle) {
                return { index, left: rect.left - rowRect.left - gap / 2, top: rect.top - rowRect.top, height: rect.height };
            }
        }
        const last = chips.at(-1)?.getBoundingClientRect();
        if (last) {
            return { index: chips.length, left: last.right - rowRect.left + gap / 2, top: last.top - rowRect.top, height: last.height };
        }
        const input = inputRef.current?.getBoundingClientRect();
        return { index: 0, left: (input?.left ?? rowRect.left) - rowRect.left, top: (input?.top ?? rowRect.top) - rowRect.top, height: input?.height ?? rowRect.height };
    };
    const chipDropHandlers = dragGroup ? {
        onDragOver: (event: React.DragEvent) => {
            if (!acceptsChipDrop(event)) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
            const located = locateChipDrop(event);
            // Right before or after the dragged chip itself, nothing moves.
            const from = ownDraggedChipIndex();
            const spot = located && from >= 0 && (located.index === from || located.index === from + 1)
                ? null
                : located;
            // Fired continuously while hovering: only a new spot re-renders.
            setChipDropSpot((current) => (
                current && spot && current.index === spot.index && current.left === spot.left && current.top === spot.top
                    ? current
                    : spot
            ));
        },
        onDragLeave: (event: React.DragEvent) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                setChipDropSpot(null);
            }
        },
        onDrop: (event: React.DragEvent) => {
            setChipDropSpot(null);
            if (!acceptsChipDrop(event)) return;
            event.preventDefault();
            const value = event.dataTransfer.getData(chipDragType(dragGroup));
            if (!value) return;
            const index = locateChipDrop(event)?.index ?? selectedItems.length;
            const next = [...selectedItems];
            const from = ownDraggedChipIndex();
            if (from >= 0) {
                // Reordered within this field: the source keeps it.
                const to = index > from ? index - 1 : index;
                if (to === from) return;
                const [moved] = next.splice(from, 1);
                next.splice(to, 0, moved);
                setSelectedItems(next);
                return;
            }
            if (draggedChip) draggedChip.dropped = true;
            next.splice(index, 0, toOption(value));
            setSelectedItems(dedupe(next));
        },
    } : {};
    const getChipDragProps = (item: Option) => (dragGroup && !props.disabled ? {
        draggable: true,
        onPointerDown: (event: React.PointerEvent) => {
            chipPointerTypeRef.current = event.pointerType;
        },
        onDragStart: (event: React.DragEvent) => {
            // A browser starts its own touch drag on the hold alone, unevenly
            // across browsers: a finger goes through the drag emulated after
            // a long press instead (see useChipTouchDrag).
            if (chipPointerTypeRef.current === "touch" && !isEmulatedChipDrag(event.nativeEvent)) {
                event.preventDefault();
                return;
            }
            const value = item.value || item.label;
            event.dataTransfer.setData(chipDragType(dragGroup), value);
            event.dataTransfer.effectAllowed = "move";
            draggedChip = { sourceId: comboboxId, value, dropped: false };
            const chip = event.currentTarget;
            // A finger's drag moves its copy itself (see useChipTouchDrag).
            if (!isEmulatedChipDrag(event.nativeEvent)) followMouseChipDrag(chip, event.nativeEvent);
            chip.classList.add(CHIP_DRAGGING_CLASS);
        },
        onDragEnd: (event: React.DragEvent) => {
            event.currentTarget.classList.remove(CHIP_DRAGGING_CLASS);
            const moved = draggedChip?.dropped ?? false;
            draggedChip = null;
            if (moved) removeSelectedItem(item);
        },
    } : {});

    const labelProps = getLabelProps();
    const renderMenuItem = (item: Option, index: number) => {
        const { itemProps: actionItemProps, button: actionButton } = optionActions.getOptionActionProps(item, index);
        return (
            <li
                className={clsx("c__combobox__menu__item", {
                    "c__combobox__menu__item--highlight": highlightedIndex === index,
                    "c__combobox__menu__item--selected": selectedItem === item,
                })}
                key={item.value}
                {...getItemProps({item, index})}
                data-option-index={index}
                {...actionItemProps}
            >
                {item.render?.() || <span>{item.label}</span>}
                {actionButton}
            </li>
        );
    };
    const valueRow = (
        <div className="c__combobox__value" ref={valueRowRef} {...chipTouchDrag.chipTouchHandlers}>
            {chipDropSpot && (
                <span
                    className="c__combobox__drop-caret"
                    aria-hidden="true"
                    style={{ left: chipDropSpot.left, top: chipDropSpot.top, height: chipDropSpot.height }}
                />
            )}
            {selectedItems.map((selectedItem, index) => (
                <input
                    key={`input-${selectedItems.length}-${index}`}
                    type="hidden"
                    name={props.name}
                    value={selectedItem.value || selectedItem.label}
                />
            ))}
            {Array.from(selectedItems).map((item, index) => (
                <Chip
                    {...getSelectedItemProps({
                        selectedItem: item,
                        index,
                    })}
                    {...getChipDragProps(item)}
                    key={`chip-${selectedItems.length}-${index}`}
                    label={item.label}
                    warning={getItemWarning?.(item.value || item.label)}
                    onRemove={() => {
                        removeSelectedItem(item)
                    }}
                />
            ))}
            <span className="c__combobox__input" data-value={inputValue}>
                <input {...inputProps} />
            </span>
        </div>
    );
    // Clicks on the actions must not bubble to the wrapper, which would
    // steal the focus back to this input.
    const actionsRow = (
        <div className="c__select__inner__actions" onClick={(e) => e.stopPropagation()}>
            {props.clearable && !props.disabled && selectedItems.length > 0 && (
                <Button
                  variant="tertiary"
                  size="nano"
                  aria-label={t('Clear selected items')}
                  className="c__select__inner__actions__clear"
                  onClick={() => setSelectedItems([])}
                  icon={<Icon name="close" />}
                  type="button"
                />
            )}
            {actions}
        </div>
    );

    return (
        <Field className={clsx("c__combobox", {
            "c__combobox--disabled": props.disabled,
            "c__combobox--error": props.state === "error",
            "c__combobox--success": props.state === "success",
            "c__combobox--inline": inline,
            // Same grid as the ui-kit inline fields: the label below lands
            // in the left column, the wrapper and footer in the right one.
            "c__field--inline": inline,
        }, className)} {...fieldProps}>
            {inline && props.label && (
                <label
                    className={clsx("c__combobox__label", {
                        "c__combobox__label--disabled": props.disabled,
                        "c__offscreen": props.hideLabel,
                    })}
                    htmlFor={labelProps.htmlFor}
                    id={labelProps.id}
                >
                    {props.label}
                </label>
            )}
            <div className="c__combobox__wrapper" ref={wrapperRef} onClick={() => {
                inputRef.current?.focus();
            }} {...chipDropHandlers}>
                {inline ? (
                    <>
                        {valueRow}
                        {actionsRow}
                    </>
                ) : (
                    <LabelledBox
                        label={props.label}
                        labelAsPlaceholder={showLabelAsPlaceholder}
                        htmlFor={labelProps.htmlFor}
                        labelId={labelProps.id}
                        hideLabel={props.hideLabel}
                        disabled={props.disabled}
                    >
                        {valueRow}
                        {actionsRow}
                    </LabelledBox>
                )}
                {/* Portaled so the menu is not clipped by a scrolling ancestor
                    (e.g. a compose window) and can flip above the field.
                    Non-modal: the focus stays in the input, downshift drives
                    the menu. react-aria closes it when an ancestor of the
                    field scrolls, as it does not follow the field. */}
                <Popover
                    className="c__combobox__popover"
                    triggerRef={wrapperRef}
                    isOpen={isOpen && (inputFocused || optionActions.keepsMenuDisplayed) && menuItems.length > 0}
                    onOpenChange={(open) => { if (!open) closeMenu(); }}
                    isNonModal
                    placement="bottom start"
                    maxHeight={MENU_MAX_HEIGHT}
                >
                    <ul
                        className="c__combobox__menu"
                        {...getMenuProps(optionActions.menuTouchHandlers, { suppressRefError: true })}
                    >
                        {filteredOptions.map((item, index) => renderMenuItem(item, index))}
                        {fallbackSection && fallbackOptions.length > 0 && (
                            // Pressing the title must not move the focus out of the input.
                            <li role="presentation" className="c__combobox__menu__section" onMouseDown={(e) => e.preventDefault()}>
                                <span id={fallbackSectionTitleId} className="c__combobox__menu__section__title">
                                    {fallbackSection.title}
                                </span>
                                <ul role="group" aria-labelledby={fallbackSectionTitleId} className="c__combobox__menu__section__options">
                                    {fallbackOptions.map((item, index) => renderMenuItem(item, filteredOptions.length + index))}
                                </ul>
                            </li>
                        )}
                    </ul>
                </Popover>
            </div>
        </Field>
    )
}
