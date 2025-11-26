import { useBlockNoteEditor, useComponentsContext } from "@blocknote/react";
import { useTranslation } from "react-i18next";
import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Modal, ModalSize } from "@openfun/cunningham-react";
import { MessageTemplateTypeChoices, ReadOnlyMessageTemplate, useMailboxesMessageTemplatesAvailableList, mailboxesMessageTemplatesRenderRetrieve, MailboxesMessageTemplatesRenderRetrieveParams } from "@/features/api/gen";
import { MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema, PartialMessageComposerBlockSchema } from "@/features/forms/components/message-composer";
import { useModal } from "@openfun/cunningham-react";
import { handle } from "@/features/utils/errors";

type MessageTemplateSelectorProps = {
    mailboxId: string;
    context?: Record<string, string>;
}

/**
 * A BlockNote toolbar selector which allows the user to select a message template
 * from all active templates for a given mailbox.
 */
export const MessageTemplateSelector = ({ mailboxId, context = {} }: MessageTemplateSelectorProps) => {
    const { t } = useTranslation();
    const editor = useBlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>();
    const Components = useComponentsContext()!;
    const modal = useModal();

    const { data: { data: templates = [] } = {}, isLoading } = useMailboxesMessageTemplatesAvailableList(
        mailboxId,
        {
            type: MessageTemplateTypeChoices.message,
        },
        {}
    );

    const handleSelect = async (template: ReadOnlyMessageTemplate) => {
        if (!template.raw_body || !template.id) return;

        try {
            // Get rendered template content (allows to use placeholders)
            const { data: renderedTemplate } = await mailboxesMessageTemplatesRenderRetrieve(
                mailboxId,
                template.id,
                context as MailboxesMessageTemplatesRenderRetrieveParams,
            );
            if (!renderedTemplate?.html_body) {
                handle(new Error("Failed to render template."), { extra: { templateId: template.id, mailboxId: mailboxId } });
                return;
            }

            // Parse template blocks for signature
            const blocks = JSON.parse(template.raw_body);
            const templateSignature = blocks.find((block: { type: string }) => block.type === "signature");

            // Convert HTML to blocks using BlockNote's built-in parser
            const contentBlocks = await editor.tryParseHTMLToBlocks(renderedTemplate.html_body) as PartialMessageComposerBlockSchema[];

            // Check if there's already a signature in the editor
            const editorSignature = editor.getBlock("signature");

            // Add signature if needed
            if (templateSignature && !editorSignature) {
                contentBlocks.push({
                    ...templateSignature,
                    props: {
                        ...templateSignature.props,
                        mailboxId
                    }
                } as PartialMessageComposerBlockSchema);
            }

            // Insert blocks at cursor position
            const currentBlock = editor.getTextCursorPosition().block;

            // if the current block is empty, replace it with the template blocks
            const currentBlockContent = editor.getBlock(currentBlock)?.content;
            if (currentBlock && (!currentBlockContent || (Array.isArray(currentBlockContent) && currentBlockContent.length === 0))) {
                editor.replaceBlocks([currentBlock], contentBlocks);
            } else {
                // Otherwise we insert after
                editor.insertBlocks(contentBlocks, currentBlock, "after");
            }
            modal.close();
        } catch (error) {
            handle(
                new Error("Failed to insert template."),
                { extra: { error, templateId: template.id, mailboxId: mailboxId } }
            );
        }
    };

    if (isLoading) {
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
        return (
            <Components.FormattingToolbar.Button
                icon={<Icon name="description" size={IconSize.SMALL} />}
                isDisabled={true}
                label={t("No templates available")}
                mainTooltip={t("No templates available")}
            />
        );
    }

    return (
        <>
            <Components.FormattingToolbar.Button
                icon={<Icon name="description" size={IconSize.SMALL} />}
                label={t("Insert template")}
                mainTooltip={t("Insert template")}
                onClick={modal.open}
            />
            <Modal
                isOpen={modal.isOpen}
                onClose={modal.close}
                title={t("Insert template")}
                size={ModalSize.SMALL}
            >
                <div className="template-list">
                    {templates.map((template) => (
                        <button
                            type="button"
                            key={template.id}
                            className="template-item"
                            onClick={() => handleSelect(template)}
                        >
                            <div className="template-icon">
                                <Icon name="description" size={IconSize.MEDIUM} />
                            </div>
                            <div className="template-content">
                                <div className="template-name">
                                    {template.name}
                                </div>
                            </div>
                        </button>
                    ))}
                </div>
            </Modal>
        </>
    );
};
