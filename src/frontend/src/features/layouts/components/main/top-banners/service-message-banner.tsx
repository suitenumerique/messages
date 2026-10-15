import { useState } from "react";
import { useTranslation } from "react-i18next";
import { HeaderBanner } from "@gouvfr-lasuite/ui-components";
import { useConfig } from "@/features/providers/config";
import { I18nHelper } from "@/features/utils/i18n-helper";

/**
 * Service message configured from the Django admin (site configuration).
 * Closing it only hides it until the next page load.
 */
export const ServiceMessageBanner = () => {
  const { SERVICE_MESSAGE, BASE_LANGUAGE } = useConfig();
  const { i18n } = useTranslation();
  const [isClosed, setIsClosed] = useState(false);

  if (!SERVICE_MESSAGE || isClosed) return null;

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
      onClose={() => setIsClosed(true)}
    />
  );
};
