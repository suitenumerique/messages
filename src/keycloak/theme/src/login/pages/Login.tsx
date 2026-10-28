import { useState } from "react";
import { Input } from "@codegouvfr/react-dsfr/Input";
import { PasswordInput } from "@codegouvfr/react-dsfr/blocks/PasswordInput";
import { Checkbox } from "@codegouvfr/react-dsfr/Checkbox";
import { Button } from "@codegouvfr/react-dsfr/Button";
import { kcSanitize } from "keycloakify/lib/kcSanitize";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import { useScript } from "keycloakify/login/pages/Login.useScript";
import { getSettings } from "../../shared/settings";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";
import "../login.css";

export default function Login(
  props: PageProps<Extract<KcContext, { pageId: "login.ftl" }>, I18n>,
) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const {
    realm,
    url,
    usernameHidden,
    login,
    auth,
    messagesPerField,
    enableWebAuthnConditionalUI,
    authenticators,
    registrationDisabled,
  } = kcContext;
  const { msg } = i18n;

  const [isLoginButtonDisabled, setIsLoginButtonDisabled] = useState(false);

  const webAuthnButtonId = "authenticateWebAuthnButton";

  useScript({ webAuthnButtonId, kcContext, i18n });

  const showRememberMe = realm.rememberMe && !usernameHidden;
  const { forgotPasswordUrl } = getSettings(kcContext.properties);
  const resetHref =
    forgotPasswordUrl ||
    (realm.resetPasswordAllowed ? url.loginResetCredentialsUrl : undefined);
  const hasError = messagesPerField.existsError("username", "password");
  const errorHtml = hasError ? (
    <span
      id="input-error"
      aria-live="polite"
      dangerouslySetInnerHTML={{
        __html: kcSanitize(
          messagesPerField.getFirstError("username", "password"),
        ),
      }}
    />
  ) : undefined;

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage={!hasError}
      headerNode={msg("lstLoginTitle")}
      displayInfo={
        realm.password && realm.registrationAllowed && !registrationDisabled
      }
      infoNode={
        <p className="fr-mt-2w" id="kc-registration">
          {msg("noAccount")}{" "}
          <a className="fr-link" href={url.registrationUrl}>
            {msg("doRegister")}
          </a>
        </p>
      }
    >
      {realm.password && (
        <form
          id="kc-form-login"
          onSubmit={() => {
            setIsLoginButtonDisabled(true);
            return true;
          }}
          action={url.loginAction}
          method="post"
        >
          {!usernameHidden && (
            <Input
              label={
                !realm.loginWithEmailAllowed
                  ? msg("username")
                  : !realm.registrationEmailAsUsername
                    ? msg("usernameOrEmail")
                    : msg("email")
              }
              state={hasError ? "error" : "default"}
              stateRelatedMessage={errorHtml}
              nativeInputProps={{
                id: "username",
                name: "username",
                type: "text",
                autoComplete: enableWebAuthnConditionalUI
                  ? "username webauthn"
                  : "username",
                autoFocus: true,
                defaultValue: login.username ?? "",
              }}
            />
          )}
          <PasswordInput
            label={msg("lstPasswordLabel")}
            messagesHint=""
            messages={
              usernameHidden && hasError
                ? [{ severity: "error", message: errorHtml }]
                : []
            }
            nativeInputProps={{
              id: "password",
              name: "password",
              autoComplete: "current-password",
            }}
          />
          {(showRememberMe || resetHref) && (
            <div className="lst-login-options">
              {showRememberMe && (
                <Checkbox
                  small
                  options={[
                    {
                      label: msg("rememberMe"),
                      nativeInputProps: {
                        id: "rememberMe",
                        name: "rememberMe",
                        defaultChecked: !!login.rememberMe,
                      },
                    },
                  ]}
                />
              )}
              {resetHref && (
                <a className="fr-link" href={resetHref} id="kc-forgot-password">
                  {msg("doForgotPassword")}
                </a>
              )}
            </div>
          )}
          <input
            type="hidden"
            id="id-hidden-input"
            name="credentialId"
            value={auth.selectedCredential ?? ""}
          />
          <Button
            className="lst-btn-full"
            disabled={isLoginButtonDisabled}
            nativeButtonProps={{
              id: "kc-login",
              name: "login",
              type: "submit",
            }}
          >
            {msg("doLogIn")}
          </Button>
        </form>
      )}
      {enableWebAuthnConditionalUI && (
        <>
          <p className="fr-hr-or">{msg("lstOr")}</p>
          <form id="webauth" action={url.loginAction} method="post">
            <input type="hidden" id="clientDataJSON" name="clientDataJSON" />
            <input
              type="hidden"
              id="authenticatorData"
              name="authenticatorData"
            />
            <input type="hidden" id="signature" name="signature" />
            <input type="hidden" id="credentialId" name="credentialId" />
            <input type="hidden" id="userHandle" name="userHandle" />
            <input type="hidden" id="error" name="error" />
          </form>
          {authenticators !== undefined &&
            authenticators.authenticators.length !== 0 && (
              <form id="authn_select">
                {authenticators.authenticators.map((authenticator, i) => (
                  <input
                    key={i}
                    type="hidden"
                    name="authn_use_chk"
                    readOnly
                    value={authenticator.credentialId}
                  />
                ))}
              </form>
            )}
          <button
            type="button"
            id={webAuthnButtonId}
            className="fr-btn fr-btn--secondary fr-btn--icon-left fr-icon-lock-unlock-line lst-btn-full"
          >
            {msg("lstPasskeyButton")}
          </button>
        </>
      )}
    </Template>
  );
}
