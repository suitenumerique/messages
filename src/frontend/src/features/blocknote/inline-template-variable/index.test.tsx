import { describe, it, expect } from 'vitest';
import { buildTemplateVariableInsertion } from './index';

describe('buildTemplateVariableInsertion', () => {
  it('seeds the token and trailing space with the provided styles', () => {
    const result = buildTemplateVariableInsertion<{}>(
      { value: 'name', label: 'Name' },
      { bold: true, textColor: 'red' },
    );

    // The token displays the human label, while value stays in props. Both the
    // token and the trailing space carry the styles, so the variable inherits
    // the surrounding formatting and text typed right after it keeps those
    // styles (the caret lands after the styled space).
    expect(result).toEqual([
      {
        type: 'template-variable',
        props: { value: 'name', label: 'Name' },
        content: [{ type: 'text', text: 'Name', styles: { bold: true, textColor: 'red' } }],
      },
      { type: 'text', text: ' ', styles: { bold: true, textColor: 'red' } },
    ]);
  });

  it('defaults to no styles when none are provided', () => {
    const result = buildTemplateVariableInsertion({ value: 'x', label: 'X' });

    expect(result).toEqual([
      {
        type: 'template-variable',
        props: { value: 'x', label: 'X' },
        content: [{ type: 'text', text: 'X', styles: {} }],
      },
      { type: 'text', text: ' ', styles: {} },
    ]);
  });
});
