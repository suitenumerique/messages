type WidgetEvent = [string, string, any];

type EventArray = Array<WidgetEvent> & { _loaded?: Record<string, number> };

declare global {
    var _stmsg_widget: EventArray;
}

export const getLoaded = (widgetName: string) => {
    return window._stmsg_widget?._loaded?.[widgetName];
}

export const setLoaded = (widgetName: string, status: number) => {
    if (!window._stmsg_widget?._loaded) return;
    window._stmsg_widget._loaded[widgetName] = status;
}

// Replace the push method of the _stmsg_widget array used for communication between the widget and the page
export const installHook = (widgetName: string, namespace: string) => {
    
    if (!window._stmsg_widget) {
        window._stmsg_widget = [] as EventArray;
    }
    const W = window._stmsg_widget;

    // Keep track of the loaded state of each widget
    // 0: not loaded
    // 1: loading
    // 2: loaded
    if (!W._loaded) {
        W._loaded = {} as Record<string, number>;
    }

    if (getLoaded(widgetName) !== 2) {
        // Replace the push method of the _stmsg_widget array used for communication between the widget and the page
        W.push = (elt: WidgetEvent): number => {
            // If the target widget is loaded, fire the event
            if (getLoaded(elt[0]) === 2) {
                const event = new CustomEvent(`${namespace}-${elt[0]}-${elt[1]}`, { detail: elt[2] });
                document.dispatchEvent(event);
            // If not, actually add to the queue
            } else {
                W[W.length] = elt;
            }
            return W.length;
        }
        setLoaded(widgetName, 2);

        // Empty the existing array and re-push all events that were received before the hook was installed
        for (const evt of W.splice(0, W.length)) {
            W.push(evt);
        }
    }

    // Finally, fire an event to signal that we are loaded
    document.dispatchEvent(new CustomEvent(`${namespace}-${widgetName}-loaded`));

}

// Loads another widget from the same directory
export const injectScript = (url: string) => {
    const newScript = document.createElement('script');
    newScript.src = url;
    newScript.type = 'module';
    newScript.defer = true;
    document.body.appendChild(newScript);
}