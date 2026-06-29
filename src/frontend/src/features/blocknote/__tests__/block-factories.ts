import type { Block, InlineContent, StyledText } from '@blocknote/core';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type AnyBlock = Block<any, any, any>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type AnyStyledText = StyledText<any>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type AnyInlineContent = InlineContent<any, any>;

export function styledText(
  text: string,
  styles: Record<string, unknown> = {},
): AnyStyledText {
  return { type: 'text', text, styles } as AnyStyledText;
}

export function link(href: string, text: string): AnyInlineContent {
  return {
    type: 'link',
    href,
    content: [styledText(text)],
  } as unknown as AnyInlineContent;
}

export function templateVariable(
  value: string,
  label?: string,
  styles: Record<string, unknown> = {},
): AnyInlineContent {
  const display = label ?? value;
  return {
    type: 'template-variable',
    props: { value, label: display },
    content: [styledText(display, styles)],
  } as unknown as AnyInlineContent;
}

export function paragraph(
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

export function heading(
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

export function image(
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

export function bulletListItem(
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

export function numberedListItem(
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

export function checkListItem(
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

export function codeBlock(
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

export function quote(
  content: AnyInlineContent[] | string,
  props: Record<string, unknown> = {},
  children: AnyBlock[] = [],
): AnyBlock {
  const inlineContent =
    typeof content === 'string' ? [styledText(content)] : content;
  return {
    id: crypto.randomUUID(),
    type: 'quote',
    props: { textAlignment: 'left', textColor: 'default', backgroundColor: 'default', ...props },
    content: inlineContent,
    children,
  } as AnyBlock;
}

export function divider(): AnyBlock {
  return {
    id: crypto.randomUUID(),
    type: 'divider',
    props: {},
    content: undefined,
    children: [],
  } as unknown as AnyBlock;
}

export function block(
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

export function column(
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

export function columnList(
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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type TableCellShape = AnyInlineContent[] | { type: 'tableCell'; content: AnyInlineContent[]; props?: Record<string, any> };

export function tableCell(
  content: string | AnyInlineContent[],
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  props: Record<string, any> = {},
): TableCellShape {
  const inlineContent =
    typeof content === 'string' ? [styledText(content)] : content;
  return {
    type: 'tableCell',
    content: inlineContent,
    props,
  };
}

export function table(
  rows: TableCellShape[][],
  options: {
    columnWidths?: (number | undefined)[];
    headerRows?: number;
    headerCols?: number;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    props?: Record<string, any>;
  } = {},
): AnyBlock {
  return {
    id: crypto.randomUUID(),
    type: 'table',
    props: { textColor: 'default', ...(options.props || {}) },
    content: {
      type: 'tableContent',
      columnWidths: options.columnWidths || rows[0]?.map(() => undefined) || [],
      headerRows: options.headerRows,
      headerCols: options.headerCols,
      rows: rows.map((cells) => ({ cells })),
    },
    children: [],
  } as unknown as AnyBlock;
}
