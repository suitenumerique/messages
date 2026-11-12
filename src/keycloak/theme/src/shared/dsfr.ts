import "@codegouvfr/react-dsfr/main.css";
import "./layout.css";
import { startReactDsfr } from "@codegouvfr/react-dsfr/spa";

let isStarted = false;
let lang = "fr";

export function setDsfrLang(languageTag: string) {
  lang = languageTag.split("-")[0];
}

export function startDsfrOnce() {
  if (isStarted) return;
  isStarted = true;
  // The react-dsfr components translate their own labels, such as the show-password toggle.
  startReactDsfr({ defaultColorScheme: "light", useLang: () => lang });
}
