import type { PageProps } from "keycloakify/login/pages/PageProps";
import { useScript } from "keycloakify/login/pages/WebauthnAuthenticate.useScript";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";
import "../login.css";

export default function WebauthnAuthenticate(
  props: PageProps<
    Extract<KcContext, { pageId: "webauthn-authenticate.ftl" }>,
    I18n
  >,
) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { url, authenticators, shouldDisplayAuthenticators } = kcContext;
  const { msg } = i18n;

  const authButtonId = "authenticateWebAuthnButton";

  useScript({ authButtonId, kcContext, i18n });

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayInfo={false}
      headerNode={msg("webauthn-login-title")}
    >
      <div id="kc-form-webauthn">
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
        {authenticators && (
          <>
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
            {shouldDisplayAuthenticators && (
              <>
                {authenticators.authenticators.length > 1 && (
                  <p>{msg("webauthn-available-authenticators")}</p>
                )}
                {authenticators.authenticators.map((authenticator, i) => (
                  <div
                    key={i}
                    id={`kc-webauthn-authenticator-item-${i}`}
                    className="fr-card fr-card--horizontal fr-card--sm fr-mb-2w"
                  >
                    <div className="fr-card__body">
                      <div className="fr-card__content">
                        <h2
                          id={`kc-webauthn-authenticator-label-${i}`}
                          className="fr-card__title"
                        >
                          {authenticator.label}
                        </h2>
                        <p className="fr-card__desc">
                          <span
                            id={`kc-webauthn-authenticator-createdlabel-${i}`}
                          >
                            {msg("webauthn-createdAt-label")}
                          </span>{" "}
                          <span id={`kc-webauthn-authenticator-created-${i}`}>
                            {authenticator.createdAt}
                          </span>
                        </p>
                      </div>
                    </div>
                  </div>
                ))}
              </>
            )}
          </>
        )}
        <button
          type="button"
          id={authButtonId}
          className="fr-btn lst-btn-full"
          autoFocus
        >
          {msg("webauthn-doAuthenticate")}
        </button>
      </div>
    </Template>
  );
}
