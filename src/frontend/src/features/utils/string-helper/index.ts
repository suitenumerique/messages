/**
 * Helper class for string operations.
 */
export class StringHelper {
    /**
     * Lowercase a string and remove accents to ease string comparison for search purposes.
     * Also replaces common ligatures (e.g., œ → oe, æ → ae, ß → ss).
     */
    static normalizeForSearch(str: string) {
        const ligatureMap: Record<string, string> = {
            'œ': 'oe',
            'æ': 'ae',
            'ß': 'ss',
        };
        return str
            .toLowerCase()
            .replace(/[œŒæÆß]/g, match => ligatureMap[match] || match)
            .normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    }
}

export default StringHelper;
