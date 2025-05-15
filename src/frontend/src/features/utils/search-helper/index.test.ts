import { SearchHelper } from './index';

describe('SearchHelper', () => {
    describe('parseSearchQuery', () => {
        it('should parse a simple text query', () => {
            const result = SearchHelper.parseSearchQuery('hello world');
            expect(result).toEqual({ text: 'hello world' });
        });

        it('should parse key-value pairs', () => {
            const result = SearchHelper.parseSearchQuery('from:"john@example.com" to:"jane@example.com"');
            expect(result).toEqual({
                from: 'john@example.com',
                to: 'jane@example.com'
            });
        });

        it('should parse single value filters', () => {
            const result = SearchHelper.parseSearchQuery('is:unread in:trash');
            expect(result).toEqual({
                is_unread: true,
                in: 'trash'
            });
        });

        it('should handle mixed queries', () => {
            const result = SearchHelper.parseSearchQuery('from:"john@example.com" is:unread hello world');
            expect(result).toEqual({
                from: 'john@example.com',
                is_unread: true,
                text: 'hello world'
            });
        });

        it('should handle unknown filters as text', () => {
            const result = SearchHelper.parseSearchQuery('unknown:"value" is:unread');
            expect(result).toEqual({
                is_unread: true,
                text: 'unknown:"value"'
            });
        });
    });

    describe('serializeSearchFormData', () => {
        it('should serialize simple text', () => {
            const formData = new FormData();
            formData.append('text', 'hello world');
            const result = SearchHelper.serializeSearchFormData(formData, 'en');
            expect(result).toBe('hello world');
        });

        it('should serialize key-value pairs', () => {
            const formData = new FormData();
            formData.append('from', 'john@example.com');
            formData.append('to', 'jane@example.com');
            const result = SearchHelper.serializeSearchFormData(formData, 'en');
            expect(result).toContain('from:"john@example.com"');
            expect(result).toContain('to:"jane@example.com"');
        });

        it('should serialize boolean filters', () => {
            const formData = new FormData();
            formData.append('is_unread', 'true');
            const result = SearchHelper.serializeSearchFormData(formData, 'en');
            expect(result).toContain('is:unread');
        });

        it('should serialize folder filters', () => {
            const formData = new FormData();
            formData.append('in', 'trash');
            const result = SearchHelper.serializeSearchFormData(formData, 'en');
            expect(result).toContain('in:trash');
        });

        it('should handle empty values', () => {
            const formData = new FormData();
            formData.append('from', '');
            formData.append('text', 'hello');
            const result = SearchHelper.serializeSearchFormData(formData, 'en');
            expect(result).toBe('hello');
        });

        it('should handle different languages', () => {
            const formData = new FormData();
            formData.append('from', 'john@example.com');
            formData.append('text', 'hello');
            
            // Test with English
            const enResult = SearchHelper.serializeSearchFormData(formData, 'en');
            expect(enResult).toContain('from:"john@example.com"');
            
            // Test with French (assuming it exists in SearchFiltersMap)
            const frResult = SearchHelper.serializeSearchFormData(formData, 'fr');
            expect(frResult).toContain('de:"john@example.com"');
        });

        it('should fallback to english if unknown language', () => {
            const formData = new FormData();
            formData.append('from', 'john@example.com');
            formData.append('text', 'hello');
            
            // Test with English
            const enResult = SearchHelper.serializeSearchFormData(formData, 'de');
            expect(enResult).toContain('from:"john@example.com"');
        });
    });
}); 