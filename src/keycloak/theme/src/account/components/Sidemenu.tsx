import { getBasename, navigate, routePaths, type Route } from "../router";
import { t } from "../i18n";

const items: {
  route: Route;
  path: string;
  label: "menu.account" | "menu.personalInfo";
}[] = [
  {
    route: "personal-info",
    path: routePaths["personal-info"],
    label: "menu.personalInfo",
  },
  { route: "account", path: routePaths.account, label: "menu.account" },
];

export function Sidemenu({ route }: { route: Route }) {
  const active = route === "choice" ? "account" : route;
  return (
    <nav className="fr-sidemenu" role="navigation" aria-label={t("menu.label")}>
      <div className="fr-sidemenu__inner">
        <button
          id="fr-sidemenu-btn"
          className="fr-sidemenu__btn"
          aria-controls="fr-sidemenu-wrapper"
          aria-expanded="false"
        >
          {t("menu.label")}
        </button>
        <div className="fr-collapse" id="fr-sidemenu-wrapper">
          <ul className="fr-sidemenu__list">
            {items.map((item) => (
              <li
                key={item.route}
                className={
                  "fr-sidemenu__item" +
                  (active === item.route ? " fr-sidemenu__item--active" : "")
                }
              >
                <a
                  className="fr-sidemenu__link"
                  href={getBasename() + item.path.slice(1)}
                  aria-current={active === item.route ? "page" : undefined}
                  onClick={(event) => {
                    event.preventDefault();
                    navigate(item.path);
                  }}
                >
                  {t(item.label)}
                </a>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </nav>
  );
}
