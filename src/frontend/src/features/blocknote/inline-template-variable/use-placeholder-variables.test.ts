import { describe, it, expect } from 'vitest';
import type { TFunction } from 'i18next';
import { resolvePlaceholderLabel } from './use-placeholder-variables';

// Minimal TFunction stub: looks the key up in `dict`, otherwise honours the
// `defaultValue` option (which the resolver always passes). Only the
// `(key, { defaultValue })` shape used by resolvePlaceholderLabel is exercised.
const makeT = (dict: Record<string, string> = {}): TFunction =>
  ((key: string, opts?: { defaultValue?: string }) =>
    dict[key] ?? opts?.defaultValue ?? key) as unknown as TFunction;

describe('resolvePlaceholderLabel', () => {
  describe('built-in fields (empty meta, localized client-side)', () => {
    it('translates the slug from the "placeholders" namespace', () => {
      const t = makeT({ name: "Nom d'expéditeur" });
      expect(resolvePlaceholderLabel('name', {}, t, 'fr-FR')).toBe(
        "Nom d'expéditeur",
      );
    });

    it('falls back to the slug when the namespace has no entry', () => {
      expect(resolvePlaceholderLabel('name', {}, makeT(), 'fr-FR')).toBe('name');
    });
  });

  describe('custom fields without translations', () => {
    it('uses the schema title and never consults the i18next namespace', () => {
      // The `t` map would return "FROM_NAMESPACE" — proving it is not used here.
      const t = makeT({ job_title: 'FROM_NAMESPACE' });
      expect(
        resolvePlaceholderLabel('job_title', { title: 'Job title' }, t, 'fr-FR'),
      ).toBe('Job title');
    });
  });

  describe('custom fields with x-i18n translations', () => {
    const i18n = { 'fr-FR': 'Fonction régionale', fr: 'Fonction', en: 'Job title' };

    it('prefers the exact regional language when present', () => {
      expect(
        resolvePlaceholderLabel('job_title', { i18n }, makeT(), 'fr-FR'),
      ).toBe('Fonction régionale');
    });

    it('falls back from the regional code to the base language', () => {
      expect(
        resolvePlaceholderLabel(
          'job_title',
          { i18n: { fr: 'Fonction', en: 'Job title' } },
          makeT(),
          'fr-FR',
        ),
      ).toBe('Fonction');
    });

    it('falls back to English when the active language is missing', () => {
      expect(
        resolvePlaceholderLabel(
          'job_title',
          { i18n: { fr: 'Fonction', en: 'Job title' } },
          makeT(),
          'de-DE',
        ),
      ).toBe('Job title');
    });

    it('prefers a translation over the title when both exist', () => {
      expect(
        resolvePlaceholderLabel(
          'job_title',
          { title: 'Title', i18n: { en: 'Translated' } },
          makeT(),
          'en-US',
        ),
      ).toBe('Translated');
    });

    it('falls back to the title when no translation matches', () => {
      expect(
        resolvePlaceholderLabel(
          'job_title',
          { title: 'Default title', i18n: { fr: 'Fonction' } },
          makeT(),
          'de-DE',
        ),
      ).toBe('Default title');
    });

    it('falls back to the slug when neither a matching translation nor a title exist', () => {
      expect(
        resolvePlaceholderLabel(
          'job_title',
          { i18n: { fr: 'Fonction' } },
          makeT(),
          'de-DE',
        ),
      ).toBe('job_title');
    });

    it('treats an empty i18n object as "no match" and uses the title', () => {
      // An empty object is still truthy, so the resolver enters the i18n branch
      // and must degrade gracefully to the title.
      expect(
        resolvePlaceholderLabel('job_title', { title: 'T', i18n: {} }, makeT(), 'fr'),
      ).toBe('T');
    });
  });
});
