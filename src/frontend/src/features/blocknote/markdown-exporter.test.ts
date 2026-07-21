/**
 * Regression net for the composer's `textBody` pipeline: `blocksToPlainText()`
 * = `blocksToMarkdown()` (wrapper over BlockNote's `blocksToMarkdownLossy()`)
 * followed by `markdownToPlainText()`.
 *
 * This pipeline produces the email text body (see `use-base64-composer.tsx`),
 * so a silent regression breaks plain-text recipients. The `blocksToMarkdown`
 * suite pins the intermediate markdown shape per block type against the
 * production schema (`BLOCKNOTE_SCHEMA`) — the exact conventions (escapes,
 * autolinks, fences…) the `markdownToPlainText` regexes assume in input — so
 * a BlockNote upgrade that changes that shape is caught at CI time rather
 * than surfacing as mangled plain-text output.
 *
 * Note: snapshots are deliberately structural (`toContain`) rather than full
 * inline snapshots to absorb cosmetic differences (bullet marker, etc.) across
 * BlockNote patch versions. The only inline snapshot is the empty-document
 * case: BlockNote >=0.51 emits a trailing "\n" there, which the wrapper trims
 * back to '' — that contract is what this case guards.
 */
import { BlockNoteEditor } from '@blocknote/core';
import type { PartialBlock } from '@blocknote/core';
import { BLOCKNOTE_SCHEMA } from '@/features/forms/components/message-composer';
import { blocksToMarkdown, blocksToPlainText, markdownToPlainText } from './markdown-exporter';

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
  return blocksToMarkdown(editor, blocks);
}

describe('blocksToMarkdown', () => {
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

describe('markdownToPlainText', () => {
  it('returns an empty string for empty input', () => {
    expect(markdownToPlainText('')).toBe('');
  });

  it('leaves plain prose untouched', () => {
    expect(markdownToPlainText('Hello there, world.')).toBe('Hello there, world.');
  });

  it('strips emphasis markers but keeps their text', () => {
    expect(markdownToPlainText('This is **bold**, *italic*, ~~struck~~ and `code`.'))
      .toBe('This is bold, italic, struck and code.');
  });

  it('preserves word-internal underscores (snake_case)', () => {
    expect(markdownToPlainText('use snake_case or _italic_')).toBe('use snake_case or italic');
  });

  it('converts links to "label (url)" keeping the target', () => {
    expect(markdownToPlainText('See [the docs](https://example.com/a).'))
      .toBe('See the docs (https://example.com/a).');
  });

  it('converts images to "alt (url)"', () => {
    expect(markdownToPlainText('![a chart](https://example.com/i.png)'))
      .toBe('a chart (https://example.com/i.png)');
  });

  it('keeps the url alone when a link has no label', () => {
    expect(markdownToPlainText('[](https://example.com/a)')).toBe('https://example.com/a');
  });

  it('strips heading hashes but keeps the title text', () => {
    expect(markdownToPlainText('# Title\nBody')).toBe('Title\nBody');
  });

  it('removes code fence lines but keeps the code', () => {
    expect(markdownToPlainText('before\n```ts\nconsole.log(1)\n```\nafter'))
      .toBe('before\nconsole.log(1)\nafter');
  });

  it('keeps angle-bracket code inside fenced blocks verbatim', () => {
    expect(markdownToPlainText('```html\n<div>Hello</div>\n```'))
      .toBe('<div>Hello</div>');
  });

  it('keeps markdown-looking code inside fenced blocks verbatim', () => {
    expect(markdownToPlainText('```\nconst re = /\\d+/; // **not bold** [not](a-link)\n```'))
      .toBe('const re = /\\d+/; // **not bold** [not](a-link)');
  });

  it('keeps the body of an unclosed fence verbatim', () => {
    expect(markdownToPlainText('before **bold**\n```\n<p>raw</p>'))
      .toBe('before bold\n<p>raw</p>');
  });

  it('still strips syntax in prose surrounding a fenced block', () => {
    expect(markdownToPlainText('**intro**\n```\n<b>code</b>\n```\n<u>outro</u>'))
      .toBe('intro\n<b>code</b>\noutro');
  });

  it('preserves list markers and blockquotes (readable as plain text)', () => {
    const text = '- item one\n- item two\n> quoted line';
    expect(markdownToPlainText(text)).toBe(text);
  });

  it('unwraps autolinks to the bare URL', () => {
    expect(markdownToPlainText('see <https://example.com/a> now'))
      .toBe('see https://example.com/a now');
  });

  it('strips raw HTML tags the serializer falls back to', () => {
    expect(markdownToPlainText('some <u>underlined</u> words'))
      .toBe('some underlined words');
  });

  it('strips tags reassembled by the removal of a nested tag', () => {
    expect(markdownToPlainText('safe <scr<b>ipt>alert(1)</scr</b>ipt> text'))
      .toBe('safe alert(1) text');
  });

  it('converts <br> tags to newlines', () => {
    expect(markdownToPlainText('line one<br/>line two')).toBe('line one\nline two');
  });

  it('keeps user-typed angle brackets (escaped by the serializer)', () => {
    expect(markdownToPlainText('use the \\<div\\> element')).toBe('use the <div> element');
  });

  it('unwraps email autolinks to the bare address', () => {
    expect(markdownToPlainText('Courriel: <contact@brigny.fr>'))
      .toBe('Courriel: contact@brigny.fr');
  });

  it('drops hard-break backslashes but keeps the line break', () => {
    expect(markdownToPlainText('tableau.\\\n\\\nCordialement'))
      .toBe('tableau.\n\nCordialement');
  });

  // The serializer escapes markdown punctuation the user typed literally.
  // Stripping the marker without its backslash used to leave the backslash
  // stranded (`2 \* 3` -> `2 \ 3`) in the text/plain part recipients read.
  it('keeps a user-typed asterisk', () => {
    expect(markdownToPlainText('2 \\* 3 = 6')).toBe('2 * 3 = 6');
  });

  it('keeps a user-typed underscore at a word edge', () => {
    expect(markdownToPlainText('the \\_private suffix')).toBe('the _private suffix');
  });

  it('keeps a user-typed backtick', () => {
    expect(markdownToPlainText('a \\` tick')).toBe('a ` tick');
  });

  it('keeps user-typed tildes', () => {
    expect(markdownToPlainText('cost \\~\\~ approx')).toBe('cost ~~ approx');
  });

  it('still strips real emphasis next to an escaped marker', () => {
    expect(markdownToPlainText('\\* **bold** \\*')).toBe('* bold *');
  });

  // A link target is a literal: emphasis stripping must not reach into it.
  it('keeps an underscore at the end of a url segment', () => {
    expect(markdownToPlainText('[wiki](https://example.com/Foo_/bar)'))
      .toBe('wiki (https://example.com/Foo_/bar)');
  });

  it('keeps an asterisk inside a url', () => {
    expect(markdownToPlainText('[q](https://example.com/s?q=a*b)'))
      .toBe('q (https://example.com/s?q=a*b)');
  });

  it('keeps an underscore at the end of an autolinked url', () => {
    expect(markdownToPlainText('see <https://example.com/Foo_/bar> now'))
      .toBe('see https://example.com/Foo_/bar now');
  });

  // The URL placeholders are NUL-delimited on the assumption that a BlockNote
  // export carries no NUL. Should one slip through, it must pass through as
  // text rather than index past the held targets and throw.
  it('leaves a stray placeholder sentinel alone instead of throwing', () => {
    expect(markdownToPlainText('before \u00007\u0000 after'))
      .toBe('before \u00007\u0000 after');
  });
});

describe('blocksToPlainText', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async function toPlainText(blocks: PartialBlock<any, any, any>[]): Promise<string> {
    const editor = createHeadlessEditor();
    return blocksToPlainText(editor, blocks);
  }

  it('returns an empty string for an empty document', async () => {
    expect(await toPlainText([])).toBe('');
  });

  it('exports a styled document as prose, with link targets preserved', async () => {
    const text = await toPlainText([
      {
        type: 'paragraph',
        content: [
          { type: 'text', text: 'Some ', styles: {} },
          { type: 'text', text: 'bold', styles: { bold: true } },
          { type: 'text', text: ' words and ', styles: {} },
          { type: 'link', href: 'https://example.com', content: 'a link' },
        ],
      },
    ]);
    expect(text).toContain('Some bold words and a link (https://example.com)');
    expect(text).not.toContain('**');
    expect(text).not.toContain('[a link]');
  });
});
