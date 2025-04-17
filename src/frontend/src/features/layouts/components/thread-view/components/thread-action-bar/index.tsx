import { useMailboxContext } from "@/features/mailbox/provider";
import { DropdownMenu } from "@gouvfr-lasuite/ui-kit"
import { Button, Tooltip } from "@openfun/cunningham-react"
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useTranslation } from "react-i18next";

export const ActionBar = () => {
    const { t } = useTranslation();
    const { selectThread } = useMailboxContext();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const { mailboxId } = useParams<{ mailboxId: string }>();
    const router = useRouter();

    const handleCloseThread = () => {
        selectThread(null);
        router.push(`/mailbox/${mailboxId}`);
    }

    return (
        <div className="thread-action-bar">
            <div className="thread-action-bar__left">
            <Tooltip content={t('tooltips.close_thread')} placement="right">
                    <Button
                        onClick={handleCloseThread}
                        color="tertiary-text"
                        aria-label={t('tooltips.close_thread')}
                        size="small"
                        icon={<span className="material-icons">close</span>}
                    />
                </Tooltip>
            </div>
            <div className="thread-action-bar__right">
                <Button
                    color="primary"
                    size="small"
                    icon={<span className="material-icons">reply</span>}
                >
                    {t('actions.reply')}
                </Button>
                <Tooltip content={t('tooltips.delete')}>
                    <Button
                        color="primary-text"
                        aria-label={t('tooltips.delete')}
                        size="small"
                        icon={<span className="material-icons">delete</span>}
                    />
                </Tooltip>
                <DropdownMenu
                    isOpen={isDropdownOpen}
                    onOpenChange={setIsDropdownOpen}
                    options={[
                        {
                            label: t('actions.reply_all'),
                            icon: <span className="material-icons">reply_all</span>,
                        },
                        {
                            label: t('actions.forward'),
                            icon: <span className="material-icons">forward</span>,
                            showSeparator: true,
                        },
                        {
                            label: t('actions.print'),
                            icon: <span className="material-icons">print</span>,
                        },
                    ]}
                >
                    <Tooltip content={t('tooltips.more_options')} placement="left">
                        <Button
                            onClick={() => setIsDropdownOpen(true)}
                            icon={<span className="material-icons">more_vert</span>}
                            color="primary-text"
                            aria-label={t('tooltips.more_options')}
                            size="small"
                        />
                    </Tooltip>
                </DropdownMenu>
            </div>
        </div>
    )
}