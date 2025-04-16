import { Button } from "@openfun/cunningham-react";
import { useTranslation } from "react-i18next";

export const MailboxPanelActions = () => {
    const { t } = useTranslation();

    return (
        <div className="mailbox-panel-actions">
            <Button icon={<span className="material-icons">edit_note</span>}>
                {t("actions.new_message")}
            </Button>
            {/* <Button
                icon={<span className="material-icons">search</span>}
                color="primary-text"
                aria-label="Rechercher"
            /> */}
        </div>
    )
}