import { MAILBOX_FOLDERS } from "@/features/layouts/components/mailbox-panel/components/mailbox-list";
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
        const query = SearchHelper.serializeSearchFormData(formData, i18n.resolvedLanguage);
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
                label={t("From")}
                value={parsedQuery.from as string}
                fullWidth
            />
            <Input
                name="to"
                label={t("To")}
                value={parsedQuery.to as string}
                fullWidth
            />
            <Input
                name="subject"
                label={t("Subject")}
                value={parsedQuery.subject as string}
                fullWidth
            />
            <Input
                name="text"
                label={t("Contains the words")}
                value={parsedQuery.text as string}
                fullWidth
            />
            <Select
                name="in"
                label={t("In")}
                value={parsedQuery.in as string ?? 'all_messages'}
                showLabelWhenSelected={false}
                onChange={handleChange}
                options={MAILBOX_FOLDERS().filter((folder) => folder.searchable).map((folder) => ({
                    label: t(folder.name),
                    render: () => <FolderOption label={t(folder.name)} icon={folder.icon} />,
                    value: folder.id
                }))}
                clearable={false}
                fullWidth
            />
            <div className="flex-row flex-align-center" style={{ gap: 'var(--c--theme--spacings--2xs)' }}>
                <Label>{t("Read state")} :</Label>
                <Checkbox label={t("Read")} value="true" name="is_read" checked={Boolean(parsedQuery.is_read)} onChange={handleReadStateChange} />
                <Checkbox label={t("Unread")} value="true" name="is_unread" checked={Boolean(parsedQuery.is_unread)} onChange={handleReadStateChange} />
            </div>
            <footer className="search__filters-footer">
                <Button type="reset" color="tertiary-text" onClick={handleReset}>
                    {t("Reset")}
                </Button>
                <Button type="submit" color="tertiary">
                    {t("Search")}
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
