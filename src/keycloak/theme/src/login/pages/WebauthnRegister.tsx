import { useEffect, useState } from "react";
import { Input } from "@codegouvfr/react-dsfr/Input";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import { useScript } from "keycloakify/login/pages/WebauthnRegister.useScript";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";
import "../login.css";
import { LogoutOtherSessions } from "../components/LogoutOtherSessions";

export default function WebauthnRegister(
  props: PageProps<
    Extract<KcContext, { pageId: "webauthn-register.ftl" }>,
    I18n
  >,
) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { url, isSetRetry, isAppInitiatedAction } = kcContext;
  const { msg, msgStr } = i18n;

  const authButtonId = "authenticateWebAuthnButton";
  const [isSupported] = useState(
    () => typeof window.PublicKeyCredential !== "undefined",
  );

  useScript({ authButtonId, kcContext, i18n });

  // The stock Keycloak script asks the key name with window.prompt; read our DSFR field instead.
  useEffect(() => {
    const originalPrompt = window.prompt;
    window.prompt = () =>
      (
        document.getElementById("lst-passkey-label") as HTMLInputElement | null
      )?.value?.trim() || msgStr("lstPasskeyLabelDefault");
    return () => {
      window.prompt = originalPrompt;
    };
  }, []);

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      headerNode={msg("lstPasskeyRegisterTitle")}
    >
      {!isSupported && (
        <div
          className="fr-callout fr-icon-alert-line"
          id="webauthn-not-supported"
        >
          <p className="fr-callout__title fr-h4">
            {msg("lstPasskeyUnsupportedTitle")}
          </p>
          <p className="fr-callout__text">{msg("lstPasskeyUnsupportedText")}</p>
          {!isAppInitiatedAction && (
            <a className="fr-link" href={url.loginRestartFlowUrl}>
              {msg("restartLoginTooltip")}
            </a>
          )}
        </div>
      )}
      {isSupported && (
        <Input
          label={msg("lstPasskeyLabelField")}
          hintText={msg("lstPasskeyLabelHint")}
          nativeInputProps={{
            id: "lst-passkey-label",
            defaultValue: msgStr("lstPasskeyLabelDefault"),
            maxLength: 64,
          }}
        />
      )}
      <form id="register" action={url.loginAction} method="post">
        <input type="hidden" id="clientDataJSON" name="clientDataJSON" />
        <input type="hidden" id="attestationObject" name="attestationObject" />
        <input
          type="hidden"
          id="publicKeyCredentialId"
          name="publicKeyCredentialId"
        />
        <input
          type="hidden"
          id="authenticatorLabel"
          name="authenticatorLabel"
        />
        <input type="hidden" id="transports" name="transports" />
        <input
          type="hidden"
          id="authenticatorAttachment"
          name="authenticatorAttachment"
        />
        <input type="hidden" id="error" name="error" />
        <LogoutOtherSessions i18n={i18n} />
      </form>
      {isSupported && (
        <>
          <button
            type="button"
            id={authButtonId}
            className="fr-btn lst-btn-full fr-mb-2w"
          >
            {msg("lstPasskeyRegisterButton")}
          </button>
        </>
      )}
      {!isSetRetry && isAppInitiatedAction && (
        <form
          action={url.loginAction}
          id="kc-webauthn-settings-form"
          method="post"
        >
          <button
            type="submit"
            className="fr-btn fr-btn--secondary lst-btn-full"
            id="cancelWebAuthnAIA"
            name="cancel-aia"
            value="true"
          >
            {msg("doCancel")}
          </button>
        </form>
      )}
    </Template>
  );
}
