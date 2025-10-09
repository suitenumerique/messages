// Shared utility for creating shadow DOM widgets
export function createShadowWidget(widgetName: string, htmlContent: string, cssContent: string): HTMLDivElement {
  const id = `stmsg-widget-${widgetName}-shadow`;
  // Check if widget already exists
  const existingWidget = document.getElementById(id);
  if (existingWidget) {
    existingWidget.remove();
  }

  // Create container element
  const container = document.createElement("div");
  container.id = id;

  // Create shadow root
  const shadow = container.attachShadow({ mode: "open" });

  // Create style element for scoped CSS
  const style = document.createElement("style");
  style.textContent = cssContent;

  // Create content element
  const content = document.createElement("div");
  content.innerHTML = htmlContent;

  // Append style and content to shadow DOM
  shadow.appendChild(style);
  shadow.appendChild(content);

  return container;
}
