import { logout } from "@/features/auth";
import { DropdownMenu } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@openfun/cunningham-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

const UserMenu = () => {
    const [isOpen, setIsOpen] = useState(false);
    const { t } = useTranslation();

    return (
        <DropdownMenu
          options={[
            {
              label: t("logout"),
              icon: <span className="material-icons">logout</span>,
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
