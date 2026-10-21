import { useEffect, useState, type FormEvent } from "react";
import Alert from "@codegouvfr/react-dsfr/Alert";
import Button from "@codegouvfr/react-dsfr/Button";
import Input from "@codegouvfr/react-dsfr/Input";
import Select from "@codegouvfr/react-dsfr/Select";
import { ApiError, type Api, type UserRepresentation } from "../api";
import type { KcContext } from "../KcContext";
import { getLang, kcMsg, t } from "../i18n";
import { getBasename } from "../router";

const LOCALE_LABELS: Record<string, string> = { fr: "Français", en: "English" };

type Props = {
  user: UserRepresentation;
  api: Api;
  reload: () => Promise<void>;
  kcContext: KcContext;
};

export function PersonalInfo({ user, api, reload, kcContext }: Props) {
  const i18nEnabled = kcContext.realm.isInternationalizationEnabled;
  const initialLocale = user.attributes?.locale?.[0] ?? getLang();
  const [firstName, setFirstName] = useState(user.firstName ?? "");
  const [lastName, setLastName] = useState(user.lastName ?? "");
  const [locale, setLocale] = useState(initialLocale);
  const [locales, setLocales] = useState<string[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [success, setSuccess] = useState(false);
  const [failure, setFailure] = useState(false);

  useEffect(() => {
    api.getSupportedLocales().then(setLocales, () => setLocales([]));
  }, []);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setErrors({});
    setSuccess(false);
    setFailure(false);
    try {
      await api.saveProfile({
        ...user,
        firstName,
        lastName,
        attributes: i18nEnabled
          ? { ...user.attributes, locale: [locale] }
          : user.attributes,
      });
    } catch (error) {
      if (error instanceof ApiError && error.fieldErrors.length > 0) {
        const next: Record<string, string> = {};
        for (const { field, errorMessage, params } of error.fieldErrors) {
          next[field] = kcMsg(errorMessage, params);
        }
        setErrors(next);
      } else {
        setFailure(true);
      }
      return;
    }
    if (i18nEnabled && locale !== initialLocale) {
      location.assign(
        getBasename() + "?kc_locale=" + encodeURIComponent(locale),
      );
      return;
    }
    await reload();
    setSuccess(true);
  }

  const localeOptions = locales.includes(locale)
    ? locales
    : [locale, ...locales];

  return (
    <>
      <h1 className="fr-h3">{t("profile.title")}</h1>
      {success && (
        <Alert
          className="fr-mb-3w"
          severity="success"
          title={t("profile.success")}
          closable
        />
      )}
      {failure && (
        <Alert
          className="fr-mb-3w"
          severity="error"
          title={t("profile.error")}
        />
      )}
      <p className="fr-hint-text">{t("profile.required")}</p>
      <form onSubmit={(event) => void onSubmit(event)}>
        <Input
          label={t("profile.firstName")}
          state={errors.firstName ? "error" : "default"}
          stateRelatedMessage={errors.firstName}
          nativeInputProps={{
            name: "firstName",
            required: true,
            autoComplete: "given-name",
            value: firstName,
            onChange: (event) => setFirstName(event.target.value),
          }}
        />
        <Input
          label={t("profile.lastName")}
          state={errors.lastName ? "error" : "default"}
          stateRelatedMessage={errors.lastName}
          nativeInputProps={{
            name: "lastName",
            required: true,
            autoComplete: "family-name",
            value: lastName,
            onChange: (event) => setLastName(event.target.value),
          }}
        />
        <Input
          label={t("profile.email")}
          disabled
          nativeInputProps={{
            type: "email",
            value: user.email,
            readOnly: true,
          }}
        />
        {i18nEnabled && (
          <Select
            label={t("profile.locale")}
            state={errors.locale ? "error" : "default"}
            stateRelatedMessage={errors.locale}
            nativeSelectProps={{
              name: "locale",
              value: locale,
              onChange: (event) => setLocale(event.target.value),
            }}
          >
            {localeOptions.map((value) => (
              <option key={value} value={value}>
                {LOCALE_LABELS[value] ?? value}
              </option>
            ))}
          </Select>
        )}
        <Button type="submit" priority="secondary">
          {t("profile.submit")}
        </Button>
      </form>
    </>
  );
}
