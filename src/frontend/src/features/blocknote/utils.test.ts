import type { Block } from '@blocknote/core';
import { backfillTemplateVariableContent, resolveTemplateVariables } from './utils';
import {
  AnyInlineContent,
  bulletListItem,
  divider,
  image,
  paragraph,
  styledText,
  templateVariable,
} from './__tests__/block-factories';

// Legacy `template-variable` token as persisted before the inline spec
// switched to `content: "styled"`: props are filled but `content` is absent.
const legacyTemplateVariable = (
  value: string,
  label?: string,
): AnyInlineContent =>
  ({
    type: 'template-variable',
    props: label === undefined ? { value } : { value, label },
  }) as unknown as AnyInlineContent;

// resolveTemplateVariables is typed against the public Block schema; the
// factories return loosely-typed blocks, so we cast at the boundary.
const asBlocks = (blocks: unknown[]): Block[] => blocks as Block[];

describe('resolveTemplateVariables', () => {
  it('replaces a single template-variable with its resolved value', () => {
    const blocks = asBlocks([
      paragraph([
        styledText('Hello '),
        templateVariable('first_name'),
      ]),
    ]);

    const resolved = resolveTemplateVariables(blocks, { first_name: 'Alice' });

    const [block] = resolved;
    const content = block.content as { type: string; text: string }[];
    expect(content).toHaveLength(2);
    expect(content[1]).toEqual({ type: 'text', text: 'Alice', styles: {} });
  });

  it('falls back to `{var}` when the value is missing from resolvedValues', () => {
    const blocks = asBlocks([
      paragraph([templateVariable('missing_var')]),
    ]);

    const resolved = resolveTemplateVariables(blocks, {});

    const content = resolved[0].content as { type: string; text: string }[];
    expect(content[0].text).toBe('{missing_var}');
  });

  it('returns blocks unchanged when there are no template-variables', () => {
    const blocks = asBlocks([
      paragraph('Plain paragraph'),
      paragraph([styledText('Bold', { bold: true })]),
    ]);

    const resolved = resolveTemplateVariables(blocks, { unused: 'value' });

    expect(resolved).toHaveLength(2);
    expect(resolved[0].content).toEqual(blocks[0].content);
    expect(resolved[1].content).toEqual(blocks[1].content);
  });

  it('preserves blocks without a `content` array (divider, image)', () => {
    const blocks = asBlocks([divider(), image('https://example.com/a.png')]);

    const resolved = resolveTemplateVariables(blocks, {});

    expect(resolved[0].type).toBe('divider');
    expect(resolved[0].content).toBeUndefined();
    expect(resolved[1].type).toBe('image');
    expect(resolved[1].content).toBeUndefined();
  });

  it('recurses into children blocks', () => {
    const blocks = asBlocks([
      bulletListItem('Parent', {}, [
        bulletListItem([templateVariable('nested')]),
      ]),
    ]);

    const resolved = resolveTemplateVariables(blocks, { nested: 'CHILD' });

    const child = resolved[0].children[0];
    const childContent = child.content as { type: string; text: string }[];
    expect(childContent[0].text).toBe('CHILD');
  });

  it('replaces multiple template-variables within a single block', () => {
    const blocks = asBlocks([
      paragraph([
        styledText('Hi '),
        templateVariable('first_name'),
        styledText(', your code is '),
        templateVariable('code'),
        styledText('.'),
      ]),
    ]);

    const resolved = resolveTemplateVariables(blocks, {
      first_name: 'Bob',
      code: '1234',
    });

    const content = resolved[0].content as { type: string; text: string }[];
    expect(content).toHaveLength(5);
    expect(content[1].text).toBe('Bob');
    expect(content[3].text).toBe('1234');
  });

  it('preserves the `styles` object on neighbouring text', () => {
    const blocks = asBlocks([
      paragraph([
        styledText('Bold prefix ', { bold: true }),
        templateVariable('name'),
      ]),
    ]);

    const resolved = resolveTemplateVariables(blocks, { name: 'Carol' });

    const content = resolved[0].content as { type: string; text: string; styles: Record<string, unknown> }[];
    expect(content[0].styles).toEqual({ bold: true });
    expect(content[1].styles).toEqual({});
  });

  it('carries the styles applied to the variable onto the resolved text', () => {
    const blocks = asBlocks([
      paragraph([
        templateVariable('name', 'Name', { bold: true, textColor: 'red' }),
      ]),
    ]);

    const resolved = resolveTemplateVariables(blocks, { name: 'Carol' });

    const content = resolved[0].content as { type: string; text: string; styles: Record<string, unknown> }[];
    expect(content[0]).toEqual({
      type: 'text',
      text: 'Carol',
      styles: { bold: true, textColor: 'red' },
    });
  });

  it('does not mutate the input blocks', () => {
    const blocks = asBlocks([
      paragraph([
        styledText('Hello '),
        templateVariable('name'),
      ]),
    ]);
    const snapshot = JSON.parse(JSON.stringify(blocks));

    resolveTemplateVariables(blocks, { name: 'Dave' });

    expect(blocks).toEqual(snapshot);
  });
});

describe('backfillTemplateVariableContent', () => {
  it('seeds the styled content of a legacy token from its label', () => {
    const blocks = [
      paragraph([
        styledText('Hello '),
        legacyTemplateVariable('name', 'Sender name'),
      ]) as unknown as Record<string, unknown>,
    ];

    const [block] = backfillTemplateVariableContent(blocks);
    const content = block.content as { content: { type: string; text: string }[] }[];

    expect(content[1].content).toEqual([
      { type: 'text', text: 'Sender name', styles: {} },
    ]);
  });

  it('falls back to the slug when the label is missing', () => {
    const blocks = [
      paragraph([legacyTemplateVariable('user_name')]) as unknown as Record<string, unknown>,
    ];

    const [block] = backfillTemplateVariableContent(blocks);
    const content = block.content as { content: { text: string }[] }[];

    expect(content[0].content[0].text).toBe('user_name');
  });

  it('leaves already-populated tokens untouched', () => {
    const blocks = [
      paragraph([templateVariable('name', 'Sender name')]) as unknown as Record<string, unknown>,
    ];
    const snapshot = JSON.parse(JSON.stringify(blocks));

    const result = backfillTemplateVariableContent(blocks);

    expect(result[0].content).toEqual(snapshot[0].content);
  });

  it('recurses into children blocks', () => {
    const blocks = [
      bulletListItem('Parent', {}, [
        bulletListItem([legacyTemplateVariable('nested', 'Nested')]),
      ]) as unknown as Record<string, unknown>,
    ];

    const result = backfillTemplateVariableContent(blocks);
    const child = (result[0].children as Record<string, unknown>[])[0];
    const childContent = child.content as { content: { text: string }[] }[];

    expect(childContent[0].content[0].text).toBe('Nested');
  });

  it('preserves blocks without a content array', () => {
    const blocks = [
      divider() as unknown as Record<string, unknown>,
      image('https://example.com/a.png') as unknown as Record<string, unknown>,
    ];

    const result = backfillTemplateVariableContent(blocks);

    expect(result[0].type).toBe('divider');
    expect(result[1].type).toBe('image');
  });
});
