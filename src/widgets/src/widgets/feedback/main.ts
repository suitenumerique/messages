import styles from './styles.css?inline'
import { createShadowWidget } from '../../shared/shadow-dom'
import { installHook } from '../../shared/script'
import { listenEvent, triggerEvent } from '../../shared/events'

const widgetName = "feedback";

type ConfigData = {
  title?: string;
  placeholder?: string;
  emailPlaceholder?: string;
  submitText?: string;
  successText?: string;
  successText2?: string;
  submitUrl?: string;
};

type ConfigResponse = {
  success?: boolean;
  detail?: string;
  captcha?: boolean;
  config?: ConfigData;
};

listenEvent(widgetName, 'init', null, false, async (args) => {

  if (!args.api || !args.channel) {
    console.error("Feedback widget requires an API URL and a channel ID");
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
  const successText2 = args.successText2 || configData?.successText2;
  
  const htmlContent = `<div id="wrapper">` +
      `<div id="header">` +
        `<span id="title"></span>` +
        `<button id="close" aria-label="Close the feedback widget" tabindex="4">&times;</button>` +
      `</div>` +
      `<form id="content">` +
        `<textarea id="feedback-text" autocomplete="off" required tabindex="1"></textarea>` +
        `<input type="email" id="email" autocomplete="email" required tabindex="2">` +
        `<button type="submit" id="submit" tabindex="3"></button>` +
        `<div id="status" aria-live="polite" role="status"></div>` +
      `</form>` +
    `</div>`;

  // Create shadow DOM widget
  const shadowContainer = createShadowWidget(widgetName, htmlContent, styles);
  const shadowRoot = shadowContainer.shadowRoot!;
  
  const titleSpan = shadowRoot.querySelector<HTMLSpanElement>('#title')!
  const submitBtn = shadowRoot.querySelector<HTMLButtonElement>('#submit')!
  const feedbackText = shadowRoot.querySelector<HTMLTextAreaElement>('#feedback-text')!
  const statusDiv = shadowRoot.querySelector<HTMLDivElement>('#status')!
  const closeBtn = shadowRoot.querySelector<HTMLButtonElement>('#close')!
  const emailInput = shadowRoot.querySelector<HTMLInputElement>('#email')!
  const form = shadowRoot.querySelector<HTMLFormElement>('form')!

  titleSpan.textContent = title;
  feedbackText.placeholder = placeholder;
  emailInput.placeholder = emailPlaceholder;
  submitBtn.textContent = submitText;

  if (args.email) {
    emailInput.remove();
  }

  const setStatus = (status: string, success: boolean) => {
    statusDiv.innerHTML = '';
    const statusSpan = document.createElement('div');
    statusSpan.id = 'statusmsg';
    statusSpan.classList.add(success ? 'success' : 'error');
    statusSpan.textContent = status;
    statusDiv.appendChild(statusSpan);
    if (successText2) {
      const statusSpan2 = document.createElement('div');
      statusSpan2.id = 'statusmsg2';
      statusSpan2.classList.add('success');
      statusSpan2.textContent = successText2;
      statusDiv.appendChild(statusSpan2);
    }
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
    
      const ret = await fetch(configData?.submitUrl || `${args.api}deliver/`, {
        'method': 'POST',
        'headers': {
          'Content-Type': 'application/json',
          'X-Channel-ID': args.channel
        },
        'body': JSON.stringify({ textBody: message, email })
      });
      let retData;
      try {
        retData = await ret.json();
      } catch (error) {
        throw new Error('Invalid response from server');
      }
      
      if (!retData.success) throw new Error(retData.detail || 'Unknown error');
  
      setStatus(successText, true);

      feedbackText.remove();
      emailInput.remove();
      submitBtn.remove();
      
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Unknown error', false);
    }
  });

  const closeWidget = () => {
    shadowRoot.host.remove();
    triggerEvent(widgetName, 'closed');
  }

  closeBtn.addEventListener('click', closeWidget);
  listenEvent(widgetName, 'close', null, false, closeWidget);

  document.body.appendChild(shadowContainer);

  feedbackText.focus();

  triggerEvent(widgetName, 'opened');
  
});

installHook(widgetName);