import { vi } from 'vitest';
import { EmailExporter } from './index';
import {
  AnyBlock,
  AnyInlineContent,
  block,
  bulletListItem,
  checkListItem,
  codeBlock,
  column,
  columnList,
  divider,
  heading,
  image,
  link,
  numberedListItem,
  paragraph,
  quote,
  styledText,
  table,
  tableCell,
  templateVariable,
} from '../__tests__/block-factories';

vi.mock('@/features/utils/mail-helper', () => ({
  default: {
    replaceBlobUrlsWithCid: (url: string) => url,
  },
}));

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
    // EmailExporter short-circuits signature/quoted-message regardless of their
    // `toExternalHTML`: the backend MDA composer embeds the real content.
    it('renders signature as empty <span>', () => {
      const html = exportBlocks([block('signature')]);
      expect(html).toContain('<span');
    });

    it('omits signature props even when populated', () => {
      const html = exportBlocks([
        block('signature', undefined, {
          templateId: 'tpl-uuid',
          mailboxId: 'mbx-uuid',
          messageId: 'msg-uuid',
        }),
      ]);
      expect(html).not.toContain('tpl-uuid');
      expect(html).not.toContain('mbx-uuid');
      expect(html).not.toContain('msg-uuid');
    });

    it('renders quoted-message as empty <span>', () => {
      const html = exportBlocks([block('quoted-message')]);
      expect(html).toContain('<span');
    });

    it('omits quoted-message props even when populated', () => {
      const html = exportBlocks([
        block('quoted-message', undefined, {
          subject: 'Confidential subject',
          sender: 'alice@example.com',
          recipients: 'bob@example.com',
          received_at: '2025-01-15T10:00:00Z',
          textBody: 'Original message body',
        }),
      ]);
      expect(html).not.toContain('Confidential subject');
      expect(html).not.toContain('alice@example.com');
      expect(html).not.toContain('Original message body');
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
  // Inline template-variable — InlineTemplateVariable has no toExternalHTML;
  // EmailExporter handles it explicitly at index.tsx:156-158. These tests
  // pin the current behavior (literal `{var}` output).
  // -----------------------------------------------------------------------
  describe('inline template-variable', () => {
    it('renders a standalone template-variable as a placeholder span', () => {
      const html = exportBlocks([
        paragraph([templateVariable('first_name')]),
      ]);
      expect(html).toContain('data-inline-content-type="template-variable"');
      expect(html).toContain('{first_name}');
    });

    it('preserves order and styles around an inline template-variable', () => {
      const html = exportBlocks([
        paragraph([
          styledText('Hi '),
          templateVariable('first_name'),
          styledText(', welcome!', { bold: true }),
        ]),
      ]);
      const hiIdx = html.indexOf('Hi ');
      const varIdx = html.indexOf('{first_name}');
      const welcomeIdx = html.indexOf('welcome!');
      expect(hiIdx).toBeGreaterThan(-1);
      expect(varIdx).toBeGreaterThan(hiIdx);
      expect(welcomeIdx).toBeGreaterThan(varIdx);
      expect(html).toContain('font-weight:bold');
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
  // 13. Table (paste-only support — see HIDDEN_BLOCK_TYPES)
  // -----------------------------------------------------------------------
  describe('table', () => {
    it('renders a simple 2x2 table with <tbody>, <tr> and <td>', () => {
      const html = exportBlocks([
        table([
          [tableCell('A1'), tableCell('A2')],
          [tableCell('B1'), tableCell('B2')],
        ]),
      ]);
      expect(html).toContain('<table');
      expect(html).toContain('<tbody>');
      expect(html).toContain('<tr>');
      expect(html).toContain('<td');
      expect(html).toContain('A1');
      expect(html).toContain('B2');
    });

    it('applies border-collapse and word-break on the table', () => {
      const html = exportBlocks([
        table([[tableCell('cell')]]),
      ]);
      expect(html).toContain('border-collapse:collapse');
      expect(html).toContain('word-break:break-word');
    });

    it('applies cell borders and padding', () => {
      const html = exportBlocks([
        table([[tableCell('cell')]]),
      ]);
      expect(html).toContain('border:1px solid #ddd');
      expect(html).toContain('padding:5px 10px');
    });

    it('returns null when content has no rows', () => {
      const emptyTable = {
        id: crypto.randomUUID(),
        type: 'table',
        props: { textColor: 'default' },
        content: { type: 'tableContent', columnWidths: [], rows: [] },
        children: [],
      } as unknown as AnyBlock;
      const html = exportBlocks([emptyTable]);
      expect(html).not.toContain('<table');
    });

    it('renders header rows as <th> with bold weight', () => {
      const html = exportBlocks([
        table(
          [
            [tableCell('Header A'), tableCell('Header B')],
            [tableCell('Body A'), tableCell('Body B')],
          ],
          { headerRows: 1 },
        ),
      ]);
      expect(html).toContain('<th');
      expect(html).toContain('Header A');
      // Body row should still use <td>
      expect(html).toContain('<td');
      // <th> cells should be bold
      expect(html).toMatch(/<th[^>]*font-weight:bold/);
    });

    it('renders header columns as <th>', () => {
      const html = exportBlocks([
        table(
          [
            [tableCell('Row 1 label'), tableCell('Row 1 value')],
            [tableCell('Row 2 label'), tableCell('Row 2 value')],
          ],
          { headerCols: 1 },
        ),
      ]);
      const ths = html.match(/<th[^>]*>/g) || [];
      expect(ths).toHaveLength(2);
    });

    it('emits <colgroup> when columnWidths contains explicit pixel widths', () => {
      const html = exportBlocks([
        table(
          [
            [tableCell('A'), tableCell('B')],
          ],
          { columnWidths: [120, 240] },
        ),
      ]);
      expect(html).toContain('<colgroup>');
      expect(html).toContain('width:120px');
      expect(html).toContain('width:240px');
    });

    it('does not emit <colgroup> when every width is undefined', () => {
      const html = exportBlocks([
        table(
          [[tableCell('A'), tableCell('B')]],
          { columnWidths: [undefined, undefined] },
        ),
      ]);
      expect(html).not.toContain('<colgroup>');
    });

    it('applies cell-level textColor, backgroundColor and textAlignment', () => {
      const html = exportBlocks([
        table([[
          tableCell('Styled', {
            textColor: 'red',
            backgroundColor: 'yellow',
            textAlignment: 'center',
          }),
        ]]),
      ]);
      expect(html).toContain('color:#e03e3e');
      expect(html).toContain('background-color:#fbf3db');
      expect(html).toContain('text-align:center');
    });

    it('emits colSpan and rowSpan when greater than 1', () => {
      const html = exportBlocks([
        table([
          [tableCell('Spanning', { colspan: 2, rowspan: 2 })],
        ]),
      ]);
      // HTML attributes are case-insensitive; React 19 preserves camelCase
      expect(html).toMatch(/colSpan="2"/i);
      expect(html).toMatch(/rowSpan="2"/i);
    });

    it('ignores colspan/rowspan of 1', () => {
      const html = exportBlocks([
        table([[tableCell('Normal', { colspan: 1, rowspan: 1 })]]),
      ]);
      expect(html).not.toMatch(/colSpan=/i);
      expect(html).not.toMatch(/rowSpan=/i);
    });

    it('handles legacy cell shape (bare InlineContent[])', () => {
      const html = exportBlocks([
        table([
          [[styledText('Legacy A')], [styledText('Legacy B')]],
        ]),
      ]);
      expect(html).toContain('Legacy A');
      expect(html).toContain('Legacy B');
      expect(html).toContain('<td');
    });

    it('applies table-level textColor', () => {
      const html = exportBlocks([
        table(
          [[tableCell('Blue table')]],
          { props: { textColor: 'blue' } },
        ),
      ]);
      // textColor on the <table> should produce inline style
      expect(html).toMatch(/<table[^>]*color:#0b6e99/);
    });

    it('does not throw on malformed rows and cells', () => {
      const malformed = {
        id: crypto.randomUUID(),
        type: 'table',
        props: { textColor: 'default' },
        content: {
          type: 'tableContent',
          rows: [
            { cells: [null, { type: 'tableCell', content: 'invalid' }] },
            {} as unknown as { cells: unknown[] },
            null as unknown as { cells: unknown[] },
            { cells: [tableCell('Survivor')] },
          ],
        },
        children: [],
      } as unknown as AnyBlock;

      let html = '';
      expect(() => {
        html = exportBlocks([malformed]);
      }).not.toThrow();
      expect(html).toContain('<table');
      expect(html).toContain('Survivor');
    });
  });

  // -----------------------------------------------------------------------
  // 14. Nested combinations — mixed structures that exercise block recursion
  // -----------------------------------------------------------------------
  describe('nested combinations', () => {
    it('renders a bullet list inside a column', () => {
      const html = exportBlocks([
        columnList([
          column([bulletListItem('Item A'), bulletListItem('Item B')], 1),
          column([paragraph('Right')], 1),
        ]),
      ]);
      // Both items must appear inside a single <ul> within the first <td>.
      expect(html).toMatch(/<td[^>]*>[\s\S]*<ul>[\s\S]*Item A[\s\S]*Item B[\s\S]*<\/ul>[\s\S]*<\/td>/);
      expect(html).toContain('Right');
    });

    it('renders styled text inside a table cell', () => {
      const html = exportBlocks([
        table([
          [
            tableCell([
              styledText('Bold-italic', { bold: true, italic: true }),
            ]),
          ],
        ]),
      ]);
      expect(html).toContain('font-weight:bold');
      expect(html).toContain('font-style:italic');
      expect(html).toContain('Bold-italic');
    });

    it('renders a quote containing styled inline content', () => {
      const html = exportBlocks([
        quote([
          styledText('Hello ', { bold: true }),
          styledText('world', { italic: true }),
        ]),
      ]);
      expect(html).toMatch(/<blockquote[^>]*>[\s\S]*font-weight:bold[\s\S]*font-style:italic[\s\S]*<\/blockquote>/);
    });

    it('renders nested bullet list inside a numbered list item', () => {
      const html = exportBlocks([
        numberedListItem('Parent', {}, [
          bulletListItem('Nested A'),
          bulletListItem('Nested B'),
        ]),
      ]);
      // Outer <ol> wrapping a <li> that itself contains an inner <ul>.
      expect(html).toMatch(/<ol>[\s\S]*<li[\s\S]*Parent[\s\S]*<ul>[\s\S]*Nested A[\s\S]*Nested B[\s\S]*<\/ul>[\s\S]*<\/li>[\s\S]*<\/ol>/);
    });

    it('renders hard breaks inside list items as <br>', () => {
      const html = exportBlocks([
        bulletListItem([styledText('Line one\nLine two')]),
      ]);
      expect(html).toMatch(/<li[^>]*>[\s\S]*Line one[\s\S]*<br\/?>[\s\S]*Line two[\s\S]*<\/li>/);
    });

    it('renders hard breaks inside a quote', () => {
      const html = exportBlocks([
        quote([styledText('Quote line 1\nQuote line 2')]),
      ]);
      expect(html).toMatch(/<blockquote[^>]*>[\s\S]*Quote line 1[\s\S]*<br\/?>[\s\S]*Quote line 2[\s\S]*<\/blockquote>/);
    });

    it('flushes a quote child as a sibling after the blockquote', () => {
      // transformBlocks pushes non-column children AFTER the parent block,
      // so an image declared as a quote child appears OUTSIDE the <blockquote>.
      // This pins the current behavior so a future change to recurse inside
      // the blockquote shape is caught.
      const html = exportBlocks([
        quote('Wisdom', {}, [
          image('https://example.com/quote.jpg', { name: 'photo' }),
        ]),
      ]);
      const blockquoteEnd = html.indexOf('</blockquote>');
      const imgIdx = html.indexOf('<img');
      expect(blockquoteEnd).toBeGreaterThan(-1);
      expect(imgIdx).toBeGreaterThan(blockquoteEnd);
    });

    it('renders heading + bullet list + paragraph inside a single column', () => {
      const html = exportBlocks([
        columnList([
          column([
            heading('Section', 2),
            bulletListItem('First'),
            bulletListItem('Second'),
            paragraph('Closing paragraph.'),
          ], 1),
          column([paragraph('Sidebar')], 1),
        ]),
      ]);
      expect(html).toContain('<h2');
      expect(html).toContain('Section');
      expect(html).toMatch(/<ul>[\s\S]*First[\s\S]*Second[\s\S]*<\/ul>/);
      expect(html).toContain('Closing paragraph.');
      expect(html).toContain('Sidebar');
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
