import { useCallback } from "react";
import { useBlockNoteEditor } from "@blocknote/react";
import { useQueryClient } from "@tanstack/react-query";
import {
    draftPlaceholdersRetrieve,
    DraftPlaceholdersRetrieve200,
    getMailboxesMessageTemplatesRetrieveQueryOptions,
} from "@/features/api/gen";
import {
    MessageComposerBlockSchema,
    MessageComposerInlineContentSchema,
    MessageComposerStyleSchema,
    PartialMessageComposerBlockSchema,
} from "@/features/forms/components/message-composer";
import { resolveTemplateVariables } from "@/features/blocknote/utils";
import { handle } from "@/features/utils/errors";
import MailHelper from "@/features/utils/mail-helper";

type UseInsertMessageTemplateOptions = {
    mailboxId: string;
    messageId?: string;
    ensureDraft?: () => Promise<string | undefined>;
    uploadInlineImage?: (file: File) => Promise<{ url: string; blobId: string } | null>;
};

/**
 * Returns a callback inserting a message template at the editor cursor:
 * placeholders are resolved from the draft, inline images are uploaded as
 * draft attachments and the template signature is added if the editor has none.
 */
export const useInsertMessageTemplate = ({
    mailboxId,
    messageId,
    ensureDraft,
    uploadInlineImage,
}: UseInsertMessageTemplateOptions) => {
    const editor = useBlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>();
    const queryClient = useQueryClient();

    return useCallback(async (templateId: string) => {
        // The insertion point is captured before any request: the user may
        // keep typing while the template is fetched and resolved.
        const targetBlockId = editor.getTextCursorPosition().block.id;

        try {
            // Template bodies embed their images as base64: they are only
            // fetched for the template being inserted, never for the whole list.
            const [{ data: template }, resolvedMessageId] = await Promise.all([
                queryClient.fetchQuery(
                    getMailboxesMessageTemplatesRetrieveQueryOptions(mailboxId, templateId, { bodies: "raw" }),
                ),
                messageId ?? ensureDraft?.(),
            ]);
            if (!template.raw_body || !resolvedMessageId) return;

            const { data: resolvedPlaceholders } = await draftPlaceholdersRetrieve(
                resolvedMessageId,
            ) as { data: DraftPlaceholdersRetrieve200 };

            const blocks = JSON.parse(template.raw_body);
            const templateSignature = blocks.find((block: { type: string }) => block.type === "signature");
            const templateBlocks = blocks.filter((block: { type: string }) => block.type !== "signature");
            const contentBlocks = resolveTemplateVariables(templateBlocks, resolvedPlaceholders) as PartialMessageComposerBlockSchema[];

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
                        }
                    })
                );
                // Reverse order to preserve indices
                for (const index of Array.from(blocksToRemove).sort((a, b) => b - a)) {
                    contentBlocks.splice(index, 1);
                }
            }

            if (templateSignature && !editor.getBlock("signature")) {
                contentBlocks.push({
                    ...templateSignature,
                    props: {
                        ...templateSignature.props,
                        mailboxId,
                        messageId: resolvedMessageId,
                    }
                } as PartialMessageComposerBlockSchema);
            }

            // The captured block may have been deleted in the meantime.
            const targetBlock = editor.getBlock(targetBlockId) ?? editor.getTextCursorPosition().block;
            const isEmptyBlock = Array.isArray(targetBlock.content) && targetBlock.content.length === 0;
            if (isEmptyBlock) {
                editor.replaceBlocks([targetBlock], contentBlocks);
            } else {
                editor.insertBlocks(contentBlocks, targetBlock, "after");
            }
        } catch (error) {
            handle(
                new Error("Failed to insert template."),
                { extra: { error, templateId, mailboxId } }
            );
        }
    }, [editor, queryClient, mailboxId, messageId, ensureDraft, uploadInlineImage]);
};
