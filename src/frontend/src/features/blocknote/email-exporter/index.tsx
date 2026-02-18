import React, { CSSProperties } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Block, InlineContent, StyledText } from '@blocknote/core';
import { Text, Heading, Img, Link, Hr } from '@react-email/components';
import MailHelper from '@/features/utils/mail-helper';

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

function renderStyledText(st: AnyStyledText, key: number): React.ReactNode {
    const style = inlineStylesToCSS(st.styles);
    if (Object.keys(style).length === 0) {
        return st.text;
    }
    return <span key={key} style={style}>{st.text}</span>;
}

function renderInlineContent(content: AnyInlineContent[]): React.ReactNode[] {
    return content.map((ic, i) => {
        if (ic.type === 'text') {
            return renderStyledText(ic as AnyStyledText, i);
        }
        if (ic.type === 'link') {
            // BlockNote Link: { type: "link", href: string, content: StyledText[] }
            const link = ic as { type: 'link'; href: string; content: AnyStyledText[] };
            return (
                <Link key={i} href={link.href} style={{ color: '#0b6e99', textDecoration: 'underline' }}>
                    {link.content.map((st, j) => renderStyledText(st, j))}
                </Link>
            );
        }
        if (ic.type === 'template-variable') {
            const variable = ic as unknown as { props: Record<string, string> };
            return <span key={i} data-inline-content-type="template-variable">{`{${variable.props.value}}`}</span>;
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
            if (isContentEmpty(content)) {
                return <Text key={key} style={{ margin: 0, ...style }}><br /></Text>;
            }
            return (
                <Text key={key} style={{ margin: 0, ...style }}>
                    {renderInlineContent(content!)}
                </Text>
            );
        }

        case 'heading': {
            const level = Math.min(Math.max((props.level as number) || 1, 1), 6);
            const as = `h${level}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';
            return (
                <Heading key={key} as={as} style={styleOrUndefined(style)}>
                    {renderInlineContent(content || [])}
                </Heading>
            );
        }

        case 'image': {
            const url = props.url as string;
            if (!url) return null;

            const cidUrl = MailHelper.replaceBlobUrlsWithCid(url);
            const imgStyle: CSSProperties = {};

            // Resolve width from previewWidth or from the editor DOM
            let width = props.previewWidth as number | undefined;
            if (!width && editorDomElement) {
                const imgEl = editorDomElement.querySelector<HTMLImageElement>(
                    `[data-id="${block.id}"] img`,
                );
                if (imgEl?.complete && imgEl.naturalWidth > 0) {
                    width = imgEl.naturalWidth;
                }
            }

            // Alignment via margin (Img already sets display:block)
            const alignment = props.textAlignment as string | undefined;
            if (alignment === 'center') {
                imgStyle.marginLeft = 'auto';
                imgStyle.marginRight = 'auto';
            } else if (alignment === 'right') {
                imgStyle.marginLeft = 'auto';
            }

            const caption = props.caption as string | undefined;
            const imgNode = (
                <Img
                    src={cidUrl}
                    alt={caption || (props.name as string) || ''}
                    width={width}
                    style={styleOrUndefined(imgStyle)}
                    loading="lazy"
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
            return <Hr key={key} style={{ margin: '12px 0' }} />;
        }

        case 'signature':
        case 'quoted-message':
            return <span key={key} />;

        default:
            if (content && content.length > 0) {
                return <div key={key}>{renderInlineContent(content)}</div>;
            }
            return null;
    }
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

            if (block.children?.length > 0) {
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
 * Exports BlockNote blocks to email-safe HTML with inline styles.
 *
 * Unlike BlockNote's built-in `blocksToHTMLLossy`, the output uses inline
 * styles (font-weight, font-style, etc.) that email clients can render,
 * and replaces blob download URLs with cid: references for inline images.
 */
export class EmailExporter {
    exportBlocks(blocks: AnyBlock[], editorDomElement: HTMLElement | null): string {
        const nodes = transformBlocks(blocks, editorDomElement);
        return renderToStaticMarkup(<>{nodes}</>);
    }
}
