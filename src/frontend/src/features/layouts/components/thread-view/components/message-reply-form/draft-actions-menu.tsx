import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { DropdownMenu, Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { Message } from "@/features/api/gen";
import useCopyDeepLink from "@/features/message/use-copy-deep-link";

type DraftActionsMenuProps = {
    message: Message;
    onDelete?: () => void;
};

/**
 * Secondary actions available on a draft's compose surface. Only exposes the
 * actions that are meaningful on an unsent draft. Print and raw-email download
 * are intentionally absent: a draft has no rendered body to print and no RFC822
 * blob to download (both only exist once the message is sent).
 */
const DraftActionsMenu = ({ message, onDelete }: DraftActionsMenuProps) => {
    const { t } = useTranslation();
    const [isOpen, setIsOpen] = useState(false);
    const copyDeepLink = useCopyDeepLink();

    const options = [
        {
            label: t('Copy link to message'),
            icon: <Icon type={IconType.FILLED} name="link" />,
            callback: () => copyDeepLink({ messageId: message.id }),
        },
        ...(onDelete ? [{
            label: t('Delete draft'),
            icon: <Icon type={IconType.FILLED} name="delete" />,
            callback: onDelete,
            showSeparator: true,
        }] : []),
    ];

    return (
        <DropdownMenu isOpen={isOpen} onOpenChange={setIsOpen} options={options}>
            <Tooltip content={t('More options')} placement="left">
                <Button
                    onClick={() => setIsOpen(true)}
                    icon={<Icon type={IconType.FILLED} name="more_vert" />}
                    color="brand"
                    variant="tertiary"
                    size="small"
                    aria-label={t('More options')}
                />
            </Tooltip>
        </DropdownMenu>
    );
};

export default DraftActionsMenu;
