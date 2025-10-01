import { DropdownMenu, HeaderProps, Icon, IconType, useResponsive, UserMenu, VerticalSeparator } from "@gouvfr-lasuite/ui-kit";
import { Button, useCunningham } from "@openfun/cunningham-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useRouter } from "next/router";
import { SearchInput } from "@/features/forms/components/search-input";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { useAuth, logout } from "@/features/auth";
import { LanguagePicker } from "@/features/layouts/components/main/language-picker";


type AuthenticatedHeaderProps = HeaderProps & {
  hideSearch?: boolean;
}

export const AuthenticatedHeader = ({
  leftIcon,
  onTogglePanel,
  isPanelOpen,
  hideSearch = false,
}: AuthenticatedHeaderProps) => {
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
        {!hideSearch && <SearchInput />}
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
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const { user } = useAuth();
  const { isDesktop } = useResponsive();
  const { t } = useTranslation();
  const router = useRouter();
  const canAccessDomainAdmin = useAbility(Abilities.CAN_VIEW_DOMAIN_ADMIN);

  return (
    <>
      <DropdownMenu
          isOpen={isDropdownOpen}
          onOpenChange={setIsDropdownOpen}
          options={[
              ...(canAccessDomainAdmin ? [{
                label: t("Domain admin"),
                icon: <Icon name="domain" />,
                callback: () => router.push("/domain"),
              }] : []),
              {
                  label: t("Import messages"),
                  icon: <Icon name="archive" type={IconType.OUTLINED} />,
                  callback: () => {
                      window.location.hash = `#modal-message-importer`;
                  }
              },
          ]}
      >
      <Button
          onClick={() => setIsDropdownOpen(true)}
          icon={<Icon name="settings" type={IconType.OUTLINED} />}
          aria-label={t("More options")}
          color="tertiary-text"
      />
      </DropdownMenu>
      {isDesktop && <VerticalSeparator size="24px" />}
      <UserMenu
        user={user ? {
          full_name: user.full_name ?? undefined,
          email: user.email || ""
        } : null}
        logout={logout}
        footerAction={
          <LanguagePicker size="small" color="secondary" />
        }
      />
    </>
  );
};
