/* eslint @typescript-eslint/no-explicit-any:0 */
const NAMESPACE = `stmsg-widget`;

export const triggerEvent = (
  widgetName: string,
  eventName: string,
  detail?: Record<string, any>,
  root?: Document | HTMLElement | null,
) => {
  return (root || document).dispatchEvent(
    new CustomEvent(`${NAMESPACE}-${widgetName}-${eventName}`, detail ? { detail } : undefined),
  );
};

export const listenEvent = (
  widgetName: string,
  eventName: string,
  root: Document | HTMLElement | null,
  once: boolean,
  callback: (data: any) => void,
) => {
  const cb = (e: any) => callback(e.detail);
  (root || document).addEventListener(`${NAMESPACE}-${widgetName}-${eventName}`, cb, once ? { once: true } : undefined);
  return () =>
    (root || document).removeEventListener(
      `${NAMESPACE}-${widgetName}-${eventName}`,
      cb,
      once ? ({ once: true } as EventListenerOptions) : undefined,
    );
};
