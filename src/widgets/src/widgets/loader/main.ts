import styles from './styles.css?inline'
import { createShadowWidget } from '../../shared/shadow-dom'
import icon from './icon.svg?raw'
import { injectScript, installHook } from '../../shared/script'

const widgetName = "loader";
const widgetPrefix = `stmsg-widget-${widgetName}`;

document.addEventListener(`${widgetPrefix}-init`, (e) => {

    const args = (e as CustomEvent).detail || {};

    const htmlContent = `<div><button type="button" aria-label="${(args.label || 'Load widget').replace(/"/g, '\\"')}">${icon}</button></div>`;

    // Create shadow DOM widget
    const shadowRoot = createShadowWidget(widgetPrefix, htmlContent, styles)
    
    if (shadowRoot) {
        const btn = shadowRoot.querySelector<HTMLButtonElement>('button')!
    
        btn.addEventListener('click', () => {
            // Add loading state
            btn.classList.add('loading')
    
            const targetWidget = args.load?.widget || 'feedback';
    
            // TODO timeout? error?
            // TODO: close again on click
            // TODO: allow open again after close
            // TODO: change icon when opened
            document.addEventListener(`stmsg-widget-${targetWidget}-loaded`, () => {
                btn.classList.remove('loading')
                window._stmsg_widget.push([targetWidget, "init", args.load]);
            })
            injectScript(args.load.url);
        })
    }
    
});

installHook(widgetPrefix);