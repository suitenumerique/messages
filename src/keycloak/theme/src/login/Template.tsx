import { useEffect } from "react";
import { Alert } from "@codegouvfr/react-dsfr/Alert";
import { kcSanitize } from "keycloakify/lib/kcSanitize";
import type { TemplateProps } from "keycloakify/login/TemplateProps";
import { useInitialize } from "keycloakify/login/Template.useInitialize";
import type { I18n } from "./i18n";
import type { KcContext } from "./KcContext";
import { setDsfrLang, startDsfrOnce } from "../shared/dsfr";
import { getAccountConsoleUrl, getBrand } from "../shared/brand";
import { Header } from "../shared/Header";
import { Footer } from "../shared/Footer";

export default function Template(props: TemplateProps<KcContext, I18n>) {
  const {
    displayInfo = false,
    displayMessage = true,
    displayRequiredFields = false,
    headerNode,
    socialProvidersNode = null,
    infoNode = null,
    documentTitle,
    kcContext,
    i18n,
    doUseDefaultCss,
    children,
  } = props;

  const { msg, msgStr, currentLanguage } = i18n;
  const { realm, auth, url, message, isAppInitiatedAction, properties } =
    kcContext;

  setDsfrLang(currentLanguage.languageTag);
  startDsfrOnce();

  // The message bundle loads after the first render, so the title follows the language.
  useEffect(() => {
    document.title = documentTitle ?? msgStr("loginTitle", realm.displayName);
  }, [documentTitle, currentLanguage.languageTag, i18n.isFetchingTranslations]);

  useEffect(() => {
    document.documentElement.lang = currentLanguage.languageTag;
  }, [currentLanguage.languageTag]);

  const { isReadyToRender } = useInitialize({ kcContext, doUseDefaultCss });

  if (!isReadyToRender) {
    return null;
  }

  const brand = getBrand(properties, getAccountConsoleUrl(realm.name));

  return (
    <>
      <Header {...brand} />
      <main role="main" id="main" className="fr-py-2w">
        <div className="fr-container">
          <div className="fr-grid-row fr-grid-row--center">
            <div className="fr-col-12 fr-col-md-8 fr-col-lg-5">
              {auth !== undefined &&
              auth.showUsername &&
              !auth.showResetCredentials ? (
                <div id="kc-username" className="fr-input-group">
                  <label className="fr-label" htmlFor="kc-attempted-username">
                    {msg("username")}
                  </label>
                  <div className="fr-input-wrap">
                    <input
                      id="kc-attempted-username"
                      className="fr-input"
                      value={auth.attemptedUsername}
                      readOnly
                    />
                  </div>
                  <button
                    id="reset-login"
                    type="button"
                    className="fr-btn fr-btn--tertiary-no-outline fr-btn--sm fr-icon-refresh-line fr-btn--icon-left"
                    aria-label={msgStr("restartLoginTooltip")}
                    onClick={() => {
                      window.location.href = url.loginRestartFlowUrl;
                    }}
                  >
                    {msg("restartLoginTooltip")}
                  </button>
                </div>
              ) : (
                <h1 id="kc-page-title" className="fr-h3">
                  {headerNode}
                </h1>
              )}

              {displayRequiredFields && (
                <p className="fr-hint-text">{msg("requiredFields")}</p>
              )}

              {displayMessage &&
                message !== undefined &&
                (message.type !== "warning" || !isAppInitiatedAction) && (
                  <Alert
                    small
                    severity={message.type}
                    className="fr-mb-2w"
                    description={
                      <span
                        dangerouslySetInnerHTML={{
                          __html: kcSanitize(message.summary),
                        }}
                      />
                    }
                  />
                )}

              {children}

              {auth !== undefined && auth.showTryAnotherWayLink && (
                <form
                  id="kc-select-try-another-way-form"
                  action={url.loginAction}
                  method="post"
                >
                  <input type="hidden" name="tryAnotherWay" value="on" />
                  <a
                    href="#"
                    id="try-another-way"
                    className="fr-link"
                    onClick={(event) => {
                      event.preventDefault();
                      document.forms[
                        "kc-select-try-another-way-form" as never
                      ].requestSubmit();
                    }}
                  >
                    {msg("doTryAnotherWay")}
                  </a>
                </form>
              )}

              {socialProvidersNode}

              {displayInfo && <div id="kc-info">{infoNode}</div>}
            </div>
          </div>
        </div>
      </main>
      <Footer {...brand} />
    </>
  );
}
