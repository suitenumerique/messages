import styles from "./styles.css?inline";
import { createShadowWidget } from "../../shared/shadow-dom";
import { installHook } from "../../shared/script";
import { listenEvent, triggerEvent } from "../../shared/events";
import { trapFocus, trapEscape } from "../../shared/focus";

const widgetName = "feedback";

type ConfigData = {
  title?: string;
  placeholder?: string;
  emailPlaceholder?: string;
  submitText?: string;
  successText?: string;
  successText2?: string;
  closeLabel?: string;
  submitUrl?: string;
};

type ConfigResponse = {
  success?: boolean;
  detail?: string;
  captcha?: boolean;
  config?: ConfigData;
};

type FeedbackWidgetArgs = {
  title?: string;
  placeholder?: string;
  emailPlaceholder?: string;
  submitText?: string;
  successText?: string;
  successText2?: string;
  closeLabel?: string;
  submitUrl?: string;
  api: string;
  channel: string;
  email?: string;
  bottomOffset?: number;
  rightOffset?: number;
};

listenEvent(widgetName, "init", null, false, async (args: FeedbackWidgetArgs) => {
  if (!args.api || !args.channel) {
    console.error("Feedback widget requires an API URL and a channel ID");
    return;
  }

  let configData: ConfigData | undefined;
  try {
    const config = await fetch(`${args.api}config/`, {
      headers: {
        "X-Channel-ID": args.channel,
      },
    });
    const configResponse = (await config.json()) as ConfigResponse;
    if (!configResponse.success) throw new Error(configResponse.detail || "Unknown error");
    if (configResponse.captcha) throw new Error("Captcha is not supported yet");
    configData = configResponse.config;
  } catch (error) {
    console.error("Error fetching config", error);
    triggerEvent(widgetName, "closed");
    return;
  }

  const title = args.title || configData?.title || "Feedback";
  const placeholder = args.placeholder || configData?.placeholder || "Share your feedback...";
  const emailPlaceholder = args.emailPlaceholder || configData?.emailPlaceholder || "Your email...";
  const submitText = args.submitText || configData?.submitText || "Send Feedback";
  const successText = args.successText || configData?.successText || "Thank you for your feedback!";
  const successText2 = args.successText2 || configData?.successText2 || "";
  const closeLabel = args.closeLabel || configData?.closeLabel || "Close the feedback widget";

  /* prettier-ignore */
  const htmlContent =
    `<div id="wrapper">` +
      `<div id="header">` +
        `<h6 id="title"></h6>` +
        `<button id="close">&times;</button>` +
      `</div>` +
      `<div id="content">` +
        `<form>` +
          `<textarea id="feedback-text" autocomplete="off" required></textarea>` +
          `<input type="email" id="email" autocomplete="email" required>` +
          `<button type="submit" id="submit"></button>` +
        `</form>` +
        `<div id="error" aria-live="polite" role="status"></div>` +
        `<div aria-live="polite" role="status" id="success">` +
          `<i aria-hidden="true">✔</i>` +
          `<p id="success-text"></p>` +
          `<p id="success-text2"></p>` +
        `</div>` +
      `</div>` +
    `</div>`;

  // Create shadow DOM widget
  const shadowContainer = createShadowWidget(widgetName, htmlContent, styles);
  const shadowRoot = shadowContainer.shadowRoot!;
  const $ = shadowRoot.querySelector.bind(shadowRoot);

  const wrapper = $<HTMLDivElement>("#wrapper")!;
  const titleSpan = $<HTMLHeadingElement>("#title")!;
  const submitBtn = $<HTMLButtonElement>("#submit")!;
  const feedbackText = $<HTMLTextAreaElement>("#feedback-text")!;
  const errorDiv = $<HTMLDivElement>("#error")!;
  const closeBtn = $<HTMLButtonElement>("#close")!;
  const emailInput = $<HTMLInputElement>("#email")!;
  const form = $<HTMLFormElement>("form")!;
  const successDiv = $<HTMLDivElement>("#success")!;
  const successTextP = $<HTMLParagraphElement>("#success-text")!;
  const successText2P = $<HTMLParagraphElement>("#success-text2")!;

  wrapper.style.bottom = 20 + (args.bottomOffset || 0) + "px";
  wrapper.style.right = 20 + (args.rightOffset || 0) + "px";

  titleSpan.textContent = title;
  feedbackText.placeholder = placeholder;
  emailInput.placeholder = emailPlaceholder;
  submitBtn.textContent = submitText;
  closeBtn.setAttribute("aria-label", closeLabel);

  if (args.email) {
    emailInput.remove();
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorDiv.textContent = "";
    const message = feedbackText.value.trim();
    const email = args.email || emailInput.value.trim();
    try {
      if (!message) {
        feedbackText.focus();
        throw new Error("Missing value");
      }
      if (!email) {
        emailInput.focus();
        throw new Error("Missing value");
      }

      const ret = await fetch(configData?.submitUrl || `${args.api}deliver/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Channel-ID": args.channel,
        },
        body: JSON.stringify({ textBody: message, email }),
      });
      let retData;
      try {
        retData = await ret.json();
      } catch {
        throw new Error("Invalid response from server");
      }

      if (!retData.success) throw new Error(retData.detail || "Unknown error");

      form.remove();
      successDiv.style.display = "flex";
      requestAnimationFrame(() => {
        // This RAF call allows screen readers to register the aria-live area
        successTextP.textContent = successText;
        successText2P.textContent = successText2;
      });
    } catch (error) {
      errorDiv.style.display = "block";
      errorDiv.textContent = "⚠ " + (error instanceof Error ? error.message : "Unknown error");
    }
  });

  let untrapFocus: (() => void) | null = null;
  let untrapEscape: (() => void) | null = null;
  let removeCloseListener: () => void = () => {};

  const closeWidget = () => {
    shadowRoot.host.remove();
    if (untrapFocus) {
      untrapFocus();
    }
    if (untrapEscape) {
      untrapEscape();
    }
    if (removeCloseListener) {
      removeCloseListener();
    }
    triggerEvent(widgetName, "closed");
  };

  closeBtn.addEventListener("click", closeWidget);
  removeCloseListener = listenEvent(widgetName, "close", null, false, closeWidget);

  document.body.appendChild(shadowContainer);

  feedbackText.focus();

  untrapFocus = trapFocus(shadowRoot, wrapper, "textarea,input,button");
  untrapEscape = trapEscape(() => {
    triggerEvent(widgetName, "close");
  });

  triggerEvent(widgetName, "opened");
});

installHook(widgetName);
