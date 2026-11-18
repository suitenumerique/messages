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

export type { ComboBoxOptionAction } from "./use-option-actions";

// react-aria lowers it to the room left on the side the menu opens to.
const MENU_MAX_HEIGHT = 320;

export type ComboBoxProps =  {
    onInputChange?: (value: string) => void;
    value?: string[],
    defaultValue?: string[],
    onChange?: (value: string[]) => void,
    renderChipLabel?: (item: Option) => string,
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
    const { valueTransformer, getItemWarning, getOptionAction, fallbackSection, className, variant = "floating", actions, ...fieldProps } = props;
    const fallbackSectionTitleId = useId();
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
     * Two entries that differ only by what the transformer folds (domain
     * case, say) become the same address once transformed, so without this
     * a restored draft renders duplicate chips and `onChange` submits the
     * same recipient twice.
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
        // Compare on the transformed value: a stored contact whose address
        // differs only by what the transformer canonicalizes (e.g. domain
        // case) would otherwise stay offered after being picked, and could be
        // added a second time.
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
                if (type === useCombobox.stateChangeTypes.InputBlur && optionActions.ignoresInputBlur()) {
                    return state;
                }
                return changes;
            },
            onSelectedItemChange: ({ selectedItem }) => {
                if (!selectedItem) return;
                // Menu entries come from stored contacts, which keep the case
                // they were received in; canonicalize them like typed input.
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
                    if (!optionActions.ignoresInputBlur()) extractNewItemsIfNeeded();
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
        <div className="c__combobox__value">
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
            }}>
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
