import { useRouter } from "next/router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Tooltip } from "@openfun/cunningham-react";
import { DropdownMenu, Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useLayoutContext } from "../../../main";
import useAbility, { Abilities } from "@/hooks/use-ability";

export const MailboxPanelActions = () => {
    const { t } = useTranslation();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const router = useRouter();
    const { selectedMailbox } = useMailboxContext();
    const { closeLeftPanel } = useLayoutContext();
    const canWriteMessages = useAbility(Abilities.CAN_WRITE_MESSAGES, selectedMailbox);

    const goToNewMessageForm = (event: React.MouseEvent<HTMLButtonElement | HTMLAnchorElement>) => {
        event.preventDefault();
        if (!canWriteMessages) return;
        closeLeftPanel();
        router.push(`/mailbox/${selectedMailbox!.id}/new`);
    }

    if (!selectedMailbox) return null;

    return (
        <div className="mailbox-panel-actions">
            <div>
            {
                <Button
                    onClick={goToNewMessageForm}
                    href={`/mailbox/${selectedMailbox.id}/new`}
                    icon={<span className="material-icons">edit_note</span>}
                    disabled={!canWriteMessages}
                >
                    {t("actions.new_message")}
                </Button>
            }
            </div>
            <div className="mailbox-panel-actions__extra">
                <DropdownMenu
                    isOpen={isDropdownOpen}
                    onOpenChange={setIsDropdownOpen}
                    options={[
                        {
                            label: t("actions.import_messages"),
                            icon: <Icon name="archive" type={IconType.OUTLINED} />,
                            callback: () => {
                                window.location.hash = `#modal-message-importer`;
                            }
                        },
                    ]}
                >
                    <Tooltip content={t("tooltips.more_options")} placement="left">
                        <Button
                            onClick={() => setIsDropdownOpen(true)}
                            icon={<Icon name="settings" type={IconType.OUTLINED} />}
                            aria-label={t("mailbox-panel.actions.more_options")}
                            color="tertiary-text"
                        />
                    </Tooltip>
                </DropdownMenu>
            </div>
        </div>
    )
}

