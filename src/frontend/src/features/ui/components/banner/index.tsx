import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { Button, ButtonProps } from "@gouvfr-lasuite/cunningham-react";
import clsx from "clsx";
import { useId } from "react";

type BannerAction = Omit<ButtonProps, 'fullWidth' | 'size' | 'iconPosition' | 'icon'> & {
    label: string;
}

type BannerProps = {
    children: React.ReactNode;
    type: "info" | "error" | "warning" | "neutral";
    icon?: React.ReactNode;
    compact?: boolean;
    fullWidth?: boolean;
    actions?: BannerAction[];
}

/**
 * A banner component that displays a message with an icon and a type (error or info).
 * TODO: Migrate this component into our ui-kit
 */
export const Banner = ({ children, type = 'info', icon, compact = false, fullWidth = false, actions = [] }: BannerProps) => {
    const ariaLabelId = useId();

    return (
        <div
            className={clsx("banner", `banner--${type}`, { "banner--compact": compact, "banner--full-width": fullWidth })}
            role="alert"
            aria-live="polite"
            data-testid="banner"
            aria-labelledby={ariaLabelId}
        >
            <div
                className="banner__icon"
                aria-hidden="true"
            >
                {
                    icon ? icon : (
                        <Icon name={type === 'neutral' ? 'info' : type} type={IconType.OUTLINED} />
                    )
                }
            </div>
            <div className="banner__content">
                <div className="banner__content__text" id={ariaLabelId}>
                    {children}
                </div>
                {actions.length > 0 && (
                    <div className="banner__content__actions">
                        {actions.map(({ label, ...props }) => (
                            <Button
                                key={label}
                                size="nano"
                                variant="tertiary"
                                color={type}
                                {...props}
                            >
                                {label}
                            </Button>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
