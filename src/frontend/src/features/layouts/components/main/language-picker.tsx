import { LanguagePicker as BaseLanguagePicker, LanguagePickerProps } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";

/**
 * @MARK: Those languages should be retrieved from the backend through conf API
 * Furthermore, this component should be moved to the UI Kit
 */
export const LanguagePicker = (props: Pick<LanguagePickerProps, "size" | "color" | "fullWidth">) => {
  const { i18n } = useTranslation();
  const languages = [
    { value: "fr-FR", label: "Français", isChecked: i18n.language === "fr-FR" },
    { value: "en-US", label: "English", isChecked: i18n.language === "en-US" }
  ]

  return (
    <BaseLanguagePicker
      languages={languages}
      onChange={(value) => {
        i18n.changeLanguage(value).catch((err) => {
          console.error("Error changing language", err);
        });
      }}
      {...props}
    />
  )
}
