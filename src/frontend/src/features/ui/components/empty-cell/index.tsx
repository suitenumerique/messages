import { Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { ReactNode } from "react";

type EmptyCellProps = {
    tooltip?: ReactNode;
};

/**
 * Em-dash placeholder for a DataGrid cell with no value, optionally wrapped
 * in a Tooltip explaining the reason.
 */
export const EmptyCell = ({ tooltip }: EmptyCellProps) => {
    const dash = (
        <span style={{ color: "var(--c--contextuals--content--semantic--neutral--tertiary)" }}>
            —
        </span>
    );
    if (!tooltip) return dash;
    return (
        <Tooltip content={tooltip} placement="top">
            {dash}
        </Tooltip>
    );
};
