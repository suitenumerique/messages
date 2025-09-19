const NAMESPACE = `stmsg-widget`;

export const triggerEvent = (widgetName: string, eventName: string, detail?: any, root?: any) => {
    return (root || document).dispatchEvent(new CustomEvent(`${NAMESPACE}-${widgetName}-${eventName}`, detail ? { detail } : undefined));
}

export const listenEvent = (widgetName: string, eventName: string, root: any, once: boolean, callback: (data: any) => void) => {
    const cb = (e: CustomEvent) => callback(e.detail);
    (root || document).addEventListener(`${NAMESPACE}-${widgetName}-${eventName}`, cb, once ? { once: true } : undefined);
    return () => (root || document).removeEventListener(`${NAMESPACE}-${widgetName}-${eventName}`, cb, once ? { once: true } : undefined);
}
