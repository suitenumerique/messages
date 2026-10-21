import { useEffect, useState } from "react";
import Alert from "@codegouvfr/react-dsfr/Alert";
import type { KcContext } from "./KcContext";
import { App } from "./App";
import { createAuth, type Auth } from "./auth";
import { initI18n, t } from "./i18n";
import { initRouter } from "./router";

let authPromise: Promise<Auth> | undefined;

export default function KcPage({ kcContext }: { kcContext: KcContext }) {
  const [auth, setAuth] = useState<Auth>();
  const [failed, setFailed] = useState(false);
  initI18n(kcContext);
  initRouter(kcContext);

  useEffect(() => {
    // One shared promise: StrictMode runs effects twice and keycloak.init must run once.
    authPromise ??= createAuth(kcContext);
    authPromise.then(setAuth, (error) => {
      console.error(error);
      setFailed(true);
    });
  }, []);

  if (!auth) {
    return (
      <main role="main" id="main" className="fr-container fr-py-4w">
        {failed ? (
          <Alert
            severity="error"
            title={t("error.load")}
            description={
              <a
                href={location.href}
                onClick={(event) => {
                  event.preventDefault();
                  location.reload();
                }}
              >
                {t("error.retry")}
              </a>
            }
          />
        ) : (
          <p className="fr-text--sm">{t("loading")}</p>
        )}
      </main>
    );
  }
  return <App kcContext={kcContext} auth={auth} />;
}
