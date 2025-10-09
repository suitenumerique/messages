import { MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema } from "@/features/forms/components/message-composer";
import { BlockNoteEditor, PartialBlock } from "@blocknote/core";

export class MessageComposerHelper {

    /**
     * Insert the signature block at the end of the document or before the quote block if it exists
     *
     * @param editor - The editor instance
     * @param signatureBlock - The signature block to insert
     * @returns The inserted block
     */
    static insertSignatureBlock(editor: BlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>, signatureBlock: PartialBlock<Pick<MessageComposerBlockSchema, 'signature'>, MessageComposerInlineContentSchema, MessageComposerStyleSchema>) {
        let insertedBlockIdentier = editor.document[editor.document.length - 1].id;
        let placement: "before" | "after" = "after";

        editor.forEachBlock((block) => {
            if (block.type === 'quoted-message') {
                insertedBlockIdentier = block.id;
                placement = "before";
                return true;
            }
            return false;
        }, true);

        return editor.insertBlocks([signatureBlock], insertedBlockIdentier, placement);
    }
}
