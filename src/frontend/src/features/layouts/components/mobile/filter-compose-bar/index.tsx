import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { XMark, Zoom } from "@gouvfr-lasuite/ui-kit/icons";
import { ThreadPanelFilter } from "@/features/layouts/components/thread-panel/components/thread-panel-filter";
import { SearchFiltersModal } from "@/features/forms/components/search-filters-modal";
import { useSearchQuery } from "@/features/forms/components/search-input/use-search-query";
import { useComposeMessage } from "@/features/message/use-compose-message";
import { isNativePlatform } from "@/features/native/platform";
import { MobileBottomBar } from "../bottom-bar";
import { Icon } from "@/features/ui/components/icon";

/**
 * Native-only bottom bar for the thread-list view: the quick filter on the left,
 * a thumb-reachable compose button on the right. While a search is applied, a
 * summary of it sits between them: it is the only entry point to the search
 * form in that mode (the header trigger is hidden) and clears the search in a
 * single tap.
 */
export const MobileFilterComposeBar = () => {
  const { t } = useTranslation();
  const { canWriteMessages, goToNewMessage, selectedMailbox } = useComposeMessage();
  const { query, isSearching, submit, reset } = useSearchQuery();
  const [showFilters, setShowFilters] = useState(false);

  if (!isNativePlatform() || !selectedMailbox) return null;

  const handleSubmit = (nextQuery: string) => {
    setShowFilters(false);
    submit(nextQuery);
  };

  return (
    <MobileBottomBar className="mobile-filter-compose-bar">
      <ThreadPanelFilter />
      {isSearching && (
        <div className="mobile-filter-compose-bar__search">
          <button
            type="button"
            className="mobile-filter-compose-bar__search-summary"
            onClick={() => setShowFilters(true)}
            aria-label={t("Edit search")}
          >
            <Icon icon={Zoom} size={18} />
            <span className="mobile-filter-compose-bar__search-query">{query}</span>
          </button>
          <Button
            className="mobile-filter-compose-bar__search-reset"
            color="neutral"
            variant="tertiary"
            size="small"
            onClick={reset}
            icon={<Icon icon={XMark} />}
            aria-label={t("Reset")}
          />
          <SearchFiltersModal
            isOpen={showFilters}
            onClose={() => setShowFilters(false)}
            query={query}
            onSubmit={handleSubmit}
          />
        </div>
      )}
      <Button
        className="mobile-filter-compose-bar__compose"
        onClick={goToNewMessage}
        href={`/mailbox/${selectedMailbox.id}/new`}
        icon={<Icon name="mail-plus" />}
        disabled={!canWriteMessages}
        aria-label={t("New message")}
        variant="tertiary"
      />
    </MobileBottomBar>
  );
};

export default MobileFilterComposeBar;
