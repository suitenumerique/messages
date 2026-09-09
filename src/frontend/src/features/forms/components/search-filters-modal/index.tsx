import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Modal, ModalSize } from "@gouvfr-lasuite/cunningham-react";
import { SearchFiltersForm } from "../search-filters-form";

const SEARCH_FILTERS_FORM_ID = "search-filters-form";

type SearchFiltersModalProps = {
    isOpen: boolean;
    onClose: () => void;
    /** Search currently applied; the form starts from it each time it opens. */
    query: string;
    onSubmit: (query: string) => void;
}

/**
 * Full-screen search form for touch viewports and the native app, where the
 * filters cannot drop down from the header field.
 */
export const SearchFiltersModal = ({ isOpen, onClose, query, onSubmit }: SearchFiltersModalProps) => {
    const { t } = useTranslation();

    return (
        <Modal
            isOpen={isOpen}
            onClose={onClose}
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
            {isOpen && <SearchFiltersDraft query={query} onSubmit={onSubmit} />}
        </Modal>
    );
};

type SearchFiltersDraftProps = Pick<SearchFiltersModalProps, "query" | "onSubmit">;

/**
 * Holds the draft the form edits: its fields are controlled from the
 * serialized query, so every change has to round-trip through a state the
 * form does not own. Mounted only while the modal is open, so reopening
 * discards an unsubmitted draft in favour of the applied search.
 */
const SearchFiltersDraft = ({ query, onSubmit }: SearchFiltersDraftProps) => {
    const [draft, setDraft] = useState(query);

    const handleChange = (nextQuery: string, submit: boolean) => {
        setDraft(nextQuery);
        if (submit) onSubmit(nextQuery);
    };

    return (
        <SearchFiltersForm
            id={SEARCH_FILTERS_FORM_ID}
            query={draft}
            onChange={handleChange}
            autoFocusText
            hideFooter
        />
    );
};
