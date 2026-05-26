import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Select } from "@gouvfr-lasuite/cunningham-react";

type CalendarOption = {
    id: string;
    name: string;
    color?: string | null;
};

interface CalendarSelectProps {
    calendars: CalendarOption[];
    value: string;
    onChange: (id: string) => void;
    className?: string;
}

function CalendarOptionItem({
    name,
    color,
}: {
    name: string;
    color: string;
}) {
    return (
        <span className="calendar-select__option">
            <span
                className="calendar-select__color"
                style={{ backgroundColor: color }}
            />
            {name}
        </span>
    );
}

export function CalendarSelect({
    calendars,
    value,
    onChange,
    className,
}: CalendarSelectProps) {
    const { t } = useTranslation();

    const options = useMemo(
        () =>
            calendars.map((cal) => {
                const color = cal.color || "#3788d8";
                const optionJsx = (
                    <CalendarOptionItem name={cal.name} color={color} />
                );
                return {
                    value: cal.id,
                    // Cunningham's Select renders ``label`` in the closed
                    // state and falls back to it in the open list when no
                    // ``render`` is provided. Passing JSX makes the swatch
                    // appear next to the calendar name everywhere; the
                    // type only declares ``string``, hence the cast.
                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                    label: optionJsx as any,
                    render: () => optionJsx,
                };
            }),
        [calendars],
    );

    return (
        <Select
            className={className}
            label={t("Choose calendar")}
            hideLabel
            value={value}
            onChange={(e) => onChange(String(e.target.value))}
            options={options}
            clearable={false}
            variant="classic"
            fullWidth
        />
    );
}
