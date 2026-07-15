import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";

/**
 * Header for the new-import sub-view, rendered in the settings tab's title slot
 * so the "back" affordance sits *above* the "Start a new import" heading (the
 * conventional order) rather than below it in the form body.
 */
export const ImportNewTitle = ({ onBack }: { onBack: () => void }) => {
  const { t } = useTranslation();
  return (
    <span className="import-new-title">
      <Button
        className="import-new-title__back"
        type="button"
        size="small"
        color="neutral"
        variant="tertiary"
        icon={<Icon name="arrow_back" />}
        onClick={onBack}
      >
        {t("Back to imports")}
      </Button>
      <span className="import-new-title__heading">{t("Start a new import")}</span>
    </span>
  );
};
