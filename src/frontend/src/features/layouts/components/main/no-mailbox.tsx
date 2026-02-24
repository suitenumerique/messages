import { Button } from "@gouvfr-lasuite/cunningham-react"
import { logout } from "@/features/auth";
import { useTranslation } from "react-i18next";
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link";
import { Icon, IconSize, IconType } from "@gouvfr-lasuite/ui-kit";

export const NoMailbox = () => {
    const { t } = useTranslation();
    return (
        <div id={SKIP_LINK_TARGET_ID} className="no-mailbox">
            <div>
                <Icon name="report" type={IconType.OUTLINED} size={IconSize.LARGE} aria-hidden="true" />
                <p>{t('No mailbox')}</p>
                <Button onClick={logout}>{t('Logout')}</Button>
            </div>
        </div>
    )
}
