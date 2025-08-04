import { useRouter } from "next/router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Tooltip } from "@openfun/cunningham-react";
import { DropdownMenu, Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useLayoutContext } from "../../../main";

export const MailboxPanelActions = () => {
    const { t } = useTranslation();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const router = useRouter();
    const { selectedMailbox } = useMailboxContext();
    const { closeLeftPanel } = useLayoutContext();

    const goToNewMessageForm = (event: React.MouseEvent<HTMLButtonElement | HTMLAnchorElement>) => {
        event.preventDefault();
        closeLeftPanel();
        router.push(`/mailbox/${selectedMailbox!.id}/new`);
    }

    if (!selectedMailbox) return null;

    return (
        <div className="mailbox-panel-actions">
            <Button
                onClick={goToNewMessageForm}
                href={`/mailbox/${selectedMailbox.id}/new`}
                icon={<span className="material-icons">edit_note</span>}
            >
                {t("actions.new_message")}
            </Button>
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

