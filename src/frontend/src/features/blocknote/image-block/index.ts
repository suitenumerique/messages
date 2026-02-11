import { defaultBlockSpecs } from '@blocknote/core';
import MailHelper from '@/features/utils/mail-helper';

export const ALLOWED_IMAGE_MIME_TYPES = [
    'image/jpeg',
    'image/png',
    'image/gif',
    'image/webp',
];

// Override the default image block to:
// - Restrict accepted MIME types (affects file picker and drag & drop routing)
// - Fix external HTML export to fit our email needs: BlockNote's imageToExternalHTML omits
//   addDefaultPropsExternalHTML (missing alignment styles) and does not resolve
//   the natural width of images rendered in the editor.
// - Replace blob download URLs with cid: references for email embedding.
const defaultImageToExternalHTML = defaultBlockSpecs.image.implementation.toExternalHTML;

export const imageBlockSpec: typeof defaultBlockSpecs.image = {
    ...defaultBlockSpecs.image,
    implementation: {
        ...defaultBlockSpecs.image.implementation,
        meta: {
            ...defaultBlockSpecs.image.implementation.meta,
            fileBlockAccept: ALLOWED_IMAGE_MIME_TYPES,
        },
        toExternalHTML(block, editor, context) {
            const result = defaultImageToExternalHTML?.call(this, block, editor, context);
            if (!result) return result;

            // After wrapInBlockStructure, result.dom is the bn-block-content wrapper.
            // Its firstElementChild is the actual exported element (<img> or <figure>).
            const target = result.dom.firstElementChild as HTMLElement;
            if (!target) return result;

            const exportedImg = target.tagName === 'IMG'
                ? target as HTMLImageElement
                : target.querySelector('img');

            // --- Blob URL → CID ---
            // Replace blob download URLs with cid: references so email clients
            // resolve images from the MIME multipart/related structure.
            if (exportedImg) {
                exportedImg.src = MailHelper.replaceBlobUrlsWithCid(exportedImg.src);
            }

            // --- Preview width ---
            // Resolve the natural width from the editor DOM
            // when the image block has not previewWidth set so the exported <img>
            // carries a width attribute (used by email clients to size the image).
            // This avoids having to enrich block props before calling blocksToHTMLLossy.
            if (exportedImg && block.props.url && !block.props.previewWidth) {
                const imgEl = editor.domElement?.querySelector<HTMLImageElement>(
                    `[data-id="${block.id}"] img`,
                );
                if (imgEl?.complete && imgEl.naturalWidth > 0) {
                    exportedImg.width = imgEl.naturalWidth;
                }
            }

            // --- Text alignment ---
            // BlockNote sets the data-text-alignment attribute but not the
            // corresponding inline style. Apply it here so the style survives
            // the serializer stripping the bn-block-content wrapper.
            const alignment = block.props.textAlignment;
            if (alignment && alignment !== 'left') {
                if (target.tagName === 'IMG') {
                    // For standalone <img>, text-align has no effect (inline-replaced element).
                    // Use display:block with margin-based alignment instead.
                    target.style.display = 'block';
                    if (alignment === 'center') {
                        target.style.marginLeft = 'auto';
                        target.style.marginRight = 'auto';
                    } else if (alignment === 'right') {
                        target.style.marginLeft = 'auto';
                    }
                } else {
                    // For <figure> (image with caption), text-align works on inline children.
                    target.style.textAlign = alignment;
                }
            }

            return result;
        },
    },
};
