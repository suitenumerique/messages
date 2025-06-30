import { describe, it, expect } from 'vitest';
import { StringHelper } from './index';

describe('StringHelper', () => {
  describe('normalizeForSearch', () => {
    it('should lowercase a string', () => {
      expect(StringHelper.normalizeForSearch('HELLO WORLD')).toBe('hello world');
      expect(StringHelper.normalizeForSearch('MiXeD cAsE')).toBe('mixed case');
    });

    it('should remove accents from characters', () => {
      expect(StringHelper.normalizeForSearch('été')).toBe('ete');
      expect(StringHelper.normalizeForSearch('naïve')).toBe('naive');
      expect(StringHelper.normalizeForSearch('café')).toBe('cafe');
      expect(StringHelper.normalizeForSearch('über')).toBe('uber');
      expect(StringHelper.normalizeForSearch('ñandú')).toBe('nandu');
    });

    it('should handle mixed case and accents together', () => {
      expect(StringHelper.normalizeForSearch('ÉTÉ Naïve')).toBe('ete naive');
      expect(StringHelper.normalizeForSearch('Café Über')).toBe('cafe uber');
    });

    it('should handle empty string', () => {
      expect(StringHelper.normalizeForSearch('')).toBe('');
    });

    it('should handle string with only spaces', () => {
      expect(StringHelper.normalizeForSearch('   ')).toBe('   ');
    });

    it('should handle string with numbers and special characters', () => {
      expect(StringHelper.normalizeForSearch('Hello123!@#')).toBe('hello123!@#');
      expect(StringHelper.normalizeForSearch('Test-Email@domain.com')).toBe('test-email@domain.com');
    });

    it('should handle string with multiple consecutive accents', () => {
      expect(StringHelper.normalizeForSearch('cœur')).toBe('coeur');
      expect(StringHelper.normalizeForSearch('FELINÆ')).toBe('felinae');
      expect(StringHelper.normalizeForSearch('naïve')).toBe('naive');
    });

    it('should handle various European characters', () => {
      expect(StringHelper.normalizeForSearch('àáâãäå')).toBe('aaaaaa');
      expect(StringHelper.normalizeForSearch('èéêë')).toBe('eeee');
      expect(StringHelper.normalizeForSearch('ìíîï')).toBe('iiii');
      expect(StringHelper.normalizeForSearch('òóôõö')).toBe('ooooo');
      expect(StringHelper.normalizeForSearch('ùúûü')).toBe('uuuu');
      expect(StringHelper.normalizeForSearch('ýÿ')).toBe('yy');
      expect(StringHelper.normalizeForSearch('ç')).toBe('c');
    });
  });
});
