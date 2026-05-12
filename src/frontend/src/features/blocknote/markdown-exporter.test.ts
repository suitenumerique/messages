/**
 * Regression net for `BlockNoteEditor.blocksToMarkdownLossy()`.
 *
 * This serializer is a BlockNote built-in (no source in this repo) used in
 * `message-composer/index.tsx` to produce the email text body. A silent
 * regression here breaks plain-text recipients. These tests pin a contract
 * per block type against the production schema (`BLOCKNOTE_SCHEMA`), so a
 * BlockNote upgrade that changes the markdown shape is caught at CI time.
 *
 * Note: snapshots are deliberately structural (`toContain`) rather than full
 * inline snapshots to absorb cosmetic differences (trailing newlines, bullet
 * marker, etc.) across BlockNote patch versions. The only inline snapshot is
 * the empty-document case, which should never produce noise.
 */
import { BlockNoteEditor } from '@blocknote/core';
import type { PartialBlock } from '@blocknote/core';
import { BLOCKNOTE_SCHEMA } from '@/features/forms/components/message-composer';

// jsdom 27 ships without matchMedia/ResizeObserver/IntersectionObserver, which
// BlockNote/TipTap probe when an editor is instantiated. The schema import
// above is safe at module load (no DOM access), so we only need to stub before
// the first `createHeadlessEditor()` call.
beforeAll(() => {
  if (typeof window === 'undefined') return;

  if (!window.matchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: () => ({
        matches: false,
        media: '',
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  }

  class NoopObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  if (!(globalThis as any).ResizeObserver) (globalThis as any).ResizeObserver = NoopObserver;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  if (!(globalThis as any).IntersectionObserver) (globalThis as any).IntersectionObserver = NoopObserver;
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type EditorType = BlockNoteEditor<any, any, any>;

function createHeadlessEditor(): EditorType {
  return BlockNoteEditor.create({ schema: BLOCKNOTE_SCHEMA }) as EditorType;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function toMarkdown(blocks: PartialBlock<any, any, any>[]): Promise<string> {
  const editor = createHeadlessEditor();
  return editor.blocksToMarkdownLossy(blocks);
}

describe('blocksToMarkdownLossy', () => {
  it('returns an empty string for an empty document', async () => {
    const md = await toMarkdown([]);
    expect(md).toBe('');
  });

  it('serializes a plain paragraph', async () => {
    const md = await toMarkdown([
      { type: 'paragraph', content: 'Hello world' },
    ]);
    expect(md.trim()).toBe('Hello world');
  });

  it('serializes headings level 1-3', async () => {
    const md = await toMarkdown([
      { type: 'heading', props: { level: 1 }, content: 'H1' },
      { type: 'heading', props: { level: 2 }, content: 'H2' },
      { type: 'heading', props: { level: 3 }, content: 'H3' },
    ]);
    expect(md).toContain('# H1');
    expect(md).toContain('## H2');
    expect(md).toContain('### H3');
  });

  it('serializes bold, italic and strikethrough inline marks', async () => {
    const md = await toMarkdown([
      {
        type: 'paragraph',
        content: [
          { type: 'text', text: 'bold', styles: { bold: true } },
          { type: 'text', text: ' ', styles: {} },
          { type: 'text', text: 'italic', styles: { italic: true } },
          { type: 'text', text: ' ', styles: {} },
          { type: 'text', text: 'struck', styles: { strike: true } },
        ],
      },
    ]);
    expect(md).toContain('**bold**');
    expect(md).toMatch(/[*_]italic[*_]/);
    expect(md).toContain('~~struck~~');
  });

  it('serializes a link with text', async () => {
    const md = await toMarkdown([
      {
        type: 'paragraph',
        content: [
          {
            type: 'link',
            href: 'https://example.com',
            content: 'Click here',
          },
        ],
      },
    ]);
    expect(md).toContain('[Click here](https://example.com)');
  });

  it('serializes a bullet list', async () => {
    const md = await toMarkdown([
      { type: 'bulletListItem', content: 'Item A' },
      { type: 'bulletListItem', content: 'Item B' },
    ]);
    expect(md).toMatch(/[-*] Item A/);
    expect(md).toMatch(/[-*] Item B/);
  });

  it('serializes a numbered list', async () => {
    const md = await toMarkdown([
      { type: 'numberedListItem', content: 'First' },
      { type: 'numberedListItem', content: 'Second' },
    ]);
    expect(md).toContain('1. First');
    expect(md).toContain('2. Second');
  });

  it('serializes nested bullet list children with indentation', async () => {
    const md = await toMarkdown([
      {
        type: 'bulletListItem',
        content: 'Parent',
        children: [{ type: 'bulletListItem', content: 'Child' }],
      },
    ]);
    expect(md).toMatch(/[-*] Parent/);
    expect(md).toMatch(/\s+[-*] Child/);
  });

  it('serializes a code block as a fenced block', async () => {
    const md = await toMarkdown([
      { type: 'codeBlock', content: 'console.log(1)' },
    ]);
    expect(md).toContain('```');
    expect(md).toContain('console.log(1)');
  });

  it('serializes a quote', async () => {
    const md = await toMarkdown([
      { type: 'quote', content: 'A wise thought' },
    ]);
    expect(md).toContain('> A wise thought');
  });

  it('serializes an image as ![alt](url)', async () => {
    const md = await toMarkdown([
      {
        type: 'image',
        props: {
          url: 'https://example.com/photo.jpg',
          name: 'photo.jpg',
        },
      },
    ]);
    expect(md).toContain('https://example.com/photo.jpg');
    expect(md).toContain('![');
  });

  it('omits the signature block from markdown output', async () => {
    const md = await toMarkdown([
      { type: 'paragraph', content: 'Above signature' },
      {
        type: 'signature',
        props: {
          templateId: 'tpl-uuid',
          mailboxId: 'mbx-uuid',
          messageId: 'msg-uuid',
        },
      },
    ]);
    expect(md).toContain('Above signature');
    expect(md).not.toContain('tpl-uuid');
    expect(md).not.toContain('mbx-uuid');
  });

  it('omits the quoted-message block from markdown output', async () => {
    const md = await toMarkdown([
      { type: 'paragraph', content: 'Reply text' },
      {
        type: 'quoted-message',
        props: {
          subject: 'Confidential subject',
          sender: 'alice@example.com',
        },
      },
    ]);
    expect(md).toContain('Reply text');
    expect(md).not.toContain('Confidential subject');
    expect(md).not.toContain('alice@example.com');
  });

  it('preserves text around a styled span across hard breaks', async () => {
    const md = await toMarkdown([
      {
        type: 'paragraph',
        content: [
          { type: 'text', text: 'Line one\nLine two', styles: {} },
        ],
      },
    ]);
    expect(md).toContain('Line one');
    expect(md).toContain('Line two');
  });
});
