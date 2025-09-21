import styles from './styles.css?inline'
import { createShadowWidget } from '../../shared/shadow-dom'
import icon from './icon.svg?raw'
import { injectScript, installHook, getLoaded, setLoaded } from '../../shared/script'
import { triggerEvent, listenEvent } from '../../shared/events'

const widgetName = "loader";

// The init event is sent from the embedding code
listenEvent(widgetName, 'init', null, false, (args) => {

    const htmlContent = `<div><button type="button" aria-label="${(args.label || 'Load widget').replace(/"/g, '\\"')}">${icon}</button></div>`;

    // Create shadow DOM widget
    const shadowRoot = createShadowWidget(widgetName, htmlContent, styles)
    
    if (shadowRoot) {
        const btn = shadowRoot.querySelector<HTMLButtonElement>('button')!
    
        // TODO timeout? error?
        btn.addEventListener('click', () => {
    
            const targetWidget = args.widget || 'feedback';

            if (btn.classList.contains('opened')) {
                triggerEvent(targetWidget, 'close');
                return;
            }

            // Add loading state to the UI
            btn.classList.add('loading')
    
            listenEvent(targetWidget, 'closed', null, false, () => {
                btn.classList.remove('opened')
            })
            listenEvent(targetWidget, 'opened', null, false, () => {
                btn.classList.add('opened')
            })

            const loadedCallback = () => {
                btn.classList.remove('loading')
                window._stmsg_widget.push([targetWidget, "init", args.params]);
            }

            if (getLoaded(targetWidget) === 2) {
                loadedCallback();
            } else {
                listenEvent(targetWidget, 'loaded', null, true, loadedCallback);
                // If it isn't even loading, we need to inject the script
                if (!getLoaded(targetWidget)) {
                    injectScript(args.script, args.scriptType || "");
                    setLoaded(targetWidget, 1);
                }
            }

        })
    }
    
});

installHook(widgetName);