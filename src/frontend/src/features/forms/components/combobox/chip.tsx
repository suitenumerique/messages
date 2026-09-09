import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@/features/ui/components/icon";
import { HTMLAttributes } from "react";
import { useTranslation } from "react-i18next";

type ChipProps = HTMLAttributes<HTMLDivElement> & {
    label: string;
    onRemove: () => void;
}

export const Chip = ({label, onRemove, ...props}: ChipProps) => {
    const { t } = useTranslation();

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
                        color="neutral"
                        variant="tertiary"
                        size="small"
                        icon={<Icon name="close" />}
                        aria-label={t("Remove")}
                    />
                )
            }
        </div>
    );
}
