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
    const searchRef = useRef<HTMLDivElement>(null);

    const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
        handleSearch(event.target.value);
    }

    const handleFiltersChange = (query: string, submit: boolean = true) => {
        handleSearch(query, submit);
        if (submit) setShowFilters(false);
    }

    /**
     * Each time the user types, we update the URL with the new search query.
     */
    const handleSearch = (query: string, submit: boolean = false) => {
        setValue(query);
        
        const url = new URL(router.asPath, 'http://localhost');
        if (query) {
            url.searchParams.set('search', query);
        } else {
            url.searchParams.delete('search');
        }

        if (submit) {
            router.replace(url.pathname + url.search, undefined, { shallow: true });
        }
    }

    const handleKeyPress = (event: React.KeyboardEvent<HTMLInputElement>) => {
        if (event.key === 'Escape') setShowFilters(false);
        else if (event.key === 'Enter') handleFiltersChange(value, true);
        else setShowFilters(true);
    }

    const resetInput = () => {
        handleFiltersChange('', true);
    }

    /**
     * Each time the URL changes, we update the search query
     * except when the user is typing to prevent the cursor from jumping
     * to the end of the input.
     */
    useEffect(() => {
        setValue(searchParams.get('search') || '');
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
                { value && (
                <Button
                    color="tertiary-text"
                    onClick={resetInput}
                    title={t("search.filters.reset")}
                >
                    <span className="material-icons">close</span>
                    <span className="c__offscreen">{t("search.filters.reset")}</span>
                </Button>
                )}
                <Button
                    color="tertiary-text"
                    onClick={() => setShowFilters(!showFilters)}
                    title={showFilters ? t("search.filters.close") : t("search.filters.open")}
                >
                    <span className="material-icons">tune</span>
                    <span className="c__offscreen">{showFilters ? t("search.filters.close") : t("search.filters.open")}</span>
                </Button>
            </div>
            {showFilters && <SearchFiltersForm query={value} onChange={handleFiltersChange} />}
        </div>
    );
}