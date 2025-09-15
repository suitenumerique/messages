const NAMESPACE = `stmsg-widget`;

export const triggerEvent = (widgetName: string, eventName: string, detail?: any, root?: any) => {
    return (root || document).dispatchEvent(new CustomEvent(`${NAMESPACE}-${widgetName}-${eventName}`, detail ? { detail } : undefined));
}

export const listenEvent = (widgetName: string, eventName: string, root: any, once: boolean, callback: (data: any) => void) => {
    return (root || document).addEventListener(`${NAMESPACE}-${widgetName}-${eventName}`, callback, once ? { once: true } : undefined);
}

export const removeEvent = (widgetName: string, eventName: string, root: any, callback: (data: any) => void) => {
    return (root || document).removeEventListener(`${NAMESPACE}-${widgetName}-${eventName}`, callback);
}