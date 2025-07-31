import { logout } from "@/features/auth";
import useAbility, { Abilities } from "@/hooks/use-abilty";
import { DropdownMenu, Icon } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@openfun/cunningham-react";
import { useRouter } from "next/router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

const UserMenu = () => {
    const [isOpen, setIsOpen] = useState(false);
    const { t } = useTranslation();
    const router = useRouter();
    const canAccessDomainAdmin = useAbility(Abilities.CAN_VIEW_DOMAIN_ADMIN);

    return (
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
            color="primary-text"
            onClick={() => setIsOpen(!isOpen)}
            icon={
              <span className="material-icons">
                {isOpen ? "arrow_drop_up" : "arrow_drop_down"}
              </span>
            }
            iconPosition="right"
          >
            <span className="text-nowrap">{t("my_account")}</span>
          </Button>
        </DropdownMenu>
    )
}

export default UserMenu;
