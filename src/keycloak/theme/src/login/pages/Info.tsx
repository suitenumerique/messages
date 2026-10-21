import { kcSanitize } from "keycloakify/lib/kcSanitize";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";

export default function Info(
  props: PageProps<Extract<KcContext, { pageId: "info.ftl" }>, I18n>,
) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { advancedMsgStr, msg } = i18n;
  const {
    messageHeader,
    message,
    requiredActions,
    skipLink,
    pageRedirectUri,
    actionUri,
    client,
  } = kcContext;

  const link = (() => {
    if (skipLink) {
      return null;
    }
    if (pageRedirectUri) {
      return { href: pageRedirectUri, label: msg("backToApplication") };
    }
    if (actionUri) {
      return { href: actionUri, label: msg("proceedWithAction") };
    }
    if (client.baseUrl) {
      return { href: client.baseUrl, label: msg("backToApplication") };
    }
    return null;
  })();

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage={false}
      headerNode={
        <span
          dangerouslySetInnerHTML={{
            __html: kcSanitize(
              messageHeader !== undefined
                ? advancedMsgStr(messageHeader)
                : message.summary,
            ),
          }}
        />
      }
    >
      <div id="kc-info-message">
        <p
          dangerouslySetInnerHTML={{
            __html: kcSanitize(
              (message.summary?.trim() ?? "") +
                (requiredActions
                  ? " <b>" +
                    requiredActions
                      .map((requiredAction) =>
                        advancedMsgStr(`requiredAction.${requiredAction}`),
                      )
                      .join(", ") +
                    "</b>"
                  : ""),
            ),
          }}
        />
        {link !== null && (
          <p>
            <a className="fr-link" href={link.href}>
              {link.label}
            </a>
          </p>
        )}
      </div>
    </Template>
  );
}
