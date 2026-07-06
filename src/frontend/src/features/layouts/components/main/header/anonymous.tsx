import { Button, HeaderProps, useResponsive } from "@gouvfr-lasuite/ui-components";
import { LeftPanel, XMark } from "@gouvfr-lasuite/ui-components/icons";
import { useTranslation } from "react-i18next";
import { LanguagePicker } from "../language-picker";
import { Icon } from "@/features/ui/components/icon";

export const AnonymousHeader = ({
  leftIcon,
  onTogglePanel,
  isPanelOpen,
}: HeaderProps) => {
  const { t } = useTranslation();
  const { isDesktop } = useResponsive();

  return (
    <div className="c__header c__header--anonymous">
      <div className="c__header__toggle-menu">
        <Button
          size="medium"
          onClick={onTogglePanel}
          aria-label={isPanelOpen ? t("Close the menu") : t("Open the menu")}
          color="brand"
          variant="tertiary"
          icon={<Icon icon={isPanelOpen ? XMark : LeftPanel} />}
        />
      </div>
      <div className="c__header__left">
        {leftIcon}
      </div>
      <div className="c__header__right">
        {isDesktop && <LanguagePicker />}
      </div>
    </div>
  );
};
