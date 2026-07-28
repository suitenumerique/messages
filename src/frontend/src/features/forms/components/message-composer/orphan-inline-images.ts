import MailHelper from '@/features/utils/mail-helper';

/**
 * Structural view of the blocks this module cares about, so it can be tested
 * without building a whole BlockNote document.
 */
type BlockLike = {
    id: string;
    type: string;
    props?: unknown;
    children?: readonly BlockLike[];
};

/**
 * Reads the `url` prop of a block. Typed loosely on purpose: block props differ
 * from one block type to the next, and only the image ones carry a URL.
 */
const urlOf = (block: BlockLike): string => {
    const { props } = block;
    if (!props || typeof props !== 'object' || !('url' in props)) return '';
    const url = (props as { url?: unknown }).url;
    return typeof url === 'string' ? url : '';
};

/**
 * Collects the image blocks whose inline attachment is gone.
 *
 * Only images uploaded through our own pipeline are candidates: their URL is a
 * blob download URL, so a missing blob id means the user deleted the attachment
 * elsewhere (e.g. in the AttachmentUploader) and the block must follow.
 *
 * Every other image is left alone. A remote URL, a `data:` URI or an address
 * typed into the image toolbar never had an attachment to begin with, so its
 * absence from the list proves nothing — treating it as orphaned would delete
 * images the user legitimately added.
 *
 * @param blocks - the editor document, traversed recursively (images can live
 *   inside a column layout)
 * @param attachedBlobIds - blob ids of the inline attachments still present
 * @returns the ids of the blocks to remove
 */
export const findOrphanInlineImages = (
    blocks: readonly BlockLike[],
    attachedBlobIds: Set<string>,
): string[] => {
    const orphans: string[] = [];

    const visit = (currentBlocks: readonly BlockLike[]) => {
        for (const block of currentBlocks) {
            if (block.type === 'image') {
                const blobId = MailHelper.extractBlobId(urlOf(block));
                if (blobId && !attachedBlobIds.has(blobId)) {
                    orphans.push(block.id);
                }
            }
            if (block.children?.length) {
                visit(block.children);
            }
        }
    };

    visit(blocks);
    return orphans;
};
