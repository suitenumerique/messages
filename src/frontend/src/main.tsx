import "@blocknote/mantine/style.css";
import "./styles/main.scss";
import "./features/i18n/initI18n";
import "../instrumentation-client";

import { createRoot } from "react-dom/client";
import { createRouter, parseSearchWith, RouterProvider, stringifySearchWith } from "@tanstack/react-router";

import { routeTree } from "./routes.gen";

// Default TSR encoding JSON-wraps every search value (`?key=1` → `?key=%221%22`).
// The rest of the app builds URLs via `URLSearchParams.toString()` and the
// backend expects plain values, so we plug identity parsers to keep both sides
// aligned — values stay as raw strings on the way out and on the way back.
const router = createRouter({
  routeTree,
  scrollRestoration: false,
  defaultPreload: false,
  parseSearch: parseSearchWith((value) => value),
  stringifySearch: stringifySearchWith((value) => (value == null ? "" : String(value))),
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const container = document.getElementById("root");
if (!container) throw new Error("#root element not found in index.html");

createRoot(container).render(<RouterProvider router={router} />);
