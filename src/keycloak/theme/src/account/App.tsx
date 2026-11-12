import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ComponentProps,
} from "react";
import Alert from "@codegouvfr/react-dsfr/Alert";
import type { KcContext } from "./KcContext";
import {
  createApi,
  type CredentialContainer,
  type UserRepresentation,
} from "./api";
import { pendingAction, type Auth, type PendingAction } from "./auth";
import { ActionAlert } from "./components/ActionAlert";
import { Sidemenu } from "./components/Sidemenu";
import { t } from "./i18n";
import { AccountAndLogin } from "./pages/AccountAndLogin";
import { DoubleAuthChoice } from "./pages/DoubleAuthChoice";
import { PersonalInfo } from "./pages/PersonalInfo";
import { getBasename, useRoute } from "./router";
import { getBrand } from "../shared/brand";
import { getSettings } from "../shared/settings";
import { startDsfrOnce } from "../shared/dsfr";
import { Footer } from "../shared/Footer";
import { Gaufre } from "../shared/Gaufre";
import { Header } from "../shared/Header";
import "./account.css";

startDsfrOnce();

type Data = { user: UserRepresentation; credentials: CredentialContainer[] };

export function App({ kcContext, auth }: { kcContext: KcContext; auth: Auth }) {
  const [api] = useState(() => createApi(kcContext, auth));
  const [data, setData] = useState<Data>();
  const [failed, setFailed] = useState(false);
  const route = useRoute();
  const brand = getBrand(
    kcContext.properties,
    new URL(getBasename(), location.href).href,
  );
  const settings = getSettings(kcContext.properties);
  const [pending, setPending] = useState<PendingAction | undefined>(
    pendingAction,
  );
  const firstRoute = useRef(route);

  useEffect(() => {
    if (route !== firstRoute.current) setPending(undefined);
  }, [route]);

  const reload = useCallback(async () => {
    try {
      const [user, credentials] = await Promise.all([
        api.getProfile(),
        api.getCredentials(),
      ]);
      setData({ user, credentials });
    } catch (error) {
      console.error(error);
      setFailed(true);
    }
  }, [api]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (!data) {
    return (
      <main role="main" id="main" className="fr-container fr-py-4w">
        {failed ? (
          <Alert severity="error" title={t("error.load")} />
        ) : (
          <p className="fr-text--sm">{t("loading")}</p>
        )}
      </main>
    );
  }

  const { user, credentials } = data;
  const quickAccessItems: NonNullable<
    ComponentProps<typeof Header>["quickAccessItems"]
  > = [];
  if (settings.lagaufre) {
    quickAccessItems.push(
      <Gaufre
        key="gaufre"
        {...settings.lagaufre}
        label={t("header.gaufre")}
        closeLabel={t("header.gaufreClose")}
      />,
    );
  }
  if (!settings.hideLogoutButton) {
    quickAccessItems.push({
      iconId: "fr-icon-logout-box-r-line",
      buttonProps: {
        onClick: () => void auth.logout(),
        "aria-label": `${t("header.logout")} - ${user.email}`,
      },
      text: user.email,
    });
  }

  return (
    <>
      <Header {...brand} quickAccessItems={quickAccessItems} />
      <main role="main" id="main">
        <div className="dashboard-container">
          <Sidemenu route={route} />
          <div className="dashboard-content fr-col-12 fr-col-lg-10 fr-col-xl-9 fr-col-offset-lg-1">
            {pending && <ActionAlert pending={pending} />}
            {route === "personal-info" ? (
              <PersonalInfo
                user={user}
                api={api}
                reload={reload}
                kcContext={kcContext}
              />
            ) : route === "choice" ? (
              <DoubleAuthChoice
                credentials={credentials}
                runAction={auth.runAction}
              />
            ) : (
              <AccountAndLogin
                user={user}
                credentials={credentials}
                runAction={auth.runAction}
                helpUrl={settings.twoFactorHelpUrl}
              />
            )}
          </div>
        </div>
      </main>
      <Footer {...brand} />
    </>
  );
}
