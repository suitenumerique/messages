import React, { CSSProperties } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { VALID_LINK_PROTOCOLS } from '@blocknote/core';
import type { Block, InlineContent, StyledText } from '@blocknote/core';
import MailHelper from '@/features/utils/mail-helper';
import { TEMPLATE_VARIABLE_TYPE } from '../inline-template-variable';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyBlock = Block<any, any, any>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyInlineContent = InlineContent<any, any>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyStyledText = StyledText<any>;

// Inline copy of COLORS_DEFAULT from @blocknote/core (not part of the public API)
const COLORS: Record<string, { text: string; background: string }> = {
    gray: { text: '#9b9a97', background: '#ebeced' },
    brown: { text: '#64473a', background: '#e9e5e3' },
    red: { text: '#e03e3e', background: '#fbe4e4' },
    orange: { text: '#d9730d', background: '#f6e9d9' },
    yellow: { text: '#dfab01', background: '#fbf3db' },
    green: { text: '#4d6461', background: '#ddedea' },
    blue: { text: '#0b6e99', background: '#ddebf1' },
    purple: { text: '#6940a5', background: '#eae4f2' },
    pink: { text: '#ad1a72', background: '#f4dfeb' },
};

// BlockNote renders heading sizes via CSS variables on `data-level` attributes,
// not on the <h1>-<h6> tags — and its `.bn-default-styles` rule forces
// `font-size: inherit` on those tags. A heading exported as a bare tag is thus
// flattened to body size wherever that stylesheet applies (e.g. a signature
// preview injected inside the editor). We inline BlockNote's own scale so the
// heading keeps its size in previews and email clients alike.
const HEADING_LEVEL_STYLES: Record<number, CSSProperties> = {
    1: { fontSize: '3em', fontWeight: 700 },
    2: { fontSize: '2em', fontWeight: 700 },
    3: { fontSize: '1.3em', fontWeight: 700 },
    4: { fontSize: '1em', fontWeight: 700 },
    5: { fontSize: '0.9em', fontWeight: 700 },
    6: { fontSize: '0.8em', fontWeight: 700 },
};

// ---------------------------------------------------------------------------
// Style utilities
// ---------------------------------------------------------------------------

function mergeStyles(styles: CSSProperties[]): CSSProperties {
    const merged: CSSProperties = {};
    const textDecorations: string[] = [];

    for (const style of styles) {
        const { textDecorationLine, ...rest } = style;
        Object.assign(merged, rest);
        if (textDecorationLine) {
            textDecorations.push(textDecorationLine as string);
        }
    }

    if (textDecorations.length > 0) {
        merged.textDecorationLine = textDecorations.join(' ');
    }

    return merged;
}

function mapStyle(key: string, value: boolean | string): CSSProperties {
    switch (key) {
        case 'bold':
            return value ? { fontWeight: 'bold' } : {};
        case 'italic':
            return value ? { fontStyle: 'italic' } : {};
        case 'underline':
            return value ? { textDecorationLine: 'underline' } : {};
        case 'strike':
            return value ? { textDecorationLine: 'line-through' } : {};
        case 'code':
            return value
                ? {
                    fontFamily: 'monospace',
                    backgroundColor: '#f0f0f0',
                    padding: '2px 4px',
                    borderRadius: '3px',
                }
                : {};
        case 'textColor':
            if (typeof value === 'string' && value !== 'default') {
                return { color: COLORS[value]?.text || value };
            }
            return {};
        case 'backgroundColor':
            if (typeof value === 'string' && value !== 'default') {
                return { backgroundColor: COLORS[value]?.background || value };
            }
            return {};
        default:
            return {};
    }
}

function inlineStylesToCSS(styles: Record<string, unknown>): CSSProperties {
    const cssArray = Object.entries(styles)
        .filter(([, value]) => value !== undefined && value !== false)
        .map(([key, value]) => mapStyle(key, value as boolean | string));
    return mergeStyles(cssArray);
}

function blockPropsToCSS(props: Record<string, unknown>): CSSProperties {
    const style: CSSProperties = {};

    const alignment = props.textAlignment as string | undefined;
    if (alignment && alignment !== 'left') {
        style.textAlign = alignment as CSSProperties['textAlign'];
    }

    const textColor = props.textColor as string | undefined;
    if (textColor && textColor !== 'default') {
        style.color = COLORS[textColor]?.text || textColor;
    }

    const bgColor = props.backgroundColor as string | undefined;
    if (bgColor && bgColor !== 'default') {
        style.backgroundColor = COLORS[bgColor]?.background || bgColor;
    }

    return style;
}

function styleOrUndefined(style: CSSProperties): CSSProperties | undefined {
    return Object.keys(style).length > 0 ? style : undefined;
}

// ---------------------------------------------------------------------------
// Inline content rendering
// ---------------------------------------------------------------------------

function textWithBreaks(text: string): React.ReactNode {
    if (!text.includes('\n')) {
        return text;
    }
    const parts = text.split('\n');
    return parts.map((part, i) => (
        <React.Fragment key={i}>
            {part}
            {i < parts.length - 1 && <br />}
        </React.Fragment>
    ));
}

/**
 * Validates a link href against BlockNote's own {@link VALID_LINK_PROTOCOLS}
 * allowlist so the export stays consistent with what the editor permits.
 * The editor only enforces this at render/paste time, so a value carrying a
 * dangerous scheme (`javascript:`, `data:`…) can still reach the stored
 * document and must be filtered here before it lands in the email HTML.
 *
 * We rely on the `URL` parser to read the scheme: per the WHATWG spec it strips
 * tab/newline/control characters, so obfuscations like `java\tscript:` cannot
 * smuggle a disallowed scheme past the check. A parse failure means a
 * schemeless/relative URL (anchor, path, query) — there is no scheme to vet.
 *
 * @param href - the raw href from the link inline content
 * @returns the normalized href for an allowed absolute scheme, the raw href for
 *   a schemeless/relative URL, or `null` when the scheme is rejected
 */
function sanitizeLinkHref(href: string): string | null {
    if (!href) return null;
    let parsed: URL;
    try {
        parsed = new URL(href);
    } catch {
        return href;
    }
    const scheme = parsed.protocol.replace(/:$/, '');
    // Return the parser's normalized href, not the raw input: we validated the
    // derived scheme, so we must emit the string that scheme was read from to
    // avoid a parser-differential gap with the email client's renderer.
    return VALID_LINK_PROTOCOLS.includes(scheme.toLowerCase()) ? parsed.href : null;
}

function renderStyledText(st: AnyStyledText, key: number): React.ReactNode {
    const style = inlineStylesToCSS(st.styles);
    const content = textWithBreaks(st.text);
    if (Object.keys(style).length === 0) {
        return <React.Fragment key={key}>{content}</React.Fragment>;
    }
    return <span key={key} style={style}>{content}</span>;
}

function renderInlineContent(content: AnyInlineContent[]): React.ReactNode[] {
    return content.map((ic, i) => {
        if (ic.type === 'text') {
            return renderStyledText(ic as AnyStyledText, i);
        }
        if (ic.type === 'link') {
            // BlockNote Link: { type: "link", href: string, content: StyledText[] }
            const link = ic as { type: 'link'; href: string; content: AnyStyledText[] };
            const renderedContent = link.content.map((st, j) => renderStyledText(st, j));
            const safeHref = sanitizeLinkHref(link.href);
            // Drop the anchor but keep its text when the href is rejected, so an
            // unsafe link degrades to plain text rather than a dangerous <a>.
            if (!safeHref) {
                return <React.Fragment key={i}>{renderedContent}</React.Fragment>;
            }
            // Mirror the link text's own color onto the <a> so the underline
            // matches the text instead of staying the default link blue.
            const textColor = link.content
                .map((st) => st.styles?.textColor as string | undefined)
                .find((color) => color && color !== 'default');
            const linkColor = textColor && (COLORS[textColor]?.text || textColor);
            return (
                <a
                    key={i}
                    href={safeHref}
                    rel="noopener noreferrer"
                    style={styleOrUndefined({ color: linkColor })}
                >
                    {renderedContent}
                </a>
            );
        }
        if (ic.type === TEMPLATE_VARIABLE_TYPE) {
            const variable = ic as unknown as { props: Record<string, string>; content?: AnyStyledText[] };
            // The editor shows the human label, but the export keeps the canonical
            // `{value}` token — carrying the styles applied to it.
            const styles = variable.content?.[0]?.styles ?? {};
            const token = { type: 'text', text: `{${variable.props.value}}`, styles } as AnyStyledText;
            return <span key={i} data-inline-content-type={TEMPLATE_VARIABLE_TYPE}>{renderStyledText(token, 0)}</span>;
        }
        return null;
    });
}

function isContentEmpty(content: AnyInlineContent[] | undefined): boolean {
    if (!content || content.length === 0) return true;
    return content.every(
        (ic) => ic.type === 'text' && !(ic as AnyStyledText).text,
    );
}

// ---------------------------------------------------------------------------
// Image / column width resolution
// ---------------------------------------------------------------------------

/**
 * Resolves the pixel width of an image block from its `previewWidth` prop,
 * falling back to the natural width read from the editor DOM when the image
 * was never resized by the user.
 */
function resolveImageWidth(
    block: AnyBlock,
    editorDomElement: HTMLElement | null,
): number | undefined {
    const props = block.props as Record<string, unknown>;
    let width = props.previewWidth as number | undefined;
    if (!width && editorDomElement) {
        const imgEl = editorDomElement.querySelector<HTMLImageElement>(
            `[data-id="${block.id}"] img`,
        );
        if (imgEl?.complete && imgEl.naturalWidth > 0) {
            width = imgEl.naturalWidth;
        }
    }
    return width;
}

/**
 * Computes the pixel width of a shrink-to-content column by returning the
 * widest image width among its children.  The HTML table algorithm uses this
 * value to allocate exactly the right space for the column.
 */
function resolveColumnContentWidth(
    blocks: AnyBlock[],
    editorDomElement: HTMLElement | null,
): number | undefined {
    let maxWidth: number | undefined;
    for (const block of blocks) {
        if (block.type === 'image') {
            const w = resolveImageWidth(block, editorDomElement);
            if (w && (!maxWidth || w > maxWidth)) {
                maxWidth = w;
            }
        }
    }
    return maxWidth;
}

// ---------------------------------------------------------------------------
// Block rendering
// ---------------------------------------------------------------------------

type ListTag = 'ul' | 'ol';

function getListTag(blockType: string): ListTag | null {
    switch (blockType) {
        case 'bulletListItem':
        case 'checkListItem':
            return 'ul';
        case 'numberedListItem':
            return 'ol';
        default:
            return null;
    }
}

function renderListItem(
    block: AnyBlock,
    editorDomElement: HTMLElement | null,
    nestedContent: React.ReactNode[] | null,
    key: number,
): React.ReactNode {
    const props = block.props as Record<string, unknown>;
    const style = blockPropsToCSS(props);
    const content = block.content as AnyInlineContent[] | undefined;

    if (block.type === 'checkListItem') {
        const checked = (props.checked as boolean) || false;
        return (
            <li key={key} style={{ ...style, listStyleType: 'none' }}>
                {/* Apply a negative margin to the checkbox to position it in the marker area (mimic list-style-position: outside) */}
                <input type="checkbox" defaultChecked={checked} disabled style={{ marginLeft: '-20px', marginRight: '4px' }} />
                {renderInlineContent(content || [])}
                {nestedContent}
            </li>
        );
    }

    return (
        <li key={key} style={styleOrUndefined(style)}>
            {renderInlineContent(content || [])}
            {nestedContent}
        </li>
    );
}

function renderBlock(
    block: AnyBlock,
    editorDomElement: HTMLElement | null,
    key: number,
): React.ReactNode {
    const props = block.props as Record<string, unknown>;
    const style = blockPropsToCSS(props);
    const content = block.content as AnyInlineContent[] | undefined;

    switch (block.type) {
        case 'paragraph': {
            // A plain paragraph (no alignment/color) is emitted as a bare <p> with
            // no inline style. Spam filters (e.g. iCloud) flag every `<p style=...>`
            // as machine-generated templating, so styles are only attached when the
            // user actually applied block-level formatting.
            const pStyle = styleOrUndefined(style);
            if (isContentEmpty(content)) {
                return <p key={key} style={pStyle}><br /></p>;
            }
            return (
                <p key={key} style={pStyle}>
                    {renderInlineContent(content!)}
                </p>
            );
        }

        case 'heading': {
            const level = Math.min(Math.max((props.level as number) || 1, 1), 6);
            const Tag = `h${level}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';
            // `margin: 0` matches BlockNote (spacing comes from block padding) and
            // avoids the browser's large default heading margins in email clients.
            // Block-level styles (color/alignment) come last so they can override.
            return (
                <Tag key={key} style={{ margin: 0, ...HEADING_LEVEL_STYLES[level], ...style }}>
                    {renderInlineContent(content || [])}
                </Tag>
            );
        }

        case 'image': {
            const url = props.url as string;
            if (!url) return null;

            const cidUrl = MailHelper.replaceBlobUrlsWithCid(url);
            // display:block is mandatory, not just for alignment: an inline
            // <img> sits on the text baseline and email clients render a few px
            // of phantom whitespace beneath it (the classic "image bottom gap").
            // Centering / right-aligning then relies on auto margins, which only
            // apply to a block-level element anyway.
            const imgStyle: CSSProperties = { display: 'block' };

            const width = resolveImageWidth(block, editorDomElement);

            const alignment = props.textAlignment as string | undefined;
            if (alignment === 'center') {
                imgStyle.marginLeft = 'auto';
                imgStyle.marginRight = 'auto';
            } else if (alignment === 'right') {
                imgStyle.marginLeft = 'auto';
            }

            const caption = props.caption as string | undefined;
            const imgNode = (
                <img
                    loading="lazy"
                    alt={caption || (props.name as string) || ''}
                    src={cidUrl}
                    style={styleOrUndefined(imgStyle)}
                    width={width}
                />
            );

            if (caption) {
                return (
                    <figure key={key} style={{ margin: '0', textAlign: (alignment as CSSProperties['textAlign']) || undefined }}>
                        {imgNode}
                        <figcaption>{caption}</figcaption>
                    </figure>
                );
            }
            return React.cloneElement(imgNode, { key });
        }

        case 'codeBlock': {
            return (
                <pre key={key} style={{ backgroundColor: '#f5f5f5', padding: '12px', borderRadius: '4px', overflowX: 'auto' }}>
                    <code>{renderInlineContent(content || [])}</code>
                </pre>
            );
        }

        case 'quote': {
            return (
                <blockquote key={key} style={{ borderLeft: '3px solid #ccc', paddingLeft: '12px', margin: '8px 0', ...style }}>
                    {renderInlineContent(content || [])}
                </blockquote>
            );
        }

        case 'divider': {
            return <hr key={key} style={{ margin: '12px 0' }} />;
        }

        case 'columnList': {
            const columns = (block.children || []).filter(
                (child: AnyBlock) => child.type === 'column',
            );
            const COLUMN_PADDING = 12;

            // A multi-column layout genuinely needs a table; `role="presentation"`
            // tells assistive tech and spam heuristics it is for layout, not data.
            return (
                <table
                    key={key}
                    role="presentation"
                    cellPadding={0}
                    cellSpacing={0}
                    border={0}
                    width="100%"
                    style={{ padding: `${COLUMN_PADDING}px 0` }}
                >
                    <tbody>
                        <tr>
                            {columns.map((col: AnyBlock, colIdx: number) => {
                                const colStyle: CSSProperties = { verticalAlign: 'top' };
                                if (colIdx === 0) {
                                    colStyle.paddingRight = `${COLUMN_PADDING}px`;
                                }
                                else if (colIdx === columns.length - 1) {
                                    colStyle.paddingLeft = `${COLUMN_PADDING}px`;
                                }
                                else {
                                    colStyle.padding = `0 ${COLUMN_PADDING}px`;
                                }
                                const w = Number((col.props as Record<string, unknown>).width);

                                // For shrink-to-content columns (width: 0), compute the
                                // exact pixel width from child image blocks so the HTML
                                // table algorithm allocates the correct space.  Without
                                // an explicit width, the table distributes space evenly;
                                // with it, sibling cells take the remaining space.
                                let tdWidth: string | undefined;
                                if (w === 0) {
                                    const contentWidth = resolveColumnContentWidth(
                                        col.children || [],
                                        editorDomElement,
                                    );
                                    if (contentWidth) {
                                        tdWidth = String(contentWidth);
                                    }
                                }

                                return (
                                    <td key={colIdx} style={colStyle} width={tdWidth}>
                                        {transformBlocks(col.children || [], editorDomElement)}
                                    </td>
                                );
                            })}
                        </tr>
                    </tbody>
                </table>
            );
        }

        case 'column':
            // Columns are rendered as <td> inside row – standalone column is a no-op
            return null;

        case 'signature':
        case 'quoted-message':
            return <span key={key} />;

        case 'table':
            return renderTable(block, key);

        default:
            if (Array.isArray(content) && content.length > 0) {
                return <div key={key}>{renderInlineContent(content)}</div>;
            }
            return null;
    }
}

// ---------------------------------------------------------------------------
// Table rendering
// ---------------------------------------------------------------------------

type TableCellLike = {
    content: AnyInlineContent[];
    props?: Record<string, unknown>;
};

/**
 * Normalises a BlockNote table cell: cells can be either raw inline content
 * arrays (legacy) or { type: 'tableCell', content, props } objects.
 */
function normalizeTableCell(cell: unknown): TableCellLike {
    if (Array.isArray(cell)) {
        return { content: cell as AnyInlineContent[] };
    }
    if (!cell || typeof cell !== 'object') {
        return { content: [] };
    }
    const cellObj = cell as { content?: unknown; props?: Record<string, unknown> };
    return {
        content: Array.isArray(cellObj.content) ? (cellObj.content as AnyInlineContent[]) : [],
        props: cellObj.props,
    };
}

function tableCellStyle(props: Record<string, unknown> | undefined): CSSProperties {
    const style: CSSProperties = {
        border: '1px solid #ddd',
        padding: '5px 10px',
        verticalAlign: 'top',
    };
    if (!props) return style;

    const alignment = props.textAlignment as string | undefined;
    if (alignment && alignment !== 'left') {
        style.textAlign = alignment as CSSProperties['textAlign'];
    }
    const textColor = props.textColor as string | undefined;
    if (textColor && textColor !== 'default') {
        style.color = COLORS[textColor]?.text || textColor;
    }
    const bgColor = props.backgroundColor as string | undefined;
    if (bgColor && bgColor !== 'default') {
        style.backgroundColor = COLORS[bgColor]?.background || bgColor;
    }
    return style;
}

/**
 * Renders a BlockNote table block as an HTML <table>.
 *
 * BlockNote tables can use `headerRows` / `headerCols` to mark the leading
 * rows or columns as headers (rendered as <th>).  Column widths from
 * `columnWidths` are emitted via a <colgroup> so email clients allocate
 * space deterministically.
 */
function renderTable(block: AnyBlock, key: number): React.ReactNode {
    const tableContent = block.content as unknown as {
        type: 'tableContent';
        columnWidths?: (number | undefined)[];
        headerRows?: number;
        headerCols?: number;
        rows: { cells: unknown[] }[];
    } | undefined;

    if (!tableContent || !Array.isArray(tableContent.rows) || tableContent.rows.length === 0) {
        return null;
    }

    const headerRows = tableContent.headerRows ?? 0;
    const headerCols = tableContent.headerCols ?? 0;
    const columnWidths = tableContent.columnWidths || [];
    const blockProps = block.props as Record<string, unknown>;
    const blockTextColor = blockProps.textColor as string | undefined;

    const tableStyle: CSSProperties = {
        borderCollapse: 'collapse',
        wordBreak: 'break-word',
    };
    if (blockTextColor && blockTextColor !== 'default') {
        tableStyle.color = COLORS[blockTextColor]?.text || blockTextColor;
    }

    const colgroup = columnWidths.some((w) => typeof w === 'number') ? (
        <colgroup>
            {columnWidths.map((w, i) => (
                <col key={i} style={typeof w === 'number' ? { width: `${w}px` } : undefined} />
            ))}
        </colgroup>
    ) : null;

    return (
        <table key={key} style={tableStyle}>
            {colgroup}
            <tbody>
                {tableContent.rows.map((row, rowIdx) => {
                    const cells = Array.isArray(row?.cells) ? row.cells : [];
                    return (
                        <tr key={rowIdx}>
                            {cells.map((rawCell, colIdx) => {
                                const cell = normalizeTableCell(rawCell);
                                const isHeader = rowIdx < headerRows || colIdx < headerCols;
                                const Tag = isHeader ? 'th' : 'td';
                                const style = tableCellStyle(cell.props);
                                if (isHeader) {
                                    style.fontWeight = 'bold';
                                    if (!style.textAlign) {
                                        style.textAlign = 'left';
                                    }
                                }
                                const colspan = cell.props?.colspan as number | undefined;
                                const rowspan = cell.props?.rowspan as number | undefined;
                                return (
                                    <Tag
                                        key={colIdx}
                                        style={style}
                                        colSpan={colspan && colspan > 1 ? colspan : undefined}
                                        rowSpan={rowspan && rowspan > 1 ? rowspan : undefined}
                                    >
                                        {renderInlineContent(cell.content)}
                                    </Tag>
                                );
                            })}
                        </tr>
                    );
                })}
            </tbody>
        </table>
    );
}

// ---------------------------------------------------------------------------
// Block tree → React node list (groups consecutive list items)
// ---------------------------------------------------------------------------

function transformBlocks(
    blocks: AnyBlock[],
    editorDomElement: HTMLElement | null,
): React.ReactNode[] {
    const result: React.ReactNode[] = [];
    let i = 0;

    while (i < blocks.length) {
        const block = blocks[i];
        const listTag = getListTag(block.type);

        if (listTag) {
            const listItems: React.ReactNode[] = [];
            const startI = i;

            while (i < blocks.length && getListTag(blocks[i].type) === listTag) {
                const item = blocks[i];
                const nested = item.children?.length > 0
                    ? transformBlocks(item.children, editorDomElement)
                    : null;
                listItems.push(renderListItem(item, editorDomElement, nested, i));
                i++;
            }

            const ListTag = listTag;
            result.push(<ListTag key={`list-${startI}`}>{listItems}</ListTag>);
        } else {
            result.push(renderBlock(block, editorDomElement, i));

            // Skip children for column blocks (handled inside row rendering)
            if (block.type !== 'column' && block.children?.length > 0) {
                result.push(...transformBlocks(block.children, editorDomElement));
            }

            i++;
        }
    }

    return result;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Exports BlockNote blocks to email-safe HTML using native tags.
 *
 * Unlike BlockNote's built-in `blocksToHTMLLossy`, the output relies on inline
 * styles (font-weight, font-style, etc.) that email clients can render, and
 * replaces blob download URLs with cid: references for inline images.
 *
 * Inline styles are attached only where the user actually applied formatting:
 * an unstyled paragraph exports as a bare `<p>`. This keeps simple messages
 * looking human-authored, since spam filters (e.g. iCloud) treat blanket
 * `<p style=...>` templating as a machine-generated-bulk signal.
 */
export class EmailExporter {
    exportBlocks(blocks: AnyBlock[], editorDomElement: HTMLElement | null): string {
        const nodes = transformBlocks(blocks, editorDomElement);
        return renderToStaticMarkup(<>{nodes}</>);
    }
}
