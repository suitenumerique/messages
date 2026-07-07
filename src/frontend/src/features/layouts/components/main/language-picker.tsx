import { LanguagePicker as BaseLanguagePicker, LanguagePickerProps } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useConfig } from "@/features/providers/config";
import { handle } from "@/features/utils/errors";

/**
 * @MARK: This component should be moved to the UI Kit
 */
export const LanguagePicker = (props: Pick<LanguagePickerProps, "size" | "color" | "variant" | "fullWidth" | "compact">) => {
  const { i18n } = useTranslation();
  const { LANGUAGES } = useConfig();
  const languages = LANGUAGES.map((language) => ({
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
