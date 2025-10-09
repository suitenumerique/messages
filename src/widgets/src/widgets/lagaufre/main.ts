import styles from "./styles.css?inline";
import { createShadowWidget } from "../../shared/shadow-dom";
import { installHook } from "../../shared/script";
import { listenEvent, triggerEvent } from "../../shared/events";
import { trapFocus, trapEscape } from "../../shared/focus";

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
  open?: boolean;
  label?: string;
  closeLabel?: string;
};

// Initialize widget (load data and prepare shadow DOM)
listenEvent(widgetName, "init", null, false, async (args: GaufreWidgetArgs) => {
  if (!args.api && !args.data) {
    console.error("Missing API URL");
    return;
  }

  let isVisible = false;

  let headerHtml = "";
  if (args.headerLogo && args.headerUrl) {
    headerHtml =
      `<a href="${args.headerUrl}" target="_blank">` +
      `<img src="${args.headerLogo}" alt="Logo" class="header-logo">` +
      `</a>`;
  }

  /* prettier-ignore */
  const htmlContent =
    `<div id="wrapper" role="dialog" aria-modal="true" tabindex="-1">` +
        `<div id="header">` +
            headerHtml +
            `<button id="close">&times;</button>` +
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

  // Apply texts
  const label = args.label || "Services";
  const closeLabel = args.closeLabel || "Close the Services menu";
  wrapper.setAttribute("aria-labelledby", label);
  closeBtn.setAttribute("aria-label", closeLabel);

  // Initially hide the widget
  wrapper.style.display = "none";

  const showError = (message: string) => {
    loadingDiv.style.display = "none";
    servicesContainer.style.display = "none";
    errorDiv.style.display = "block";
    errorDiv.textContent = message;
  };

  const renderServices = (data: ServicesResponse) => {
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
            `<img src="${service.logo}" alt="${service.name} logo" class="service-logo" aria-hidden="true" onerror="this.style.display='none'">` +
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

  // Load data
  if (args.data) {
    renderServices(args.data);
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
        renderServices(data);
      } else {
        showError("No services found");
      }
    } catch (error) {
      showError(`Failed to load services: ${error instanceof Error ? error.message : "Unknown error"}`);
    }
  }

  const handleClickOutside = (event: MouseEvent) => {
    if (!shadowContainer.contains(event.target as Node)) {
      triggerEvent(widgetName, "close");
    }
  };

  let untrapFocus: (() => void) | null = null;
  let untrapEscape: (() => void) | null = null;

  // Open widget (show the prepared shadow DOM)
  listenEvent(widgetName, "open", null, false, () => {
    wrapper.style.display = "block";

    // Add click outside listener after a short delay to prevent immediate closing or double-clicks.
    setTimeout(() => {
      isVisible = true;
      document.addEventListener("click", handleClickOutside);
      wrapper.focus();
    }, 200);

    untrapFocus = trapFocus(shadowRoot, wrapper, "a,button");
    untrapEscape = trapEscape(() => {
      triggerEvent(widgetName, "close");
    });

    triggerEvent(widgetName, "opened");
  });

  // Close widget (hide the shadow DOM)
  listenEvent(widgetName, "close", null, false, () => {
    if (untrapFocus) {
      untrapFocus();
    }
    if (untrapEscape) {
      untrapEscape();
    }

    if (!isVisible) {
      return; // Already closed
    }

    wrapper.style.display = "none";
    isVisible = false;

    // Remove click outside listener
    document.removeEventListener("click", handleClickOutside);

    triggerEvent(widgetName, "closed");
  });

  // Toggle widget visibility
  listenEvent(widgetName, "toggle", null, false, () => {
    if (isVisible) {
      triggerEvent(widgetName, "close");
    } else {
      triggerEvent(widgetName, "open");
    }
  });

  // Close button click handlers
  okBtn.addEventListener("click", () => {
    triggerEvent(widgetName, "close");
  });
  closeBtn.addEventListener("click", () => {
    triggerEvent(widgetName, "close");
  });

  // Add to DOM but keep hidden
  document.body.appendChild(shadowContainer);

  triggerEvent(widgetName, "initialized");

  if (args.open) {
    triggerEvent(widgetName, "open");
  }
});

installHook(widgetName);
