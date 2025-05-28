import { DropdownMenu } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@openfun/cunningham-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

/**
 * @MARK: Those languages should be retrieved from the backend through conf API
 * Furthermore, this component should be moved to the UI Kit
 */
export const LanguagePicker = () => {
    const [isOpen, setIsOpen] = useState(false);
    const { i18n } = useTranslation();
    const [selectedValues, setSelectedValues] = useState([i18n.language]);
    const languages = [
        { label: "Français", value: "fr" },
        { label: "English", value: "en" },
    ];

    return (
      <DropdownMenu
        options={languages}
        isOpen={isOpen}
        onOpenChange={setIsOpen}
        onSelectValue={(value) => {
          setSelectedValues([value]);
          i18n.changeLanguage(value).catch((err) => {
            console.error("Error changing language", err);
          });
        }}
        selectedValues={selectedValues}
      >
        <Button
          onClick={() => setIsOpen(!isOpen)}
          color="primary-text"
          className="c__language-picker"
          icon={
            <span className="material-icons">
              {isOpen ? "arrow_drop_up" : "arrow_drop_down"}
            </span>
          }
          iconPosition="right"
        >
          <span className="material-icons">translate</span>
          <span className="c__language-picker__label">
            {languages.find((lang) => lang.value === selectedValues[0])?.label}
          </span>
        </Button>
      </DropdownMenu>
    );
}
  