import { triggerEvent } from "./events";

type WidgetEvent = [string, string, Record<string, unknown> | undefined];

type EventArray = Array<WidgetEvent> & { _loaded?: Record<string, number> };

declare global {
  var _stmsg_widget: EventArray;
}

// This could have been an enum but we want to support erasableSyntaxOnly TS settings
export const STATE_NOT_LOADED = 0;
export const STATE_LOADING = 1;
export const STATE_LOADED = 2;

export const getLoaded = (widgetName: string) => {
  return window._stmsg_widget?._loaded?.[widgetName];
};

export const setLoaded = (widgetName: string, status: number) => {
  if (!window._stmsg_widget?._loaded) return;
  window._stmsg_widget._loaded[widgetName] = status;
};

// Replace the push method of the _stmsg_widget array used for communication between the widget and the page
export const installHook = (widgetName: string) => {
  if (!window._stmsg_widget) {
    window._stmsg_widget = [] as EventArray;
  }
  const W = window._stmsg_widget;

  // Keep track of the loaded state of each widget
  if (!W._loaded) {
    W._loaded = {} as Record<string, number>;
  }

  if (getLoaded(widgetName) !== STATE_LOADED) {
    // Replace the push method of the _stmsg_widget array used for communication between the widget and the page
    W.push = ((...elts: WidgetEvent[]): number => {
      for (const elt of elts) {
        // If the target widget is loaded, fire the event
        if (getLoaded(elt[0]) === STATE_LOADED) {
          triggerEvent(elt[0], elt[1], elt[2]);
        } else {
          W[W.length] = elt;
        }
      }
      return W.length;
    }) as typeof Array.prototype.push;

    setLoaded(widgetName, STATE_LOADED);

    // Empty the existing array and re-push all events that were received before the hook was installed
    for (const evt of W.splice(0, W.length)) {
      W.push(evt);
    }
  }

  // Finally, fire an event to signal that we are loaded
  triggerEvent(widgetName, "loaded");
};

// Loads another widget from the same directory
export const injectScript = (url: string, type: string = "") => {
  const newScript = document.createElement("script");
  newScript.src = url;
  newScript.type = type;
  newScript.defer = true;
  document.body.appendChild(newScript);
};
