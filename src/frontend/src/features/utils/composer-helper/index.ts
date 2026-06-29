import { MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema } from "@/features/forms/components/message-composer";
import { BlockNoteEditor, PartialBlock } from "@blocknote/core";

// Blocks that are inserted automatically (not typed by the user) and must not
// be counted as user content when deciding whether to create a draft.
const NON_USER_CONTENT_BLOCK_TYPES = new Set(["signature", "quoted-message"]);

type SerializedBlock = {
    type?: string;
    content?: unknown;
    children?: SerializedBlock[];
};

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

    /**
     * Tell whether the serialized editor body holds genuine user content.
     *
     * An empty BlockNote document still serializes to a non-empty JSON string,
     * so a raw length check would always be truthy. This inspects the blocks
     * and ignores the auto-inserted signature and quoted-message blocks, so a
     * pristine composer (even with a default signature) is reported as empty.
     *
     * @param serializedBody - The JSON string stored in the `messageDraftBody` field
     * @returns true if the user typed text or added an inline image
     */
    static hasUserBodyContent(serializedBody?: string): boolean {
        if (!serializedBody) return false;
        let blocks: unknown;
        try {
            blocks = JSON.parse(serializedBody);
        } catch {
            return false;
        }
        if (!Array.isArray(blocks)) return false;
        return MessageComposerHelper.#blocksHaveUserContent(blocks);
    }

    static #blocksHaveUserContent(blocks: unknown[]): boolean {
        return blocks.some((rawBlock) => {
            if (!rawBlock || typeof rawBlock !== "object") return false;
            const block = rawBlock as SerializedBlock;
            if (block.type && NON_USER_CONTENT_BLOCK_TYPES.has(block.type)) return false;
            if (block.type === "image") return true;
            if (MessageComposerHelper.#inlineTextOf(block.content).trim().length > 0) return true;
            if (Array.isArray(block.children) && block.children.length > 0) {
                return MessageComposerHelper.#blocksHaveUserContent(block.children);
            }
            return false;
        });
    }

    static #inlineTextOf(content: unknown): string {
        if (!Array.isArray(content)) return "";
        return content
            .map((node): string => {
                if (node && typeof node === "object") {
                    const inline = node as { text?: unknown; content?: unknown };
                    if (typeof inline.text === "string") return inline.text;
                    // Nested inline content (e.g. link nodes wrapping text nodes).
                    if (Array.isArray(inline.content)) return MessageComposerHelper.#inlineTextOf(inline.content);
                }
                return "";
            })
            .join("");
    }
}
