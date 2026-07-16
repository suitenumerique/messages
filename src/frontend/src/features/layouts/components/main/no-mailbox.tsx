import { Button } from "@gouvfr-lasuite/cunningham-react"
import { logout, useAuth } from "@/features/auth";
import { useTranslation } from "react-i18next";
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link";

export const NoMailbox = () => {
    const { t } = useTranslation();
    const { user } = useAuth();

    return (
        <div id={SKIP_LINK_TARGET_ID} className="no-mailbox">
            <div>
                <img src="/images/svg/no-access.svg" alt="" width={102} height={72} />
                <h1>{t('No access')}</h1>
                <p>
                    {t(
                        'You are signed in with the address {{email}}, but this account has not been configured to use the service.',
                        { email: user?.email }
                    )}
                </p>
            </div>
            <Button onClick={() => logout()} size="small">
                {t('Sign in with another address')}
            </Button>
        </div>
    )
}
