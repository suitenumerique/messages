import styles from './styles.css?inline'
import { createShadowWidget } from '../../shared/shadow-dom'
import { installHook } from '../../shared/script'
import { listenEvent, triggerEvent } from '../../shared/events'

const widgetName = "feedback";

type ConfigData = {
  captcha: boolean;
  title: string;
  placeholder: string;
  emailPlaceholder: string;
  submitText: string;
  successText: string;
};

type ConfigResponse = {
  success: boolean;
  detail?: string;
  captcha: boolean;
  config: ConfigData;
};

listenEvent(widgetName, 'init', null, false, async (args) => {

  if (!args.api) {
    console.error("Feedback widget requires an API URL");
    return;
  }

  let configData: ConfigData | undefined;
  try {
    const config = await fetch(`${args.api}config/`, {
      'headers': {
        'X-Channel-ID': args.channel
      }
    });
    const configResponse = await config.json() as ConfigResponse;
    if (!configResponse.success) throw new Error(configResponse.detail || 'Unknown error');
    if (configResponse.captcha) throw new Error('Captcha is not supported yet');
    configData = configResponse.config;
  } catch (error) {
    console.error("Error fetching config", error);
    triggerEvent(widgetName, 'closed');
    return;
  }

  const title = args.title || configData?.title || 'Feedback';
  const placeholder = args.placeholder || configData?.placeholder || 'Share your feedback...';
  const emailPlaceholder = args.emailPlaceholder || configData?.emailPlaceholder || 'Your email...';
  const submitText = args.submitText || configData?.submitText || 'Send Feedback';
  const successText = args.successText || configData?.successText || 'Thank you for your feedback!';

  const htmlContent = `<div class="wrapper">` +
      `<div class="header">` +
        `<span>${title}</span>` +
        `<button class="close-btn" id="close">×</button>` +
      `</div>` +
      `<form class="content">` +
        `<textarea id="feedback-text" autocomplete="off" required placeholder="${placeholder}"></textarea>` +
        `<input type="email" id="email" autocomplete="email" required placeholder="${emailPlaceholder}">` +
        `<button type="submit" id="submit">${submitText}</button>` +
        `<div id="status" class="status"></div>` +
      `</form>` +
    `</div>`;

  // Create shadow DOM widget
  const shadowRoot = createShadowWidget(widgetName, htmlContent, styles);

  triggerEvent(widgetName, 'opened');
  
  const submitBtn = shadowRoot.querySelector<HTMLButtonElement>('#submit')!
  const feedbackText = shadowRoot.querySelector<HTMLTextAreaElement>('#feedback-text')!
  const statusDiv = shadowRoot.querySelector<HTMLDivElement>('#status')!
  const closeBtn = shadowRoot.querySelector<HTMLButtonElement>('#close')!
  const emailInput = shadowRoot.querySelector<HTMLInputElement>('#email')!
  const form = shadowRoot.querySelector<HTMLFormElement>('form')!

  if (args.email) {
    emailInput.remove();
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = feedbackText.value.trim()
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
    
      const ret = await fetch(`${args.api}deliver/`, {
        'method': 'POST',
        'headers': {
          'Content-Type': 'application/json',
          'X-Channel-ID': args.channel
        },
        'body': JSON.stringify({ textBody: message, email })
      });
      const retData = await ret.json();
      
      if (!retData.success) throw new Error(retData.detail || 'Unknown error');
  
      statusDiv.innerHTML = `<span class="success">${successText}</span>`
      feedbackText.remove();
      emailInput.remove();
      submitBtn.remove();
      
    } catch (error) {
      statusDiv.innerHTML = `<span class="error">${error instanceof Error ? error.message : 'Unknown error'}</span>`
    }
  });

  const closeWidget = () => {
    shadowRoot.host.remove();
    triggerEvent(widgetName, 'closed');
  }

  closeBtn.addEventListener('click', closeWidget);
  listenEvent(widgetName, 'close', null, false, closeWidget);
});

installHook(widgetName);