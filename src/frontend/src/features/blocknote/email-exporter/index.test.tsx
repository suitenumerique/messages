import { vi } from 'vitest';
import type { Block, InlineContent, StyledText } from '@blocknote/core';
import { EmailExporter } from './index';

vi.mock('@/features/utils/mail-helper', () => ({
  default: {
    replaceBlobUrlsWithCid: (url: string) => url,
  },
}));

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyBlock = Block<any, any, any>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyStyledText = StyledText<any>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyInlineContent = InlineContent<any, any>;

// ---------------------------------------------------------------------------
// Block factories
// ---------------------------------------------------------------------------

function styledText(
  text: string,
  styles: Record<string, unknown> = {},
): AnyStyledText {
  return { type: 'text', text, styles } as AnyStyledText;
}

function link(href: string, text: string): AnyInlineContent {
  return {
    type: 'link',
    href,
    content: [styledText(text)],
  } as unknown as AnyInlineContent;
}

function paragraph(
  content: AnyInlineContent[] | string,
  props: Record<string, unknown> = {},
  children: AnyBlock[] = [],
): AnyBlock {
  const inlineContent =
    typeof content === 'string' ? [styledText(content)] : content;
  return {
    id: crypto.randomUUID(),
    type: 'paragraph',
    props: { textAlignment: 'left', textColor: 'default', backgroundColor: 'default', ...props },
    content: inlineContent,
    children,
  } as AnyBlock;
}

function heading(
  content: AnyInlineContent[] | string,
  level: number,
  props: Record<string, unknown> = {},
  children: AnyBlock[] = [],
): AnyBlock {
  const inlineContent =
    typeof content === 'string' ? [styledText(content)] : content;
  return {
    id: crypto.randomUUID(),
    type: 'heading',
    props: { level, textAlignment: 'left', textColor: 'default', backgroundColor: 'default', ...props },
    content: inlineContent,
    children,
  } as AnyBlock;
}

function image(
  url: string,
  props: Record<string, unknown> = {},
): AnyBlock {
  return {
    id: crypto.randomUUID(),
    type: 'image',
    props: { url, caption: '', name: '', textAlignment: 'left', ...props },
    content: undefined,
    children: [],
  } as unknown as AnyBlock;
}

function bulletListItem(
  content: AnyInlineContent[] | string,
  props: Record<string, unknown> = {},
  children: AnyBlock[] = [],
): AnyBlock {
  const inlineContent =
    typeof content === 'string' ? [styledText(content)] : content;
  return {
    id: crypto.randomUUID(),
    type: 'bulletListItem',
    props: { textAlignment: 'left', textColor: 'default', backgroundColor: 'default', ...props },
    content: inlineContent,
    children,
  } as AnyBlock;
}

function numberedListItem(
  content: AnyInlineContent[] | string,
  props: Record<string, unknown> = {},
  children: AnyBlock[] = [],
): AnyBlock {
  const inlineContent =
    typeof content === 'string' ? [styledText(content)] : content;
  return {
    id: crypto.randomUUID(),
    type: 'numberedListItem',
    props: { textAlignment: 'left', textColor: 'default', backgroundColor: 'default', ...props },
    content: inlineContent,
    children,
  } as AnyBlock;
}

function checkListItem(
  content: AnyInlineContent[] | string,
  checked: boolean,
  props: Record<string, unknown> = {},
  children: AnyBlock[] = [],
): AnyBlock {
  const inlineContent =
    typeof content === 'string' ? [styledText(content)] : content;
  return {
    id: crypto.randomUUID(),
    type: 'checkListItem',
    props: { checked, textAlignment: 'left', textColor: 'default', backgroundColor: 'default', ...props },
    content: inlineContent,
    children,
  } as AnyBlock;
}

function codeBlock(
  content: AnyInlineContent[] | string,
): AnyBlock {
  const inlineContent =
    typeof content === 'string' ? [styledText(content)] : content;
  return {
    id: crypto.randomUUID(),
    type: 'codeBlock',
    props: {},
    content: inlineContent,
    children: [],
  } as AnyBlock;
}

function quote(
  content: AnyInlineContent[] | string,
  props: Record<string, unknown> = {},
): AnyBlock {
  const inlineContent =
    typeof content === 'string' ? [styledText(content)] : content;
  return {
    id: crypto.randomUUID(),
    type: 'quote',
    props: { textAlignment: 'left', textColor: 'default', backgroundColor: 'default', ...props },
    content: inlineContent,
    children: [],
  } as AnyBlock;
}

function divider(): AnyBlock {
  return {
    id: crypto.randomUUID(),
    type: 'divider',
    props: {},
    content: undefined,
    children: [],
  } as unknown as AnyBlock;
}

function block(
  type: string,
  content?: AnyInlineContent[] | string,
  props: Record<string, unknown> = {},
): AnyBlock {
  const inlineContent =
    content === undefined
      ? undefined
      : typeof content === 'string'
        ? [styledText(content)]
        : content;
  return {
    id: crypto.randomUUID(),
    type,
    props,
    content: inlineContent,
    children: [],
  } as unknown as AnyBlock;
}

function column(
  children: AnyBlock[],
  width: number = 1,
): AnyBlock {
  return {
    id: crypto.randomUUID(),
    type: 'column',
    props: { width },
    content: undefined,
    children,
  } as unknown as AnyBlock;
}

function columnList(
  columns: AnyBlock[],
): AnyBlock {
  return {
    id: crypto.randomUUID(),
    type: 'columnList',
    props: {},
    content: undefined,
    children: columns,
  } as unknown as AnyBlock;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('EmailExporter', () => {
  const exporter = new EmailExporter();

  function exportBlocks(blocks: AnyBlock[]): string {
    return exporter.exportBlocks(blocks, null);
  }

  // -----------------------------------------------------------------------
  // 1. Paragraph
  // -----------------------------------------------------------------------
  describe('paragraph', () => {
    it('renders simple text in a <p> with margin:0', () => {
      const html = exportBlocks([paragraph('Hello world')]);
      expect(html).toContain('<p');
      expect(html).toContain('margin:0');
      expect(html).toContain('Hello world');
    });

    it('renders empty paragraph as <br>', () => {
      const html = exportBlocks([paragraph([])]);
      expect(html).toContain('<br/>');
    });

    it('renders center alignment', () => {
      const html = exportBlocks([
        paragraph('Centered', { textAlignment: 'center' }),
      ]);
      expect(html).toContain('text-align:center');
    });

    it('renders textColor', () => {
      const html = exportBlocks([
        paragraph('Red text', { textColor: 'red' }),
      ]);
      expect(html).toContain('color:#e03e3e');
    });

    it('renders backgroundColor', () => {
      const html = exportBlocks([
        paragraph('Highlighted', { backgroundColor: 'yellow' }),
      ]);
      expect(html).toContain('background-color:#fbf3db');
    });

    it('renders hard breaks (Shift+Enter) as <br>', () => {
      const html = exportBlocks([
        paragraph([styledText('Line one\nLine two')]),
      ]);
      expect(html).toContain('Line one');
      expect(html).toContain('<br/>');
      expect(html).toContain('Line two');
    });

    it('renders hard breaks within styled text as <br>', () => {
      const html = exportBlocks([
        paragraph([styledText('Bold line one\nBold line two', { bold: true })]),
      ]);
      expect(html).toContain('font-weight:bold');
      expect(html).toContain('Bold line one');
      expect(html).toContain('<br/>');
      expect(html).toContain('Bold line two');
    });
  });

  // -----------------------------------------------------------------------
  // 3. Heading
  // -----------------------------------------------------------------------
  describe('heading', () => {
    it('renders level 1 as <h1>', () => {
      const html = exportBlocks([heading('Title', 1)]);
      expect(html).toContain('<h1');
      expect(html).toContain('Title');
    });

    it('renders level 2 as <h2>', () => {
      const html = exportBlocks([heading('Subtitle', 2)]);
      expect(html).toContain('<h2');
    });

    it('renders level 3 as <h3>', () => {
      const html = exportBlocks([heading('Section', 3)]);
      expect(html).toContain('<h3');
    });

    it('applies block-level styles', () => {
      const html = exportBlocks([
        heading('Colored heading', 1, { textColor: 'blue' }),
      ]);
      expect(html).toContain('color:#0b6e99');
    });
  });

  // -----------------------------------------------------------------------
  // 4. Inline styles
  // -----------------------------------------------------------------------
  describe('inline styles', () => {
    it('renders bold text', () => {
      const html = exportBlocks([
        paragraph([styledText('Bold', { bold: true })]),
      ]);
      expect(html).toContain('font-weight:bold');
      expect(html).toContain('Bold');
    });

    it('renders italic text', () => {
      const html = exportBlocks([
        paragraph([styledText('Italic', { italic: true })]),
      ]);
      expect(html).toContain('font-style:italic');
    });

    it('renders underline text', () => {
      const html = exportBlocks([
        paragraph([styledText('Underlined', { underline: true })]),
      ]);
      expect(html).toContain('text-decoration-line:underline');
    });

    it('renders strikethrough text', () => {
      const html = exportBlocks([
        paragraph([styledText('Struck', { strike: true })]),
      ]);
      expect(html).toContain('text-decoration-line:line-through');
    });

    it('renders code inline style', () => {
      const html = exportBlocks([
        paragraph([styledText('const x', { code: true })]),
      ]);
      expect(html).toContain('font-family:monospace');
    });

    it('renders combined bold + italic', () => {
      const html = exportBlocks([
        paragraph([styledText('BoldItalic', { bold: true, italic: true })]),
      ]);
      expect(html).toContain('font-weight:bold');
      expect(html).toContain('font-style:italic');
    });

    it('merges underline + strikethrough into a single text-decoration-line', () => {
      const html = exportBlocks([
        paragraph([
          styledText('Both', { underline: true, strike: true }),
        ]),
      ]);
      expect(html).toContain('text-decoration-line:underline line-through');
    });

    it('renders named textColor via COLORS', () => {
      const html = exportBlocks([
        paragraph([styledText('Purple', { textColor: 'purple' })]),
      ]);
      expect(html).toContain('color:#6940a5');
    });

    it('renders named backgroundColor via COLORS', () => {
      const html = exportBlocks([
        paragraph([styledText('Green bg', { backgroundColor: 'green' })]),
      ]);
      expect(html).toContain('background-color:#ddedea');
    });

    it('passes through non-named color values', () => {
      const html = exportBlocks([
        paragraph([styledText('Custom', { textColor: '#ff00ff' })]),
      ]);
      expect(html).toContain('color:#ff00ff');
    });

    it('ignores default color values', () => {
      const html = exportBlocks([
        paragraph([styledText('Default', { textColor: 'default', backgroundColor: 'default' })]),
      ]);
      // Should render plain text without a <span> wrapper since styles are empty
      expect(html).toContain('Default');
      expect(html).not.toContain('color:');
    });
  });

  // -----------------------------------------------------------------------
  // 5. Links
  // -----------------------------------------------------------------------
  describe('links', () => {
    it('renders a simple link with href and underline', () => {
      const html = exportBlocks([
        paragraph([link('https://example.com', 'Click here')]),
      ]);
      expect(html).toContain('<a');
      expect(html).toContain('href="https://example.com"');
      expect(html).toContain('text-decoration:underline');
      expect(html).toContain('Click here');
    });

    it('renders a link with styled text', () => {
      const styledLink: AnyInlineContent = {
        type: 'link',
        href: 'https://example.com',
        content: [styledText('Bold link', { bold: true })],
      } as unknown as AnyInlineContent;
      const html = exportBlocks([paragraph([styledLink])]);
      expect(html).toContain('font-weight:bold');
      expect(html).toContain('href="https://example.com"');
    });
  });

  // -----------------------------------------------------------------------
  // 6. Images
  // -----------------------------------------------------------------------
  describe('images', () => {
    it('renders a simple image with src and alt', () => {
      const html = exportBlocks([
        image('https://example.com/photo.jpg', { name: 'photo' }),
      ]);
      expect(html).toContain('<img');
      expect(html).toContain('src="https://example.com/photo.jpg"');
      expect(html).toContain('alt="photo"');
    });

    it('renders image with caption as <figure> + <figcaption>', () => {
      const html = exportBlocks([
        image('https://example.com/photo.jpg', { caption: 'A nice photo' }),
      ]);
      expect(html).toContain('<figure');
      expect(html).toContain('<figcaption>');
      expect(html).toContain('A nice photo');
    });

    it('renders center alignment with auto margins', () => {
      const html = exportBlocks([
        image('https://example.com/photo.jpg', { textAlignment: 'center' }),
      ]);
      expect(html).toContain('margin-left:auto');
      expect(html).toContain('margin-right:auto');
    });

    it('renders right alignment with margin-left:auto', () => {
      const html = exportBlocks([
        image('https://example.com/photo.jpg', { textAlignment: 'right' }),
      ]);
      expect(html).toContain('margin-left:auto');
      expect(html).not.toContain('margin-right:auto');
    });

    it('renders previewWidth as width attribute', () => {
      const html = exportBlocks([
        image('https://example.com/photo.jpg', { previewWidth: 300 }),
      ]);
      expect(html).toContain('width="300"');
    });

    it('does not render when url is empty', () => {
      const html = exportBlocks([
        image(''),
      ]);
      expect(html).not.toContain('<img');
    });
  });

  // -----------------------------------------------------------------------
  // 7. Lists
  // -----------------------------------------------------------------------
  describe('lists', () => {
    it('groups consecutive bullet list items in <ul>', () => {
      const html = exportBlocks([
        bulletListItem('Item A'),
        bulletListItem('Item B'),
      ]);
      expect(html).toContain('<ul>');
      expect(html).toContain('<li');
      expect(html).toContain('Item A');
      expect(html).toContain('Item B');
    });

    it('groups consecutive numbered list items in <ol>', () => {
      const html = exportBlocks([
        numberedListItem('First'),
        numberedListItem('Second'),
      ]);
      expect(html).toContain('<ol>');
      expect(html).toContain('First');
      expect(html).toContain('Second');
    });

    it('renders checked check list item with checked input', () => {
      const html = exportBlocks([
        checkListItem('Done', true),
      ]);
      expect(html).toContain('<input');
      expect(html).toContain('checked');
    });

    it('renders unchecked check list item without checked attribute', () => {
      const html = exportBlocks([
        checkListItem('Todo', false),
      ]);
      expect(html).toContain('<input');
      // The input should not have checked="" attribute
      expect(html).not.toMatch(/<input[^>]*checked/);
    });

    it('positions checkbox in the marker area with negative margin-left', () => {
      const html = exportBlocks([
        checkListItem('Task', false),
      ]);
      expect(html).toContain('margin-left:-20px');
    });

    it('renders nested lists from children', () => {
      const html = exportBlocks([
        bulletListItem('Parent', {}, [
          bulletListItem('Child'),
        ]),
      ]);
      // Nested children should generate a second <ul> within the parent <li>
      const ulCount = (html.match(/<ul>/g) || []).length;
      expect(ulCount).toBe(2);
      expect(html).toContain('Parent');
      expect(html).toContain('Child');
    });
  });

  // -----------------------------------------------------------------------
  // 8. Code block
  // -----------------------------------------------------------------------
  describe('code block', () => {
    it('renders <pre> + <code>', () => {
      const html = exportBlocks([codeBlock('console.log("hello")')]);
      expect(html).toContain('<pre');
      expect(html).toContain('<code>');
      expect(html).toContain('console.log(&quot;hello&quot;)');
    });
  });

  // -----------------------------------------------------------------------
  // 9. Quote
  // -----------------------------------------------------------------------
  describe('quote', () => {
    it('renders <blockquote> with border-left', () => {
      const html = exportBlocks([quote('A wise thought')]);
      expect(html).toContain('<blockquote');
      expect(html).toContain('border-left');
      expect(html).toContain('A wise thought');
    });
  });

  // -----------------------------------------------------------------------
  // 10. Divider
  // -----------------------------------------------------------------------
  describe('divider', () => {
    it('renders <hr> with margin:12px 0', () => {
      const html = exportBlocks([divider()]);
      expect(html).toContain('<hr');
      expect(html).toContain('margin:12px 0');
    });
  });

  // -----------------------------------------------------------------------
  // 11. Special blocks
  // -----------------------------------------------------------------------
  describe('special blocks', () => {
    it('does not render table block', () => {
      const html = exportBlocks([
        block('table'),
      ]);
      expect(html).not.toContain('<table>');
    });

    it('renders signature as empty <span>', () => {
      const html = exportBlocks([block('signature')]);
      expect(html).toContain('<span');
    });

    it('renders quoted-message as empty <span>', () => {
      const html = exportBlocks([block('quoted-message')]);
      expect(html).toContain('<span');
    });

    it('renders unknown block with content as <div>', () => {
      const html = exportBlocks([
        block('custom-block', 'Some content'),
      ]);
      expect(html).toContain('<div>');
      expect(html).toContain('Some content');
    });

    it('does not render unknown block without content', () => {
      const html = exportBlocks([block('empty-block')]);
      // Should not produce any visible element
      expect(html).not.toContain('<div>');
      expect(html).not.toContain('empty-block');
    });
  });

  // -----------------------------------------------------------------------
  // 12. Column layout
  // -----------------------------------------------------------------------
  describe('column layout', () => {
    it('renders row as a <table> with <td> columns', () => {
      const html = exportBlocks([
        columnList([
          column([paragraph('Left')], 1),
          column([paragraph('Right')], 2),
        ]),
      ]);
      expect(html).toContain('<table');
      expect(html).toContain('role="presentation"');
      expect(html).toContain('<td');
      expect(html).toContain('Left');
      expect(html).toContain('Right');
    });

    it('does not set explicit width on greedy columns', () => {
      const html = exportBlocks([
        columnList([
          column([paragraph('A')], 1),
          column([paragraph('B')], 2),
        ]),
      ]);
      const tds = html.match(/<td[^>]*>/g) || [];
      expect(tds).toHaveLength(2);
      for (const td of tds) {
        expect(td).not.toContain('width="');
      }
    });

    it('renders nested content recursively inside columns', () => {
      const html = exportBlocks([
        columnList([
          column([
            image('https://example.com/photo.jpg', { name: 'photo' }),
          ], 1),
          column([
            heading('Title', 2),
            paragraph('Description'),
          ], 2),
        ]),
      ]);
      expect(html).toContain('<img');
      expect(html).toContain('src="https://example.com/photo.jpg"');
      expect(html).toContain('<h2');
      expect(html).toContain('Title');
      expect(html).toContain('Description');
    });

    it('applies vertical-align:top and padding on <td>', () => {
      const html = exportBlocks([
        columnList([
          column([paragraph('A')], 1),
          column([paragraph('B')], 1),
        ]),
      ]);
      expect(html).toContain('vertical-align:top');
      expect(html).toContain('padding-right:12px');
      expect(html).toContain('padding-left:12px');
    });

    it('sets explicit width on shrink-to-content column with image', () => {
      const html = exportBlocks([
        columnList([
          column([image('https://example.com/photo.jpg', { previewWidth: 86 })], 0),
          column([paragraph('Text content')], 2),
        ]),
      ]);
      const tds = html.match(/<td[^>]*>/g) || [];
      expect(tds).toHaveLength(2);
      // First td (shrink column) should have width matching the image previewWidth
      expect(tds[0]).toContain('width="86"');
      // Second td (greedy column) should not have a width attribute
      expect(tds[1]).not.toContain('width=');
    });

    it('does not set width on shrink column without image previewWidth', () => {
      const html = exportBlocks([
        columnList([
          column([paragraph('No image')], 0),
          column([paragraph('Text')], 2),
        ]),
      ]);
      const tds = html.match(/<td[^>]*>/g) || [];
      expect(tds).toHaveLength(2);
      for (const td of tds) {
        expect(td).not.toContain('width=');
      }
    });

    it('renders standalone column as nothing', () => {
      const html = exportBlocks([
        column([paragraph('Orphan')]),
      ]);
      expect(html).not.toContain('Orphan');
    });
  });

  // -----------------------------------------------------------------------
  // Golden snapshots — full HTML reference to detect structural changes
  // -----------------------------------------------------------------------
  describe('golden snapshots', () => {
    it('renders a paragraph with styled text', () => {
      const html = exportBlocks([
        paragraph([
          styledText('Hello '),
          styledText('world', { bold: true }),
        ]),
      ]);
      expect(html).toMatchInlineSnapshot(`"<p style="font-size:14px;line-height:24px;margin:0;margin-top:0;margin-bottom:0;margin-left:0;margin-right:0">Hello <span style="font-weight:bold">world</span></p>"`);
    });

    it('renders a heading with block-level color', () => {
      const html = exportBlocks([
        heading('Important', 2, { textColor: 'red' }),
      ]);
      expect(html).toMatchInlineSnapshot(`"<h2 style="color:#e03e3e">Important</h2>"`);
    });

    it('renders an image with caption and center alignment', () => {
      const html = exportBlocks([
        image('https://example.com/photo.jpg', {
          caption: 'A nice photo',
          textAlignment: 'center',
          previewWidth: 400,
        }),
      ]);
      expect(html).toMatchInlineSnapshot(`"<figure style="margin:0;text-align:center"><img loading="lazy" alt="A nice photo" src="https://example.com/photo.jpg" style="display:block;outline:none;border:none;text-decoration:none;margin-left:auto;margin-right:auto" width="400"/><figcaption>A nice photo</figcaption></figure>"`);
    });
  });
});
