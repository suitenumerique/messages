import { useTranslation } from "react-i18next";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { Banner } from "@/features/ui/components/banner";

type ComposeDraftBannerProps = {
    onFocus: () => void;
};

/**
 * Shown in place of the inline reply form while its draft is being edited in
 * a floating compose window. The CTA restores/focuses that window.
 */
export const ComposeDraftBanner = ({ onFocus }: ComposeDraftBannerProps) => {
    const { t } = useTranslation();

    return (
        <div className="compose-draft-banner">
            <Banner
                type="info"
                icon={<Icon name="open_in_new" type={IconType.OUTLINED} />}
                fullWidth
                actions={[
                    {
                        label: t("Show window"),
                        onClick: onFocus,
                        variant: "secondary",
                    },
                ]}
            >
                <p>{t("You are editing this draft in a separate window.")}</p>
            </Banner>
        </div>
    );
};
