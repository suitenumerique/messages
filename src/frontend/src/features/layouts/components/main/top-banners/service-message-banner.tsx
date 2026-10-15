import { useTranslation } from "react-i18next";
import { HeaderBanner } from "@gouvfr-lasuite/ui-components";
import { useConfig } from "@/features/providers/config";
import { I18nHelper } from "@/features/utils/i18n-helper";
import { useBannerDismissal } from "./use-banner-dismissal";

/**
 * Service message configured from the Django admin (site configuration).
 * Closing it hides it everywhere until the next page load.
 */
export const ServiceMessageBanner = () => {
  const { SERVICE_MESSAGE, BASE_LANGUAGE } = useConfig();
  const { i18n } = useTranslation();
  const { isDismissed, dismiss } = useBannerDismissal("service-message");

  if (!SERVICE_MESSAGE || isDismissed) return null;

  const language = i18n.resolvedLanguage ?? BASE_LANGUAGE;
  const label = I18nHelper.pickTranslation(SERVICE_MESSAGE.message, language, BASE_LANGUAGE);
  if (!label) return null;

  const link = SERVICE_MESSAGE.link;
  const linkLabel = link && I18nHelper.pickTranslation(link.label, language, BASE_LANGUAGE);

  return (
    <HeaderBanner
      label={label}
      color={SERVICE_MESSAGE.variant}
      ctaProps={
        link && linkLabel
          ? { label: linkLabel, href: link.url, target: "_blank", rel: "noopener noreferrer" }
          : undefined
      }
      onClose={dismiss}
    />
  );
};
