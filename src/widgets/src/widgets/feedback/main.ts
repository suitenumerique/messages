import styles from './styles.css?inline'
import { createShadowWidget } from '../../shared/shadow-dom'
import { installHook } from '../../shared/script'
import { submitFeedback } from './submit'

const widgetName = "feedback";
const namespace = `stmsg-widget`;

document.addEventListener(`${namespace}-${widgetName}-init`, (e) => {

  const args = (e as CustomEvent).detail || {};
  const title = args.title || 'Feedback';

  if (!args.api) {
    console.error("Feedback widget requires an API URL");
    return;
  }

  const htmlContent = `
    <div class="wrapper">
      <div class="header">
        <span>${title}</span>
        <button class="close-btn" id="close">×</button>
      </div>
      <div class="content">
        <textarea id="feedback-text" placeholder="Share your feedback..."></textarea>
        <button type="submit" id="submit">Send Feedback</button>
        <div id="status" class="status"></div>
      </div>
    </div>
  `

  // Create shadow DOM widget
  const shadowRoot = createShadowWidget(widgetName, htmlContent, styles);
  document.dispatchEvent(new CustomEvent(`${namespace}-${widgetName}-opened`));

  const submitBtn = shadowRoot.querySelector<HTMLButtonElement>('#submit')!
  const feedbackText = shadowRoot.querySelector<HTMLTextAreaElement>('#feedback-text')!
  const statusDiv = shadowRoot.querySelector<HTMLDivElement>('#status')!
  const closeBtn = shadowRoot.querySelector<HTMLButtonElement>('#close')!

  submitBtn.addEventListener('click', () => {
    const feedback = feedbackText.value.trim()
    if (feedback) {
      statusDiv.innerHTML = '<span class="success">Thank you for your feedback!</span>'
      feedbackText.value = ''
      submitFeedback(feedback, args.api)
    } else {
      statusDiv.innerHTML = '<span class="error">Please enter some feedback.</span>'
    }
  });

  const closeWidget = () => {
    shadowRoot.host.remove();
    document.dispatchEvent(new CustomEvent(`${namespace}-${widgetName}-closed`));
  }

  closeBtn.addEventListener('click', closeWidget);
  document.addEventListener(`${namespace}-${widgetName}-close`, closeWidget);

});

installHook(widgetName, namespace);