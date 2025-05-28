import { HeaderProps } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@openfun/cunningham-react";
import { LanguagePicker } from "../language-picker";
import { useTranslation } from "react-i18next";


export const AnonymousHeader = ({
  leftIcon,
  onTogglePanel,
  isPanelOpen,
}: HeaderProps) => {
  const { t } = useTranslation();

  return (
    <div className="c__header c__header--anonymous">
      <div className="c__header__toggle-menu">
        <Button
          size="medium"
          onClick={onTogglePanel}
          aria-label={isPanelOpen ? t("Close the menu") : t("Open the menu")}
          color="tertiary-text"
          icon={
            <span className="material-icons clr-primary-800">
              {isPanelOpen ? "close" : "menu"}
            </span>
          }
        />
      </div>
      <div className="c__header__left">{leftIcon}</div>
      <div className="c__header__right">
        <div className="c__header__right__language-picker">
          <LanguagePicker />
        </div>
      </div>
    </div>
  );
};
