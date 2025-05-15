import SearchFiltersMap from "@/features/i18n/search-filters-map.json";

const KEYS_FLATTEN = Object.entries(SearchFiltersMap).reduce((acc, [, keys]) => {
    Object.entries(keys).forEach(([key, values]) => {
        if (!acc[key]) {
            acc[key] = [];
        }
        acc[key] = [...acc[key], ...values];
    });
    return acc;
}, {} as Record<string, string[]>);

const KEYS_SINGLE = Object.fromEntries(Object.entries(KEYS_FLATTEN).filter(([key]) => key.includes('_')));
const KEYS_PAIR = Object.fromEntries(Object.entries(KEYS_FLATTEN).filter(([key]) => !key.includes('_')));

export class SearchHelper {
    static parseSearchQuery = (query: string): Record<string, string | boolean> => {
        const result: Record<string, string | boolean> = {};
        // A group is a string of the form from:"value" or to:"value" or text:"value" 
        const regex_keys_single = new RegExp(Object.values(KEYS_SINGLE).flat().join('|'), 'g');
        const regex_keys_pair = new RegExp(`(${Object.values(KEYS_PAIR).flat().join('|')}):"[^"]*"`, 'g');
        const single_matches = query.match(regex_keys_single);
        const pair_matches = query.match(regex_keys_pair);

        // Extract remaining text
        let rawText = query
            .replace(regex_keys_single, '')
            .replace(regex_keys_pair, '')
            .trim();
        
        // Process key-value pairs (e.g "from:value")
        pair_matches?.forEach(match => {
            const [localizedKey, value] = match.split(':');
            const key = Object.entries(KEYS_PAIR).find(([, value]) => value.includes(localizedKey))?.[0];
            if (key) result[key] = value?.replace(/"/g, '');
            else rawText = `${rawText} ${match}`;
        });
    

        // Process single value (e.g "is_unread", "in_trash")
        single_matches?.forEach(match => {
            const key = Object.entries(KEYS_SINGLE).find(([, value]) => value.includes(match))?.[0];
            if (!key) rawText = `${rawText} ${match}`;
            else if(key.startsWith('in_')) result['in'] = key.split('_')[1];
            else result[key] = true;
        });
    
        if (rawText) {
            result.text = rawText;
        }
    
        return result;
    }

    
    static serializeSearchFormData = (data: FormData, language: string): string => {
        const i18nFiltersMap = SearchFiltersMap[language as keyof typeof SearchFiltersMap] ?? SearchFiltersMap['en'];
        const isFiltersMapKey = (key: string): key is keyof typeof i18nFiltersMap => i18nFiltersMap.hasOwnProperty(key);
        
        return Array.from(data.entries()).reduce((acc, [key, value]) => {
            if (key === 'text') return acc;
            if (key.startsWith('is_')) {
                if (value !== 'true' || !isFiltersMapKey(key)) return acc;
                return `${i18nFiltersMap[key][0]} ${acc}`;
            }
            if (key === 'in') {
                const filterKey = key.concat('_', value.toString());
                if (value === 'all' ||  !isFiltersMapKey(filterKey)) return acc;
                return `${i18nFiltersMap[filterKey][0]} ${acc}`;
            }
            if ((value as string).trim() && isFiltersMapKey(key)) {
                return `${i18nFiltersMap[key][0]}:"${value}" ${acc}`;
            }
            return acc;
        }, data.get('text') as string).trim();
    }
}