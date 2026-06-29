import { describe, it, expect } from 'vitest';
import { MessageComposerHelper } from './index';

const serialize = (blocks: unknown) => JSON.stringify(blocks);

const paragraph = (content: unknown[]) => ({
    id: 'p1',
    type: 'paragraph',
    props: {},
    content,
    children: [],
});

const text = (value: string) => ({ type: 'text', text: value, styles: {} });

describe('MessageComposerHelper.hasUserBodyContent', () => {
    it('returns false for undefined or empty input', () => {
        expect(MessageComposerHelper.hasUserBodyContent(undefined)).toBe(false);
        expect(MessageComposerHelper.hasUserBodyContent('')).toBe(false);
    });

    it('returns false for invalid JSON', () => {
        expect(MessageComposerHelper.hasUserBodyContent('not-json')).toBe(false);
    });

    it('ignores non-object entries without crashing', () => {
        expect(MessageComposerHelper.hasUserBodyContent(serialize([null, 'foo', 42]))).toBe(false);
    });

    it('returns false for a pristine empty paragraph', () => {
        expect(MessageComposerHelper.hasUserBodyContent(serialize([paragraph([])]))).toBe(false);
    });

    it('returns false when the only text is whitespace', () => {
        expect(MessageComposerHelper.hasUserBodyContent(serialize([paragraph([text('   ')])]))).toBe(false);
    });

    it('returns true when a paragraph holds typed text', () => {
        expect(MessageComposerHelper.hasUserBodyContent(serialize([paragraph([text('Hello')])]))).toBe(true);
    });

    it('ignores the auto-inserted signature block', () => {
        const blocks = [
            paragraph([]),
            { id: 's', type: 'signature', props: { templateId: 'sig-1' }, content: undefined, children: [] },
        ];
        expect(MessageComposerHelper.hasUserBodyContent(serialize(blocks))).toBe(false);
    });

    it('ignores the auto-inserted quoted-message block', () => {
        const blocks = [
            paragraph([]),
            { id: 'q', type: 'quoted-message', props: {}, content: undefined, children: [] },
        ];
        expect(MessageComposerHelper.hasUserBodyContent(serialize(blocks))).toBe(false);
    });

    it('counts an inline image as content', () => {
        const blocks = [{ id: 'i', type: 'image', props: { url: 'blob:x' }, children: [] }];
        expect(MessageComposerHelper.hasUserBodyContent(serialize(blocks))).toBe(true);
    });

    it('detects text nested inside link inline content', () => {
        const link = { type: 'link', href: 'https://x', content: [text('click')] };
        expect(MessageComposerHelper.hasUserBodyContent(serialize([paragraph([link])]))).toBe(true);
    });

    it('detects text inside nested children blocks', () => {
        const blocks = [
            {
                id: 'list',
                type: 'bulletListItem',
                props: {},
                content: [],
                children: [paragraph([text('nested')])],
            },
        ];
        expect(MessageComposerHelper.hasUserBodyContent(serialize(blocks))).toBe(true);
    });
});
