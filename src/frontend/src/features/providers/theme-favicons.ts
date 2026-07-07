/**
 * Inject theme-aware SVG favicons into <head>. `index.html` only ships the
 * fixed PWA bitmap icons, which are not theme-aware. Called during bootstrap
 * so the favicon is set before the first paint.
 */
export const installThemeFavicons = (theme: string) => {
  const variants: Array<{ media: string; href: string }> = [
    { media: "(prefers-color-scheme: light)", href: `/images/${theme}/favicon-light.svg` },
    { media: "(prefers-color-scheme: dark)", href: `/images/${theme}/favicon-dark.svg` },
  ];
  const links = variants.map(({ media, href }) => {
    const el = document.createElement("link");
    el.rel = "icon";
    el.type = "image/svg+xml";
    el.media = media;
    el.href = href;
    document.head.appendChild(el);
    return el;
  });
  return () => links.forEach((el) => el.remove());
};
