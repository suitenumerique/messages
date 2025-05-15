import { useRouter } from "next/router";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "next/navigation";
import { useEffect, useState, useRef } from "react";
import { Button } from "@openfun/cunningham-react";
import { SearchFiltersForm } from "../search-filters-form";

export const SearchInput = () => {
    const router = useRouter();
    const searchParams = useSearchParams();
    const [value, setValue] = useState<string>(searchParams.get('search') || '');
    const [showFilters, setShowFilters] = useState<boolean>(false);
    const { t } = useTranslation();
    const isUserTyping = useRef(false);
    const searchRef = useRef<HTMLDivElement>(null);

    const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
        handleSearch(event.target.value);
    }

    const handleFiltersChange = (query: string, closeFilters: boolean = true) => {
        handleSearch(query);
        if (closeFilters) setShowFilters(false);
    }

    /**
     * Each time the user types, we update the URL with the new search query.
     */
    const handleSearch = (query: string) => {
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

    const handleKeyPress = (event: React.KeyboardEvent<HTMLInputElement>) => {
        if (event.key === 'Escape') setShowFilters(false);
        else if (event.key === 'Enter') handleFiltersChange(value, true);
        else setShowFilters(true);
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

    // Add click outside handler
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (!searchRef.current?.contains(event.target as Node)) {
                setShowFilters(false);
            }
        };

        document.addEventListener('mousedown', handleClickOutside);
        return () => {
            document.removeEventListener('mousedown', handleClickOutside);
        };
    }, []);

    return (
        <div className="search" ref={searchRef}>
            <div className="search__container">
                <div className="search__input-container">
                    <label className="search__label" htmlFor="search">
                        <span className="material-icons">search</span>
                        <span className="c__offscreen">{t("search.placeholder")}</span>
                    </label>
                    <input
                        className="search__input"
                        id="search"
                        type="search"
                        value={value}
                        onChange={handleChange}
                        onFocus={() => setShowFilters(true)}
                        onKeyDown={handleKeyPress}
                        placeholder={t("search.placeholder")}
                    />
                </div>
                <Button color="tertiary-text" className="search__filters-toggle" onClick={() => setShowFilters(!showFilters)}>
                    <span className="material-icons">tune</span>
                    <span className="c__offscreen">{t("search.filters")}</span>
                </Button>
            </div>
            {showFilters && <SearchFiltersForm query={value} onChange={handleFiltersChange} />}
        </div>
    );
}