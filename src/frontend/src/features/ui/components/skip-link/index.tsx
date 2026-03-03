import { useTranslation } from "react-i18next";

// Use this id to flag an element as the main content of the view
export const SKIP_LINK_TARGET_ID = "skip-link-target";

type SkipLinkProps = {
  onClick?: () => void;
};

/**
 * An a11y component that act like a shorcut to go the main content
 * of the view by skipping all other elements before.
 */
export const SkipLink = ({ onClick }: SkipLinkProps) => {
  const { t } = useTranslation();
  return (
    <a href={`#${SKIP_LINK_TARGET_ID}`} className="c__skip-link" onClick={onClick}>
      {t("Skip to main content")}
    </a>
  );
};
