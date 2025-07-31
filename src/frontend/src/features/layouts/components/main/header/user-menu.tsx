import { logout, useAuth } from "@/features/auth";
import useAbility, { Abilities } from "@/hooks/use-abilty";
import { DropdownMenu, Icon, IconSize, IconType } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@openfun/cunningham-react";
import { useRouter } from "next/router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

const UserMenu = () => {
  const [isOpen, setIsOpen] = useState(false);
  const { t } = useTranslation();
  const router = useRouter();
  const { user } = useAuth();
  const canAccessDomainAdmin = useAbility(Abilities.CAN_VIEW_DOMAIN_ADMIN);

  if (!user) {
    return null;
  }

  return (
    <div className="user-menu">
      <DropdownMenu
        options={[
          ...(canAccessDomainAdmin ? [{
            label: t("user_menu.domain_admin"),
            icon: <Icon name="domain" />,
            callback: () => router.push("/domain"),
          }] : []),
          {
            label: t("user_menu.logout"),
            icon: <Icon name="logout" />,
            callback: logout,
          },
        ]}
        isOpen={isOpen}
        onOpenChange={setIsOpen}
      >
        <Button
          className="user-menu__cta"
          color="primary-text"
          onClick={() => setIsOpen(!isOpen)}
          icon={
            <span className="material-icons">
              {isOpen ? "arrow_drop_up" : "arrow_drop_down"}
            </span>
          }
          iconPosition="right"
        >
          <div className="user-menu__cta-content">
            <Icon size={IconSize.MEDIUM} name="account_circle" type={IconType.OUTLINED} />
            <span className="user-menu__account">
              <span className="user-menu__account-name">{user.full_name || user.email}</span>
              {
                user.full_name &&
                <span className="user-menu__account-email">{user.email}</span>
              }
            </span>
          </div>
        </Button>
      </DropdownMenu>
    </div>
  )
}

export default UserMenu;
