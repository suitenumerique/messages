import { Button } from "@codegouvfr/react-dsfr/Button";
import "../login.css";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";

export default function LogoutConfirm(
  props: PageProps<Extract<KcContext, { pageId: "logout-confirm.ftl" }>, I18n>,
) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { url, client, logoutConfirm } = kcContext;
  const { msg, msgStr } = i18n;

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      headerNode={msg("logoutConfirmTitle")}
    >
      <p>{msg("logoutConfirmHeader")}</p>
      <form
        id="kc-logout-confirm"
        action={url.logoutConfirmAction}
        method="post"
      >
        <input type="hidden" name="session_code" value={logoutConfirm.code} />
        <Button
          type="submit"
          className="lst-btn-full"
          nativeButtonProps={{ id: "kc-logout", name: "confirmLogout" }}
        >
          {msgStr("doLogout")}
        </Button>
      </form>
      {!logoutConfirm.skipLink && client.baseUrl && (
        <p className="fr-mt-2w">
          <a id="backToApplication" className="fr-link" href={client.baseUrl}>
            {msg("backToApplication")}
          </a>
        </p>
      )}
    </Template>
  );
}
