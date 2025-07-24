import { Icon } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@openfun/cunningham-react";
import { HTMLAttributes } from "react";

type ChipProps = HTMLAttributes<HTMLDivElement> & {
    label: string;
    onRemove: () => void;
}

export const Chip = ({label, onRemove, ...props}: ChipProps) => {

    return (
        <div className="c__combobox__chip" {...props}>
            <span className="c__combobox__chip__label">{label}</span>
            {
                onRemove && (
                    <Button
                        className="c__combobox__chip__clear"
                        onClick={(e) => {
                            e.stopPropagation();
                            onRemove();
                        }}
                        color="tertiary-text"
                        size="small"
                        icon={<Icon name="close" />}
                        aria-label="Remove"
                    />
                )
            }
        </div>
    );
}
