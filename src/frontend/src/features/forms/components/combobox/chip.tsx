import { Icon, IconType, Tooltip } from "@gouvfr-lasuite/ui-components";
import { Button } from "@gouvfr-lasuite/ui-components";
import clsx from "clsx";
import { HTMLAttributes } from "react";
import { useTranslation } from "react-i18next";

type ChipProps = HTMLAttributes<HTMLDivElement> & {
    label: string;
    onRemove: () => void;
    // Short warning attached to this value. Rendered as an icon in the chip
    // and read out to assistive technologies; the tooltip shown while the
    // chip is hovered or focused is a desktop bonus only, the surrounding
    // field is expected to carry the text where hover does not exist (touch
    // devices).
    warning?: string;
}

export const Chip = ({ label, onRemove, warning, className, ...props }: ChipProps) => {
    const { t } = useTranslation();

    const chip = (
        <div className={clsx("c__combobox__chip", { "c__combobox__chip--warning": warning }, className)} {...props}>
            {warning && (
                <span className="c__combobox__chip__warning" role="img" aria-label={warning}>
                    <Icon name="warning" type={IconType.OUTLINED} />
                </span>
            )}
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

    if (!warning) return chip;
    // The tooltip trigger takes over the `ref` of its child, and the chip's
    // ref belongs to downshift (keyboard navigation between chips): anchor
    // the tooltip on a wrapper so both keep working.
    return (
        <Tooltip content={warning} placement="top">
            <span className="c__combobox__chip__tooltip-anchor">{chip}</span>
        </Tooltip>
    );
}
