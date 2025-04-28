import { useMailboxContext } from "@/features/mailbox/provider";
import Bar from "@/features/ui/components/bar";
import { DropdownMenu } from "@gouvfr-lasuite/ui-kit"
import { Button, Tooltip } from "@openfun/cunningham-react"
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useTranslation } from "react-i18next";


export const ActionBar = () => {
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
        <Bar className="thread-action-bar">
            <div className="thread-action-bar__left">
            <Tooltip content={t('actions.close_thread')} placement="right">
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
                <Tooltip content={t('actions.archive')}>
                    <Button
                        color="primary-text"
                        aria-label={t('actions.archive')}
                        size="small"
                        icon={<span className="material-icons">archive</span>}
                    />
                </Tooltip>
                <Tooltip content={t('actions.delete')}>
                    <Button
                        color="primary-text"
                        aria-label={t('actions.delete')}
                        size="small"
                        icon={<span className="material-icons">delete</span>}
                    />
                </Tooltip>
                <Tooltip content={t('actions.mark_as_unread')}>
                    <Button
                        color="primary-text"
                        aria-label={t('actions.mark_as_unread')}
                        size="small"
                        icon={<span className="material-icons">mark_email_unread</span>}
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
        </Bar>
    )
}