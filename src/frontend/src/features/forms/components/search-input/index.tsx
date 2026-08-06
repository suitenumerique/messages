import { useLocation, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useUrlSearchParams } from "@/hooks/use-url-search-params";
import { useEffect, useState, useRef } from "react";
import { Button, Modal, ModalSize } from "@gouvfr-lasuite/cunningham-react";
import { SearchFiltersForm } from "../search-filters-form";
import { useLayoutContext } from "@/features/layouts/components/layout-context";
import { MAILBOX_FOLDERS } from "@/features/layouts/components/mailbox-panel/components/mailbox-list";
import { IconSize, useResponsive } from "@gouvfr-lasuite/ui-kit";
import { Icon } from "@/features/ui/components/icon";
import { Settings, XMark, Zoom } from "@gouvfr-lasuite/ui-kit/icons";

const SEARCH_FILTERS_FORM_ID = "search-filters-form";

type SearchInputProps = {
    /**
     * Render a single icon button instead of the search field. Used by the
     * native header, which has no room for the field: the button opens the
     * same full-screen search form the field would.
     */
    compact?: boolean;
}

export const SearchInput = ({ compact = false }: SearchInputProps) => {
    const navigate = useNavigate();
    const pathname = useLocation({ select: (l) => l.pathname });
    const { closeLeftPanel } = useLayoutContext();
    const searchParams = useUrlSearchParams();
    const [value, setValue] = useState<string>(searchParams.get('search') || '');
    const [showFilters, setShowFilters] = useState<boolean>(false);
    const { t } = useTranslation();
    const { isMobile } = useResponsive();
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

        let newSearchParams: URLSearchParams;
        if (query) newSearchParams = new URLSearchParams({'search': query});
        else newSearchParams = new URLSearchParams(MAILBOX_FOLDERS()[0].filter);

        if (submit) {
            closeLeftPanel();
            navigate({ to: pathname, search: Object.fromEntries(newSearchParams), replace: true });
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
        <Modal
            isOpen={showFilters}
            onClose={() => setShowFilters(false)}
            title={t("Search in messages...")}
            size={ModalSize.FULL}
            stickyFooter
            rightActions={
                <div className="flex-row flex-justify-end" style={{ paddingBottom: 'var(--c--globals--spacings--sm)' }}>
                    <Button type="reset" form={SEARCH_FILTERS_FORM_ID} variant="tertiary">
                        {t("Reset")}
                    </Button>
                    <Button type="submit" form={SEARCH_FILTERS_FORM_ID} variant="primary">
                        {t("Search")}
                    </Button>
                </div>
            }
        >
            {showFilters && (
                <SearchFiltersForm
                    id={SEARCH_FILTERS_FORM_ID}
                    query={value}
                    onChange={handleFiltersChange}
                    autoFocusText
                    hideFooter
                />
            )}
        </Modal>
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
                    onClick={resetInput}
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
