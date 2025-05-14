import { useRouter } from "next/router";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "next/navigation";
import { useEffect, useState, useRef } from "react";

export const SearchInput = () => {
    const router = useRouter();
    const searchParams = useSearchParams();
    const [value, setValue] = useState<string>(searchParams.get('search') || '');
    const { t } = useTranslation();
    const isUserTyping = useRef(false);

    /**
     * Each time the user types, we update the URL with the new search query.
     */
    const handleSearch = (event: React.ChangeEvent<HTMLInputElement>) => {
        const query = event.target.value;
        isUserTyping.current = true;
        setValue(query);
        
        const url = new URL(router.asPath, 'http://localhost');
        if (query) {
            url.searchParams.set('search', query);
        } else {
            url.searchParams.delete('search');
        }

        router.replace(url.pathname + url.search, undefined, { shallow: true });
    }

    /**
     * Each time the URL changes, we update the search query
     * except when the user is typing to prevent the cursor from jumping
     * to the end of the input.
     */
    useEffect(() => {
        // Only update the value from searchParams if the user is not currently typing
        if (!isUserTyping.current) {
            setValue(searchParams.get('search') || '');
        }
        isUserTyping.current = false;
    }, [searchParams]);

    return (
        <div className="search">
        <label className="search__label" htmlFor="search">
            <span className="material-icons">search</span>
            <span className="c__offscreen">{t("search.placeholder")}</span>
        </label>
        <input
            className="search__input"
            id="search"
            type="search"
            value={value}
            onChange={handleSearch}
            placeholder={t("search.placeholder")}
        />
        </div>
    );
}