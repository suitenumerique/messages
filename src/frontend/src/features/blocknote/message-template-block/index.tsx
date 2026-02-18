import { useBlockNoteEditor, useComponentsContext, useEditorState } from "@blocknote/react";
import { useTranslation } from "react-i18next";
import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Modal, ModalSize } from "@gouvfr-lasuite/cunningham-react";
import { MessageTemplateTypeChoices, ReadMessageTemplate, useMailboxesMessageTemplatesAvailableList, draftPlaceholdersRetrieve, DraftPlaceholdersRetrieve200 } from "@/features/api/gen";
import { MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema, PartialMessageComposerBlockSchema } from "@/features/forms/components/message-composer";
import { useModal } from "@gouvfr-lasuite/cunningham-react";
import { handle } from "@/features/utils/errors";
import MailHelper from "@/features/utils/mail-helper";
import { resolveTemplateVariables } from "@/features/blocknote/utils";

type MessageTemplateSelectorProps = {
    mailboxId: string;
    messageId?: string;
    ensureDraft?: () => Promise<string | undefined>;
    uploadInlineImage?: (file: File) => Promise<{ url: string; blobId: string } | null>;
}

/**
 * A BlockNote toolbar selector which allows the user to select a message template
 * from all active templates for a given mailbox.
 */
export const MessageTemplateSelector = ({ mailboxId, messageId, ensureDraft, uploadInlineImage }: MessageTemplateSelectorProps) => {
    const { t } = useTranslation();
    const editor = useBlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>();
    const Components = useComponentsContext()!;
    const modal = useModal();

    const hasInlineContent = useEditorState({
        editor,
        selector: ({ editor }) => {
            const selectedBlocks = editor.getSelection()?.blocks || [
                editor.getTextCursorPosition().block,
            ];
            return selectedBlocks.some((block) => block.content !== undefined);
        },
    });

    const { data: { data: templates = [] } = {}, isLoading } = useMailboxesMessageTemplatesAvailableList(
        mailboxId,
        {
            type: MessageTemplateTypeChoices.message,
            bodies: "raw",
        },
        {
            query: { enabled: hasInlineContent }
        }
    );

    const handleSelect = async (template: ReadMessageTemplate) => {
        if (!template.raw_body || !template.id) return;

        const resolvedMessageId = messageId ?? await ensureDraft?.();
        if (!resolvedMessageId) return;

        try {
            // Resolve placeholder values from the draft context
            const { data: resolvedPlaceholders } = await draftPlaceholdersRetrieve(
                resolvedMessageId,
            ) as { data: DraftPlaceholdersRetrieve200 };

            // Parse raw blocks and resolve template variables client-side
            const blocks = JSON.parse(template.raw_body);
            const templateSignature = blocks.find((block: { type: string }) => block.type === "signature");
            const templateBlocks = blocks.filter((block: { type: string }) => block.type !== "signature");
            const contentBlocks = resolveTemplateVariables(templateBlocks, resolvedPlaceholders) as PartialMessageComposerBlockSchema[];

            // Convert base64 images to blobs via upload
            if (uploadInlineImage) {
                const blocksToRemove = new Set<number>();
                await Promise.all(
                    contentBlocks.map(async (block, index) => {
                        if (block.type !== 'image' || !block.props?.url?.startsWith('data:')) return;

                        const file = MailHelper.dataUrlToFile(block.props.url, `template-image-${index}.png`);
                        if (!file) {
                            blocksToRemove.add(index);
                            return;
                        }
                        try {
                            const result = await uploadInlineImage(file);
                            if (result) {
                                contentBlocks[index] = {
                                    ...block,
                                    props: { ...block.props, url: result.url },
                                } as PartialMessageComposerBlockSchema;
                            } else {
                                blocksToRemove.add(index);
                            }
                        } catch (error) {
                            handle(
                                new Error("Failed to upload inline image."),
                                { extra: { error, block, index } }
                            );
                            blocksToRemove.add(index);
                            return;
                        }
                    })
                );
                // Remove failed blocks (reverse order to preserve indices)
                for (const index of Array.from(blocksToRemove).sort((a, b) => b - a)) {
                    contentBlocks.splice(index, 1);
                }
            }

            // Check if there's already a signature in the editor
            const editorSignature = editor.getBlock("signature");

            // Add signature if needed
            if (templateSignature && !editorSignature) {
                contentBlocks.push({
                    ...templateSignature,
                    props: {
                        ...templateSignature.props,
                        mailboxId,
                        messageId: resolvedMessageId,
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

    if (!hasInlineContent) return null;

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
        return null;
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
