import type { PageProps } from "keycloakify/login/pages/PageProps";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";
import "../login.css";

export default function SelectAuthenticator(
  props: PageProps<
    Extract<KcContext, { pageId: "select-authenticator.ftl" }>,
    I18n
  >,
) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { url, auth } = kcContext;
  const { msg, advancedMsg } = i18n;

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayInfo={false}
      headerNode={msg("loginChooseAuthenticator")}
    >
      <form
        id="kc-select-credential-form"
        action={url.loginAction}
        method="post"
      >
        {auth.authenticationSelections.map((selection, i) => (
          <button
            key={i}
            type="submit"
            name="authenticationExecution"
            value={selection.authExecId}
            className="fr-tile fr-tile--sm fr-tile--horizontal fr-enlarge-button lst-tile-button"
          >
            <div className="fr-tile__body">
              <div className="fr-tile__content">
                <h2 className="fr-tile__title">
                  {advancedMsg(selection.displayName)}
                </h2>
                <p className="fr-tile__desc">
                  {advancedMsg(selection.helpText)}
                </p>
              </div>
            </div>
          </button>
        ))}
      </form>
    </Template>
  );
}
