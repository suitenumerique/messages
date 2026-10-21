import { useState } from "react";
import Button from "@codegouvfr/react-dsfr/Button";
import CallOut from "@codegouvfr/react-dsfr/CallOut";
import RadioButtons from "@codegouvfr/react-dsfr/RadioButtons";
import type { CredentialContainer } from "../api";
import { t, type MessageKey } from "../i18n";
import { navigate, routePaths } from "../router";

const OPTIONS: { type: string; label: MessageKey; hint: MessageKey }[] = [
  { type: "otp", label: "choice.totp", hint: "choice.totpHint" },
  {
    type: "webauthn-passwordless",
    label: "choice.passkey",
    hint: "choice.passkeyHint",
  },
  {
    type: "webauthn",
    label: "choice.securityKey",
    hint: "choice.securityKeyHint",
  },
];

type Props = {
  credentials: CredentialContainer[];
  runAction: (action: string) => Promise<void>;
};

export function DoubleAuthChoice({ credentials, runAction }: Props) {
  const [action, setAction] = useState<string>();

  const webauthnSupported = typeof window.PublicKeyCredential !== "undefined";
  const options = OPTIONS.flatMap(({ type, label, hint }) => {
    const createAction = credentials.find((c) => c.type === type)?.createAction;
    return createAction ? [{ type, createAction, label, hint }] : [];
  });

  return (
    <>
      <h1 className="fr-h3">{t("choice.title")}</h1>
      {!webauthnSupported && (
        <CallOut
          iconId="fr-icon-alert-line"
          title={t("choice.unsupportedTitle")}
          className="fr-mb-3w"
        >
          {t("choice.unsupportedText")}
        </CallOut>
      )}
      {options.length === 0 ? (
        <p className="fr-mb-3w">{t("choice.empty")}</p>
      ) : (
        <RadioButtons
          legend={<span className="fr-sr-only">{t("choice.legend")}</span>}
          name="method"
          classes={{ inputGroup: "lst-option" }}
          options={options.map(({ type, createAction, label, hint }) => ({
            label: t(label),
            hintText: t(hint),
            nativeInputProps: {
              value: createAction,
              disabled: !webauthnSupported && type.startsWith("webauthn"),
              checked: action === createAction,
              onChange: () => setAction(createAction),
            },
          }))}
        />
      )}
      <Button
        className="fr-mb-2w"
        style={{ width: "100%", justifyContent: "center" }}
        disabled={!action}
        onClick={() => action && void runAction(action)}
      >
        {t("choice.continue")}
      </Button>
      <Button priority="tertiary" onClick={() => navigate(routePaths.account)}>
        {t("choice.back")}
      </Button>
    </>
  );
}
