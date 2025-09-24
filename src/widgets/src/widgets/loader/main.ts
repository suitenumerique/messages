import styles from './styles.css?inline'
import { createShadowWidget } from '../../shared/shadow-dom'
import icon from './icon.svg?raw'
import { injectScript, installHook, getLoaded, setLoaded, STATE_LOADED, STATE_LOADING } from '../../shared/script'
import { triggerEvent, listenEvent } from '../../shared/events'
const widgetName = "loader";

// The init event is sent from the embedding code
listenEvent(widgetName, 'init', null, false, (args) => {

    const targetWidget = args.widget || 'feedback';

    const htmlContent = `<div><button type="button">${icon}</button></div>`;

    // Create shadow DOM widget
    const shadowContainer = createShadowWidget(widgetName, htmlContent, styles)
    const shadowRoot = shadowContainer.shadowRoot!;

    const btn = shadowRoot.querySelector<HTMLButtonElement>('button')!

    const ariaOpen = () => {
        btn.setAttribute('aria-label', String(args.closeLabel || 'Close widget'))
        btn.setAttribute('aria-expanded', 'true')
        // TODO: How could we set the aria-controls attribute too, given that we
        // have no id for the widget? Should we ask for it via an event?
    }
    const ariaClose = () => {
        btn.setAttribute('aria-label', String(args.label || 'Load widget'))
        btn.setAttribute('aria-expanded', 'false')
    }
    ariaClose();

    listenEvent(targetWidget, 'closed', null, false, () => {
        btn.classList.remove('opened')
        ariaClose();
    })
    listenEvent(targetWidget, 'opened', null, false, () => {
        btn.classList.add('opened')
        ariaOpen();
    })

    btn.addEventListener('click', () => {

        if (btn.classList.contains('opened')) {
            triggerEvent(targetWidget, 'close');
            return;
        }

        const loadTimeout = setTimeout(() => {
            btn.classList.remove('loading');
        }, 10000)

        // Add loading state to the UI
        btn.classList.add('loading')

        const loadedCallback = () => {
            clearTimeout(loadTimeout)
            btn.classList.remove('loading')
            window._stmsg_widget.push([targetWidget, "init", args.params]);
        }

        if (getLoaded(targetWidget) === STATE_LOADED) {
            loadedCallback();
        } else {
            listenEvent(targetWidget, 'loaded', null, true, loadedCallback);
            // If it isn't even loading, we need to inject the script
            if (!getLoaded(targetWidget)) {
                injectScript(args.script, args.scriptType || "");
                setLoaded(targetWidget, STATE_LOADING);
            }
        }

    })

    document.body.appendChild(shadowContainer);
    
});

installHook(widgetName);