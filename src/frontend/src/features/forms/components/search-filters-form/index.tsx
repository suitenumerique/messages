import { SearchHelper } from "@/features/utils/search-helper";
import { Label } from "@gouvfr-lasuite/ui-kit";
import { Button, Checkbox, Input, Select } from "@openfun/cunningham-react";
import { useRef } from "react";
import { useTranslation } from "react-i18next";

type SearchFiltersFormProps = {
    query: string;
    onChange: (query: string, submit: boolean) => void;
}

export const SearchFiltersForm = ({ query, onChange }: SearchFiltersFormProps) => {
    const { t, i18n } = useTranslation();
    const formRef = useRef<HTMLFormElement>(null);

    const updateQuery = (submit: boolean) => {
        const formData = new FormData(formRef.current as HTMLFormElement);
        const query = SearchHelper.serializeSearchFormData(formData, i18n.language);
        onChange(query, submit);
        formRef.current?.reset();
    }

    const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => updateQuery(event.type === 'submit');
    const handleChange = () => updateQuery(false);

    const handleReset = () => {
        onChange('', false);
        formRef.current?.reset();
    }

    const parsedQuery = SearchHelper.parseSearchQuery(query);

    const handleReadStateChange = (event: React.ChangeEvent<HTMLInputElement>) => {
        const { name, checked } = event.target;
        if (checked) {
            const checkboxToUncheck = formRef.current?.elements.namedItem(name === "is_read" ? "is_unread" : "is_read") as HTMLInputElement;
            if (checkboxToUncheck) {
                checkboxToUncheck.checked = false;
            }
        }
    }

    return (
        <form className="search__filters" ref={formRef} onSubmit={handleSubmit} onChange={handleChange}>
            <Input
                name="from"
                label={t("search.filters.label.from")}
                value={parsedQuery.from as string}
                fullWidth
            />
            <Input
                name="to"
                label={t("search.filters.label.to")}
                value={parsedQuery.to as string}
                fullWidth
            />
            <Input
                name="subject"
                label={t("search.filters.label.subject")}
                value={parsedQuery.subject as string}
                fullWidth
            />
            <Input
                name="text"
                label={t("search.filters.label.text")}
                value={parsedQuery.text as string}
                fullWidth
            />
            <Select
                name="in"
                label={t("search.filters.label.in")}
                value={parsedQuery.in as string ?? 'all'}
                showLabelWhenSelected={false}
                onChange={handleChange}
                options={[
                    {
                        label: t("search.filters.label.folder_all_messages"),
                        render: () => <FolderOption label={t("search.filters.label.folder_all_messages")} icon="folder" />,
                        value: 'all'
                    },
                    {
                        label: t("search.filters.label.folder_drafts"),
                        render: () => <FolderOption label={t("search.filters.label.folder_drafts")} icon="drafts" />,
                        value: "draft"
                    },
                    {
                        label: t("search.filters.label.folder_sent"),
                        render: () => <FolderOption label={t("search.filters.label.folder_sent")} icon="outbox" />,
                        value: "sent"
                    },
                    {
                        label: t("search.filters.label.folder_trash"),
                        render: () => <FolderOption label={t("search.filters.label.folder_trash")} icon="delete" />,
                        value: "trash" },
                ]}
                clearable={false}
                fullWidth
            />
            <div className="flex-row flex-align-center" style={{ gap: 'var(--c--theme--spacings--2xs)' }}>
                <Label>{t("search.filters.label.read_state")} :</Label>
                <Checkbox label={t("search.filters.label.read")} value="true" name="is_read" checked={Boolean(parsedQuery.is_read)} onChange={handleReadStateChange} />
                <Checkbox label={t("search.filters.label.unread")} value="true" name="is_unread" checked={Boolean(parsedQuery.is_unread)} onChange={handleReadStateChange} />
            </div>
            <footer className="search__filters-footer">
                <Button type="reset" color="tertiary-text" onClick={handleReset}>
                    {t("search.filters.reset")}
                </Button>
                <Button type="submit" color="tertiary">
                    {t("search.filters.submit")}
                </Button>
            </footer>
        </form>
    );
};

type FolderOptionProps = {
    label: string;
    icon: string;
}

const FolderOption = ({ label, icon }: FolderOptionProps) => {
    return (
        <div className="search__filters-folder-option">
            <span className="material-icons">{icon}</span>
            {label}
        </div>
    );
}
