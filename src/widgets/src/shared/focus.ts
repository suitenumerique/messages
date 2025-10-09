const isVisible = (element: HTMLElement) => {
  return element.offsetWidth > 0 && element.offsetHeight > 0;
};

export const trapFocus = (shadowRoot: ShadowRoot, container: HTMLElement, selector: string) => {
  const handleKeydown = (e: KeyboardEvent) => {
    if (e.key !== "Tab") return;

    const focusable = Array.from(container.querySelectorAll(selector)).filter((element) =>
      isVisible(element as HTMLElement),
    );
    if (focusable.length === 0) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.shiftKey && shadowRoot.activeElement === first) {
      e.preventDefault();
      (last as HTMLElement).focus();
    } else if (!e.shiftKey && shadowRoot.activeElement === last) {
      e.preventDefault();
      (first as HTMLElement).focus();
    }
  };

  container.addEventListener("keydown", handleKeydown);

  return () => {
    container.removeEventListener("keydown", handleKeydown);
  };
};

export const trapEscape = (cb: () => void) => {
  const handleKeydown = (e: KeyboardEvent) => {
    if (e.key !== "Escape") return;
    e.preventDefault();
    cb();
  };
  document.addEventListener("keydown", handleKeydown);
  return () => {
    document.removeEventListener("keydown", handleKeydown);
  };
};
