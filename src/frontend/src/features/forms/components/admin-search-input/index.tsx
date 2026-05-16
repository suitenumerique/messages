import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { Input } from "@gouvfr-lasuite/cunningham-react";
import { useEffect, useState } from "react";
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

    // Re-sync when the parent resets the query externally (e.g. switching
    // resources). Cancel any pending debounced call so a still-buffered
    // keystroke from the previous resource doesn't fire onChange with
    // stale input after the reset.
    useEffect(() => {
        debounced.cancel();
        setValue(initialValue);
    }, [initialValue, debounced]);

    return (
        <Input
            icon={<Icon name="search" type={IconType.OUTLINED} />}
            type="search"
            label={label}
            placeholder={placeholder}
            value={value}
            onChange={(e) => {
                setValue(e.target.value);
                debounced(e.target.value);
            }}
            fullWidth
        />
    );
};
