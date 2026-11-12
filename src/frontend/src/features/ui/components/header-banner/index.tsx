import { Button, ButtonProps, useResponsive } from "@gouvfr-lasuite/ui-components";
import clsx from "clsx";
import { HTMLAttributes, ReactNode } from "react";
import { useTranslation } from "react-i18next";

export type HeaderBannerColor = "brand" | "neutral" | "error" | "warning";

type HeaderBannerCTAProps = Omit<ButtonProps, "size" | "variant" | "color" | "icon" | "children"> & {
    label: string;
    icon: ReactNode;
};

type HeaderBannerProps = HTMLAttributes<HTMLDivElement> & {
    label: ReactNode;
    color?: HeaderBannerColor;
    ctaProps?: HeaderBannerCTAProps;
    variant?: "centered";
    /** On small viewports, move the CTA below the label instead of collapsing it to its icon. */
    stackOnMobile?: boolean;
};

/**
 * Full width banner displayed on top of a layout area to announce a message
 * that applies to the whole area.
 *
 * @TODO: Replace by the `HeaderBanner` of `@gouvfr-lasuite/ui-components` once
 * released (suitenumerique/ui-kit#253). Props and class names mirror it so the
 * swap only means changing the import and deleting this component, except for
 * the `neutral` color, the `centered` variant and `stackOnMobile` which the
 * ui-kit does not offer yet.
 */
export const HeaderBanner = ({ label, color = "brand", ctaProps, className, variant, stackOnMobile = false, ...props }: HeaderBannerProps) => {
    const { t } = useTranslation();
    const { isMobile } = useResponsive();
    const isStacked = isMobile && stackOnMobile;

    return (
        <div
            className={clsx("c__header-banner", `c__header-banner--${color}`, variant === "centered" && "c__header-banner--centered", isStacked && "c__header-banner--stacked", className)}
            role="region"
            aria-label={t("Announcement")}
            {...props}
        >
            <div className="c__header-banner__label">{label}</div>
            {ctaProps && (
                <div className="c__header-banner__cta">
                    <HeaderBannerCTA {...ctaProps} color={color} iconOnly={isMobile && !isStacked} />
                </div>
            )}
        </div>
    );
};

const HeaderBannerCTA = ({ label, icon, color, iconOnly, ...props }: HeaderBannerCTAProps & { color: HeaderBannerColor; iconOnly: boolean }) => {
    return (
        <Button
            type="button"
            size="nano"
            color={color}
            variant={color === "neutral" ? "tertiary" : "primary"}
            icon={icon}
            // Collapsed to its icon on small viewports, the label becomes the accessible name.
            aria-label={iconOnly ? label : undefined}
            {...props}
        >
            {iconOnly ? undefined : label}
        </Button>
    );
};
