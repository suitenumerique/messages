import type { Block } from '@blocknote/core';
import { resolveTemplateVariables } from './utils';
import {
  bulletListItem,
  divider,
  image,
  paragraph,
  styledText,
  templateVariable,
} from './__tests__/block-factories';

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
