import { Field, LabelledBox, SelectProps } from "@openfun/cunningham-react";
import clsx from "clsx";
import { useCombobox, useMultipleSelection } from "downshift"
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chip } from "./chip";
import { Button, Option } from "@openfun/cunningham-react";

export type ComboBoxProps =  {
    onInputChange?: (value: string) => void;
    value?: string[],
    defaultValue?: string[],
    onChange?: (value: string[]) => void,
    renderChipLabel?: (item: Option) => string,
    valueValidator?: (value: string) => boolean,
} & Omit<SelectProps, 'value' | 'defaultValue' | 'onChange'>;

export const ComboBox = (props: ComboBoxProps) => {
    const [inputValue, setInputValue] = useState('');
    const [inputFocused, setInputFocused] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);
    const { getSelectedItemProps, getDropdownProps, removeSelectedItem, addSelectedItem, selectedItems, setSelectedItems } = useMultipleSelection<Option>({
        initialSelectedItems: (props.value || props.defaultValue || []).map(item => ({
            label: item,
            value: item,
        })),
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
    const filteredOptions = useMemo(() => {
        // Limit the options list to 10 items to avoid performance issues
        return props.options.filter(option => !selectedItems.find(item => item.value === option.value)).slice(0, 10);
    }, [props.options, selectedItems])
    const showLabelAsPlaceholder = useMemo(
        () => selectedItems.length === 0 && !inputValue && !inputFocused,
        [inputFocused, inputValue, selectedItems]
    )
    const extractNewItemsIfNeeded = () => {
        const [newItems, rest] = parseInputValue(inputValue);
        if (newItems.length > 0) {
            setSelectedItems([...selectedItems, ...newItems]);
            setInputValue(rest);
            return true;
        }
        return false;
    }

    const {
        getInputProps,
        getItemProps,
        getMenuProps,
        getLabelProps,
        isOpen,
        highlightedIndex,
        selectedItem,
    } = useCombobox(
        {
            items: filteredOptions,
            itemToString: (item) => item?.label || item?.value || '',
            onSelectedItemChange: ({ selectedItem }) => {
                if (!selectedItem) return;
                addSelectedItem(selectedItem);
                setInputValue('');
            },
            onInputValueChange: ({ inputValue: newInputValue }) => {
                const isPasted = newInputValue.length - inputValue?.length > 1
                const [newItems, rest] = parseInputValue(newInputValue);
                if (isPasted && newItems.length > 0) {
                    setSelectedItems([...selectedItems, ...newItems]);
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
                onBlurCapture: extractNewItemsIfNeeded,
                onBlur: () => { setInputFocused(false) },
                onFocus: () => { setInputFocused(true) },
                onChange: (e) => {
                    // Synchronously update the input value
                    // This is important to avoid cursor jumping to the end of the input
                    // https://dev.to/kwirke/solving-caret-jumping-in-react-inputs-36ic
                    setInputValue((e.target as HTMLInputElement).value);
                },
                onKeyDown: (e) => {
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

        return [validValues.map(item => ({
            label: item,
            value: item,
        })), validValues.length === 0 ? value : invalidValues.join(', ')];
    }, [props.valueValidator]);

    useEffect(() => {
        props.onChange?.(selectedItems.map(item => item.value || item.label));
    }, [selectedItems]);

    return (
        <Field className={clsx("c__combobox", {
            "c__combobox--disabled": props.disabled,
            "c__combobox--error": props.state === "error",
            "c__combobox--success": props.state === "success",
        })} {...props}>
            <div className="c__combobox__wrapper" onClick={() => {
                inputRef.current?.focus();
            }}>
                <LabelledBox
                    label={props.label}
                    labelAsPlaceholder={showLabelAsPlaceholder}
                    htmlFor={getLabelProps().htmlFor}
                    labelId={getLabelProps().id}
                    hideLabel={props.hideLabel}
                    disabled={props.disabled}
                >
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
                                onRemove={() => {
                                    removeSelectedItem(item)
                                }}
                            />
                        ))}
                        <span className="c__combobox__input" data-value={inputValue}>
                            <input {...inputProps} />
                        </span>
                    </div>
                    <div className="c__select__inner__actions">
                    {props.clearable && !props.disabled && selectedItems.length > 0 && (
                        <Button
                          color="tertiary-text"
                          size="nano"
                          aria-label="Clear selected items"
                          className="c__select__inner__actions__clear"
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedItems([]);
                          }}
                          icon={<span className="material-icons">close</span>}
                          type="button"
                        />
                    )}
                    </div>
                </LabelledBox>
                <ul className={
                    clsx("c__combobox__menu", {
                        "c__combobox__menu--opened": isOpen,
                    })} {...getMenuProps()}>
                {isOpen && inputFocused && filteredOptions.length > 0 &&
                    filteredOptions.map((item, index) => (
                    <li
                        className={clsx("c__combobox__menu__item", {
                            "c__combobox__menu__item--highlight": highlightedIndex === index,
                            "c__combobox__menu__item--selected": selectedItem === item,
                        })}
                        key={item.value}
                        {...getItemProps({item, index})}
                    >
                        {item.render?.() || <span>{item.label}</span>}
                    </li>
                    ))}
                </ul>
            </div>
        </Field>
    )
}
