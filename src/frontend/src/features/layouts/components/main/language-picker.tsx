import { LanguagePicker as BaseLanguagePicker, LanguagePickerProps } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { LANGUAGES } from "@/features/i18n/conf";
import { handle } from "@/features/utils/errors";

/**
 * @MARK: Those languages should be retrieved from the backend through conf API
 * Furthermore, this component should be moved to the UI Kit
 */
export const LanguagePicker = (props: Pick<LanguagePickerProps, "size" | "color" | "fullWidth">) => {
  const { i18n } = useTranslation();
  const languages = LANGUAGES.map((language: [string, string]) => ({
    value: language[0],
    label: language[1],
    isChecked: i18n.language === language[0]
  }));

  return (
    <BaseLanguagePicker
      languages={languages}
      onChange={(value) => {
        i18n.changeLanguage(value).catch((error) => {
          handle(new Error("Error changing language."), { extra: { error, value } });
        });
      }}
      {...props}
    />
  )
}
