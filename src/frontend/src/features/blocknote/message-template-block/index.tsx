import { useBlockNoteEditor, useComponentsContext, useEditorState } from "@blocknote/react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { IconSize, IconType, Spinner } from "@gouvfr-lasuite/ui-components";
import { Modal, ModalSize } from "@gouvfr-lasuite/ui-components";
import { MobileToolbarButton } from "@/features/blocknote/mobile-toolbar/buttons";
import { useMobileToolbarChildDrawer } from "@/features/blocknote/mobile-toolbar/drawer-context";
import { Drawer } from "@/features/ui/components/drawer";
import { MessageTemplateTypeChoices, useMailboxesMessageTemplatesAvailableList } from "@/features/api/gen";
import { MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema } from "@/features/forms/components/message-composer";
import { useModal } from "@gouvfr-lasuite/ui-components";
import { Icon } from "@/features/ui/components/icon";
import { MessageTemplatePicker } from "./message-template-picker";
import { useInsertMessageTemplate } from "./use-insert-message-template";

const TEMPLATES_DRAWER_ID = "message-templates";

type MessageTemplateSelectorProps = {
    mailboxId: string;
    messageId?: string;
    ensureDraft?: () => Promise<string | undefined>;
    uploadInlineImage?: (file: File) => Promise<{ url: string; blobId: string } | null>;
}

/**
 * A BlockNote toolbar selector which allows the user to search and insert a
 * message template among all active templates for a given mailbox.
 */
export const MessageTemplateSelector = ({ mailboxId, messageId, ensureDraft, uploadInlineImage }: MessageTemplateSelectorProps) => {
    const { t } = useTranslation();
    const editor = useBlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>();
    const Components = useComponentsContext()!;
    const modal = useModal();
    // Non-null when rendered inside the mobile toolbar: templates are then
    // picked from a bottom drawer instead of the desktop modal.
    const mobileDrawer = useMobileToolbarChildDrawer(TEMPLATES_DRAWER_ID);
    const insertTemplate = useInsertMessageTemplate({ mailboxId, messageId, ensureDraft, uploadInlineImage });

    const hasInlineContent = useEditorState({
        editor,
        selector: ({ editor }) => {
            const selectedBlocks = editor.getSelection()?.blocks || [
                editor.getTextCursorPosition().block,
            ];
            return selectedBlocks.some((block) => block.content !== undefined);
        },
    });

    // No body here: the list only needs the names, the body of a template is
    // fetched once it is picked.
    const { data: { data: templates = [] } = {}, isLoading } = useMailboxesMessageTemplatesAvailableList(
        mailboxId,
        { type: MessageTemplateTypeChoices.message },
        { query: { enabled: hasInlineContent } }
    );

    if (!hasInlineContent) return null;

    if (isLoading) {
        if (mobileDrawer) {
            return (
                <MobileToolbarButton
                    icon={<Spinner size="sm" />}
                    label={t("Loading templates...")}
                    isDisabled
                    onClick={() => {}}
                />
            );
        }
        return (
            <Components.FormattingToolbar.Button
                icon={<Spinner size="sm" />}
                isDisabled={true}
                label={t("Loading templates...")}
                mainTooltip={t("Loading templates...")}
            />
        );
    }

    if (templates.length === 0) {
        return null;
    }

    if (mobileDrawer) {
        return (
            <>
                <MobileToolbarButton
                    icon={<Icon name="description" type={IconType.OUTLINED} size={IconSize.MEDIUM} />}
                    label={t("Insert template")}
                    isActive={mobileDrawer.openId === TEMPLATES_DRAWER_ID}
                    onClick={() => mobileDrawer.open(TEMPLATES_DRAWER_ID)}
                />
                {mobileDrawer.openId === TEMPLATES_DRAWER_ID &&
                    mobileDrawer.slot &&
                    createPortal(
                        <Drawer
                            title={t("Insert template")}
                            onClose={mobileDrawer.close}
                        >
                            <MessageTemplatePicker
                                mailboxId={mailboxId}
                                messageId={messageId}
                                templates={templates}
                                // Focusing the search would pop the virtual
                                // keyboard over the list users came to browse.
                                autoFocus={false}
                                onSelect={(templateId) => {
                                    // Close first: the keyboard comes
                                    // back while the async insertion
                                    // (placeholders, images) settles.
                                    mobileDrawer.close();
                                    void insertTemplate(templateId);
                                }}
                            />
                        </Drawer>,
                        mobileDrawer.slot,
                    )}
            </>
        );
    }

    return (
        <>
            <Components.FormattingToolbar.Button
                icon={<Icon name="description" type={IconType.OUTLINED} size={IconSize.SMALL} />}
                label={t("Insert template")}
                mainTooltip={t("Insert template")}
                onClick={modal.open}
            />
            <Modal
                isOpen={modal.isOpen}
                onClose={modal.close}
                title={t("Insert template")}
                size={ModalSize.LARGE}
            >
                {modal.isOpen && (
                    <MessageTemplatePicker
                        mailboxId={mailboxId}
                        messageId={messageId}
                        templates={templates}
                        showPreview
                        onSelect={(templateId) => {
                            modal.close();
                            void insertTemplate(templateId);
                        }}
                    />
                )}
            </Modal>
        </>
    );
};
