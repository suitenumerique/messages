import styles from "./styles.css?inline";
import { createShadowWidget } from "../../shared/shadow-dom";
import { installHook } from "../../shared/script";
import { listenEvent, triggerEvent } from "../../shared/events";

const widgetName = "lagaufre";

type Service = {
  name: string;
  url: string;
  maturity: string;
  logo?: string;
};

type Organization = {
  name: string;
  type: string;
  siret: string;
};

type ServicesResponse = {
  organization?: Organization;
  services: Service[];
  error?: unknown;
};

type GaufreWidgetArgs = {
  api?: string;
  position?: string;
  top?: number;
  bottom?: number;
  left?: number;
  right?: number;
  data?: ServicesResponse;
  fontFamily?: string;
  background?: string;
  headerLogo?: string;
  headerUrl?: string;
};

listenEvent(widgetName, "init", null, false, async (args: GaufreWidgetArgs) => {

    if (!args.api && !args.data) {
    console.error("Missing API URL");
    return;
  }

  let headerHtml = "";
  if (args.headerLogo && args.headerUrl) {
    headerHtml =
      `<a href="${args.headerUrl}" target="_blank">` +
      `<img src="${args.headerLogo}" alt="Header Logo" class="header-logo">` +
      `</a>`;
  }

  /* prettier-ignore */
  const htmlContent =
    `<div id="wrapper">` +
        `<div id="header">` +
            headerHtml +
            `<button id="close" tabindex="1">&times;</button>` +
        `</div>` +
        `<div id="content">` +
            `<div id="loading" class="loading">Loading services...</div>` +
            `<div id="services-container" class="services-container" style="display: none;"></div>` +
            `<div id="error" class="error" style="display: none;"></div>` +
        `</div>` +
        `<div id="footer">` +
            `<button id="ok-button" class="ok-button">OK</button>` +
        `</div>` +
    `</div>`;

  // Create shadow DOM widget
  const shadowContainer = createShadowWidget(widgetName, htmlContent, styles);
  const shadowRoot = shadowContainer.shadowRoot!;

  const wrapper = shadowRoot.querySelector<HTMLDivElement>("#wrapper")!;
  const loadingDiv = shadowRoot.querySelector<HTMLDivElement>("#loading")!;
  const servicesContainer = shadowRoot.querySelector<HTMLDivElement>("#services-container")!;
  const errorDiv = shadowRoot.querySelector<HTMLDivElement>("#error")!;
  const closeBtn = shadowRoot.querySelector<HTMLButtonElement>("#close")!;
  const okBtn = shadowRoot.querySelector<HTMLButtonElement>("#ok-button")!;

  // Positioning parameters
  const position = args.position || "fixed"; // 'fixed' or 'absolute'
  let top = args.top;
  const bottom = args.bottom;
  const left = args.left;
  let right = args.right;

  if (top === undefined && bottom === undefined && left === undefined && right === undefined) {
    top = 60;
    right = 60;
  }

  // Apply positioning styles
  wrapper.style.position = position;

  wrapper.style.top = typeof top === "number" ? `${top}px` : "unset";
  wrapper.style.bottom = typeof bottom === "number" ? `${bottom}px` : "unset";
  wrapper.style.left = typeof left === "number" ? `${left}px` : "unset";
  wrapper.style.right = typeof right === "number" ? `${right}px` : "unset";

  // Apply font family (inherit from parent or use provided)
  if (args.fontFamily) {
    wrapper.style.fontFamily = args.fontFamily;
  }

  // Apply background gradient if requested
  if (args.background) {
    wrapper.style.background = args.background;
  }

  const showError = (message: string) => {
    loadingDiv.style.display = "none";
    servicesContainer.style.display = "none";
    errorDiv.style.display = "block";
    errorDiv.textContent = message;
  };

  const showServices = (data: ServicesResponse) => {
    loadingDiv.style.display = "none";
    errorDiv.style.display = "none";
    servicesContainer.style.display = "block";

    // Clear previous content
    servicesContainer.innerHTML = "";

    // Create services grid
    const servicesGrid = document.createElement("div");
    servicesGrid.className = "services-grid";

    data.services.forEach((service) => {
      if (!service.logo) return;

      const serviceCard = document.createElement("div");
      serviceCard.className = `service-card`;

      /* prettier-ignore */
      serviceCard.innerHTML =
        `<a href="${service.url}" target="_blank">` +
            `<img src="${service.logo}" alt="${service.name} logo" class="service-logo" onerror="this.style.display='none'">` +
            (service.maturity && service.maturity !== "stable"
              ? `<div class="maturity-badge">${service.maturity}</div>`
              : "") +
            `<div class="service-info">` +
                `<div class="service-name">${service.name}</div>` +
            `</div>` +
        `</a>`;

      servicesGrid.appendChild(serviceCard);
    });

    servicesContainer.appendChild(servicesGrid);
  };

  if (args.data) {
    showServices(args.data);
  } else {
    // Fetch services from API
    try {

      const response = await fetch(args.api!, {
        method: "GET",
      });

      const data = (await response.json()) as ServicesResponse;

      if (data.error) {
        showError(`Error: ${JSON.stringify(data.error)}`);
      } else if (data.services && data.services.length > 0) {
        showServices(data);
      } else {
        showError("No services found");
      }
    } catch (error) {
      showError(`Failed to load services: ${error instanceof Error ? error.message : "Unknown error"}`);
    }
  }

  const closeWidget = () => {
    shadowRoot.host.remove();
    triggerEvent(widgetName, "closed");
  };

  // Close button click handler
  closeBtn.addEventListener("click", closeWidget);

  // OK button click handler (mobile only)
  okBtn.addEventListener("click", closeWidget);

  // Listen for programmatic close events
  listenEvent(widgetName, "close", null, false, closeWidget);

  // Click outside to close
  const handleClickOutside = (event: MouseEvent) => {
    if (!shadowContainer.contains(event.target as Node)) {
      closeWidget();
    }
  };

  // Add click outside listener after a short delay to prevent immediate closing
  setTimeout(() => {
    document.addEventListener("click", handleClickOutside);
  }, 100);

  // TODO: listen to "escape" key ?

  document.body.appendChild(shadowContainer);

  triggerEvent(widgetName, "opened");
});

installHook(widgetName);
