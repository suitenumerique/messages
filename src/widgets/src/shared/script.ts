type WidgetEvent = [string, string, any];

type EventArray = Array<WidgetEvent> & { _loaded?: boolean };

declare global {
    var _stmsg_widget: EventArray;
}

// Replace the push method of the _stmsg_widget array used for communication between the widget and the page
export const installHook = (widgetPrefix: string) => {
    
    if (!window._stmsg_widget) {
        window._stmsg_widget = [] as EventArray;
    }

    // TODO: refactor this so we can include multiple widget scripts in the same page and load independently
    if (!window._stmsg_widget._loaded) {
        window._stmsg_widget.push = (elt: WidgetEvent): number => {
            const event = new CustomEvent(`stmsg-widget-${elt[0]}-${elt[1]}`, { detail: elt[2] });
            document.dispatchEvent(event);
            return window._stmsg_widget.length;
        }
        window._stmsg_widget._loaded = true;

        // Empty the existing array and re-push all events that were received before the hook was installed
        for (const evt of window._stmsg_widget.splice(0, window._stmsg_widget.length)) {
            window._stmsg_widget.push(evt);
        }
    }

    // Finally, fire an event to signal that we are loaded
    document.dispatchEvent(new CustomEvent(`${widgetPrefix}-loaded`));

}

// Loads another widget from the same directory
export const injectScript = (url: string) => {
    const newScript = document.createElement('script');
    newScript.src = url;
    newScript.type = 'module';
    newScript.defer = true;
    document.body.appendChild(newScript);
}