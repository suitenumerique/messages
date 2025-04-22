import { useMailboxContext } from "@/features/mailbox/provider";
import { DropdownMenu } from "@gouvfr-lasuite/ui-kit"
import { Button, Tooltip } from "@openfun/cunningham-react"
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useTranslation } from "react-i18next";

type ActionBarProps = {
    handleReplyAll: () => void;
}

export const ActionBar = ({ handleReplyAll }: ActionBarProps) => {
    const { t } = useTranslation();
    const { selectThread } = useMailboxContext();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const params = useParams<{ mailboxId: string }>();
    const router = useRouter();

    const handleCloseThread = () => {
        selectThread(null);
        router.push(`/mailbox/${params?.mailboxId}`);
    }

    return (
        <div className="thread-action-bar">
            <div className="thread-action-bar__left">
                <Button
                    onClick={handleCloseThread}
                    color="tertiary-text"
                    aria-label={t('tooltips.close_thread')}
                    size="small"
                    icon={<span className="material-icons">close</span>}
                >
                    {t('actions.close_thread')}
                </Button>
            </div>
            <div className="thread-action-bar__right">
                <Button
                    onClick={handleReplyAll}
                    color="primary"
                    aria-label={t('tooltips.reply_all')}
                    size="small"
                    icon={<span className="material-icons">reply_all</span>}
                >
                    {t('actions.reply_all')}
                </Button>
                <Tooltip content={t('tooltips.reply')}>
                    <Button
                        color="primary-text"
                        aria-label={t('tooltips.reply')}
                        size="small"
                        icon={<span className="material-icons">reply</span>}
                    />
                </Tooltip>
                <Tooltip content={t('tooltips.forward')}>
                    <Button
                        color="primary-text"
                        aria-label={t('tooltips.forward')}
                        size="small"
                        icon={<span className="material-icons">forward</span>}
                    />
                </Tooltip>
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