import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { useState } from "react";
import { useDebounceCallback } from "@/hooks/use-debounce-callback";

type AdminSearchInputProps = {
    /** Visually hidden — used as the input's accessible name. */
    label: string;
    /** Visible placeholder shown when the input is empty. */
    placeholder: string;
    onChange: (value: string) => void;
    initialValue?: string;
};

const DEBOUNCE_MS = 200;

/**
 * Search input used at the top of admin lists. Maintains its own immediate
 * input value and reports changes upward through a debounced callback.
 */
export const AdminSearchInput = ({
    label,
    placeholder,
    onChange,
    initialValue = "",
}: AdminSearchInputProps) => {
    const [value, setValue] = useState<string>(initialValue);
    const debounced = useDebounceCallback(onChange, DEBOUNCE_MS);

    return (
        <div className="admin-search-input">
            <Icon className="admin-search-input__icon" name="search" type={IconType.OUTLINED} />
            <input
                className="admin-search-input__input"
                type="search"
                aria-label={label}
                placeholder={placeholder}
                value={value}
                onChange={(e) => {
                    setValue(e.target.value);
                    debounced(e.target.value);
                }}
            />
        </div>
    );
};
