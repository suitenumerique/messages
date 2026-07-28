import { findOrphanInlineImages } from './orphan-inline-images';

const BLOB_ID = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890';
const OTHER_BLOB_ID = 'ffffffff-0000-1111-2222-333333333333';

const blobUrl = (id: string) => `/api/v1.0/blob/${id}/download/`;

const image = (id: string, url: string) => ({ id, type: 'image', props: { url } });

describe('findOrphanInlineImages', () => {
    it('removes an uploaded image whose attachment is gone', () => {
        const blocks = [image('img-1', blobUrl(BLOB_ID))];

        expect(findOrphanInlineImages(blocks, new Set())).toEqual(['img-1']);
    });

    it('keeps an uploaded image whose attachment is still there', () => {
        const blocks = [image('img-1', blobUrl(BLOB_ID))];

        expect(findOrphanInlineImages(blocks, new Set([BLOB_ID]))).toEqual([]);
    });

    it('keeps an uploaded image when another attachment was removed', () => {
        const blocks = [image('img-1', blobUrl(BLOB_ID))];

        expect(findOrphanInlineImages(blocks, new Set([BLOB_ID, OTHER_BLOB_ID]))).toEqual([]);
    });

    // The regression that emptied drafts: an image the user pasted or typed a URL
    // for never had an attachment, so it must survive an empty attachment list.
    it.each([
        ['a remote URL', 'https://example.com/photo.png'],
        ['a data URI', 'data:image/png;base64,iVBORw0KGgo='],
        ['an object URL', 'blob:http://localhost:8900/8f3b-4a1c'],
        ['an empty URL', ''],
    ])('keeps an image with %s even with no attachments', (_label, url) => {
        expect(findOrphanInlineImages([image('img-1', url)], new Set())).toEqual([]);
    });

    it('keeps an image block with no props at all', () => {
        expect(findOrphanInlineImages([{ id: 'img-1', type: 'image' }], new Set())).toEqual([]);
    });

    it('ignores blocks that are not images', () => {
        const blocks = [
            { id: 'p-1', type: 'paragraph', props: { url: blobUrl(BLOB_ID) } },
        ];

        expect(findOrphanInlineImages(blocks, new Set())).toEqual([]);
    });

    it('reaches images nested in a column layout', () => {
        const blocks = [
            {
                id: 'cols',
                type: 'columnList',
                children: [
                    {
                        id: 'col-1',
                        type: 'column',
                        children: [
                            image('nested-orphan', blobUrl(BLOB_ID)),
                            image('nested-remote', 'https://example.com/photo.png'),
                        ],
                    },
                ],
            },
        ];

        expect(findOrphanInlineImages(blocks, new Set())).toEqual(['nested-orphan']);
    });

    it('returns every orphan of a mixed document', () => {
        const blocks = [
            { id: 'p-1', type: 'paragraph' },
            image('kept-attached', blobUrl(BLOB_ID)),
            image('kept-remote', 'https://example.com/photo.png'),
            image('orphan', blobUrl(OTHER_BLOB_ID)),
        ];

        expect(findOrphanInlineImages(blocks, new Set([BLOB_ID]))).toEqual(['orphan']);
    });
});
