import { SearchInput } from "@/features/forms/components/search-input";
import { HeaderProps, useResponsive } from "@gouvfr-lasuite/ui-kit";
import { Button, useCunningham } from "@openfun/cunningham-react";
import { LanguagePicker } from "../language-picker";
import { useAuth } from "@/features/auth";
import UserMenu from "./user-menu";


export const AuthenticatedHeader = ({
  leftIcon,
  onTogglePanel,
  isPanelOpen,
}: HeaderProps) => {
  const { t } = useCunningham();
  const { isDesktop } = useResponsive();

  return (
    <div className="c__header">
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
      <div className="c__header__center">
        <SearchInput />
      </div>
      {isDesktop && (
        <div className="c__header__right">
          <HeaderRight />
        </div>
      )}
    </div>
  );
};

export const HeaderRight = () => {
  const { user } = useAuth();
  
  return (
    <>
      {user && <UserMenu />}
      <LanguagePicker />
    </>
  );
};
