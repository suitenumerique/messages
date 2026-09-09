import { useTranslation } from "react-i18next";
import { useEffect, useState, useRef } from "react";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { SearchFiltersForm } from "../search-filters-form";
import { SearchFiltersModal } from "../search-filters-modal";
import { useSearchQuery } from "./use-search-query";
import { IconSize, useResponsive } from "@gouvfr-lasuite/ui-kit";
import { Icon } from "@/features/ui/components/icon";
import { Settings, XMark, Zoom } from "@gouvfr-lasuite/ui-kit/icons";

type SearchInputProps = {
    /**
     * Render a single icon button instead of the search field. Used by the
     * native header, which has no room for the field: the button opens the
     * same full-screen search form the field would.
     */
    compact?: boolean;
}

export const SearchInput = ({ compact = false }: SearchInputProps) => {
    const { query, submit } = useSearchQuery();
    const [value, setValue] = useState<string>(query);
    const [syncedQuery, setSyncedQuery] = useState<string>(query);
    const [showFilters, setShowFilters] = useState<boolean>(false);
    const { t } = useTranslation();
    const { isMobile } = useResponsive();
    const searchRef = useRef<HTMLDivElement>(null);

    const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
        setValue(event.target.value);
    }

    const handleSubmit = (nextQuery: string) => {
        setValue(nextQuery);
        setShowFilters(false);
        submit(nextQuery);
    }

    const handleFiltersChange = (nextQuery: string, shouldSubmit: boolean) => {
        if (shouldSubmit) handleSubmit(nextQuery);
        else setValue(nextQuery);
    }

    const handleKeyPress = (event: React.KeyboardEvent<HTMLInputElement>) => {
        if (event.key === 'Escape') setShowFilters(false);
        else if (event.key === 'Enter') handleSubmit(value);
        else setShowFilters(true);
    }

    // Follow the URL when it changes (navigation, reset), but only then: while
    // the user types, the field keeps its own draft.
    if (query !== syncedQuery) {
        setSyncedQuery(query);
        setValue(query);
    }

    // Add click outside handler (desktop only: the fullscreen modal handles its
    // own dismissal and renders outside searchRef).
    useEffect(() => {
        if (isMobile || compact) return;
        const handleClickOutside = (event: MouseEvent) => {
            if (!searchRef.current?.contains(event.target as Node)) {
                setShowFilters(false);
            }
        };

        document.addEventListener('mousedown', handleClickOutside);
        return () => {
            document.removeEventListener('mousedown', handleClickOutside);
        };
    }, [isMobile, compact]);

    // Touch viewports (and the native trigger) open the filters as a full-screen
    // form rather than a dropdown anchored to the field.
    const filtersModal = (
        <SearchFiltersModal
            isOpen={showFilters}
            onClose={() => setShowFilters(false)}
            query={query}
            onSubmit={handleSubmit}
        />
    );

    if (compact) {
        return (
            <>
                <Button
                    className="search__trigger"
                    color="neutral"
                    variant="tertiary"
                    size="medium"
                    onClick={() => setShowFilters(true)}
                    icon={<Icon icon={Zoom} />}
                    aria-label={t("Search in messages...")}
                />
                {filtersModal}
            </>
        );
    }

    return (
        <div className="search" ref={searchRef}>
            <div className="search__container">
                <div className="search__input-container">
                    <label className="search__label" htmlFor="search">
                        <Icon icon={Zoom} size={18} />
                        <span className="c__offscreen">{t("Search in messages...")}</span>
                    </label>
                    <input
                        className="search__input"
                        id="search"
                        type="search"
                        value={value}
                        onChange={handleChange}
                        onFocus={() => setShowFilters(true)}
                        onClick={isMobile ? () => setShowFilters(true) : undefined}
                        onKeyDown={handleKeyPress}
                        placeholder={t("Search in messages...")}
                        readOnly={isMobile}
                    />
                </div>
                {value && (
                <Button
                    color="neutral"
                    variant="tertiary"
                    onClick={() => handleSubmit('')}
                    title={t("Reset")}
                    size="small"
                    icon={<Icon icon={XMark} size={IconSize.MEDIUM} />}
                    aria-label={t("Reset")}
                />
                )}
                {!isMobile && (
                <Button
                    color="neutral"
                    variant="tertiary"
                    onClick={() => setShowFilters(!showFilters)}
                    title={showFilters ? t("Close filters") : t("Open filters")}
                    size="small"
                    icon={<Icon icon={Settings} size={IconSize.MEDIUM} />}
                    aria-label={showFilters ? t("Close filters") : t("Open filters")}
                />
                )}
            </div>
            {isMobile ? filtersModal : (
                showFilters && <SearchFiltersForm query={value} onChange={handleFiltersChange} />
            )}
        </div>
    );
}
