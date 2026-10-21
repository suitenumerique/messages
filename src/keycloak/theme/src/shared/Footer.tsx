import type { Brand } from "./brand";

export function Footer(
  props: Pick<Brand, "brandTopHtml" | "homeUrl" | "serviceTitle">,
) {
  const { brandTopHtml, homeUrl, serviceTitle } = props;
  return (
    <footer className="fr-footer lst-footer" role="contentinfo" id="footer">
      <div className="fr-container">
        <div className="fr-footer__body">
          <div className="fr-footer__brand fr-enlarge-link">
            <a href={homeUrl} title={serviceTitle}>
              <p
                className="fr-logo"
                dangerouslySetInnerHTML={{ __html: brandTopHtml }}
              />
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
