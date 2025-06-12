import clsx from "clsx";
import { useTranslation } from "react-i18next";

type BannerProps = {
    children: React.ReactNode;
    type: "info" | "error";
    icon?: React.ReactNode;
}

/**
 * A banner component that displays a message with an icon and a type (error or info).
 * TODO: Migrate this component into our ui-kit
 */
export const Banner = ({ children, type = 'info', icon }: BannerProps) => {
    const { t } = useTranslation();

    return (
        <div 
            className={clsx("banner", `banner--${type}`)}
            role="alert"
            aria-live="polite"
            aria-label={t(`aria.labels.banner.${type}`)}
        >
            <div className="banner__content">
                <div 
                    className="banner__content__icon"
                    aria-hidden="true"
                >
                    {
                        icon ? icon : (
                            <span className="material-icons">{type}</span>
                        )
                    }
                </div>
                <div className="banner__content__text">
                    {children}
                </div>
            </div>
        </div>
    );
}
