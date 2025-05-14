import { SearchInput } from "@/features/forms/components/search-input";
import { DropdownMenuOption, LanguagePicker } from "@gouvfr-lasuite/ui-kit";

import { Button, useCunningham } from "@openfun/cunningham-react";

export type HeaderProps = {
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
  languages?: DropdownMenuOption[];
  onTogglePanel?: () => void;
  isPanelOpen?: boolean;
};

export const Header = ({
  leftIcon,
  rightIcon,
  languages,
  onTogglePanel,
  isPanelOpen,
}: HeaderProps) => {
  const { t } = useCunningham();
  return (
    <div className="c__header">
      <div className="c__header__toggle-menu">
        <Button
          size="medium"
          onClick={onTogglePanel}
          aria-label={isPanelOpen ? t("Close the menu") : t("Open the menu")}
          color="tertiary-text"
          icon={
            <span className="material-icons clr-primary-800">
              {isPanelOpen ? "close" : "menu"}
            </span>
          }
        />
      </div>
      <div className="c__header__left">{leftIcon}</div>
      <div className="c__header__center">
        <SearchInput />
      </div>
      <div className="c__header__right">
        {languages && (
          <div className="c__header__right__language-picker">
            <LanguagePicker languages={languages} />
          </div>
        )}

        {rightIcon}
      </div>
    </div>
  );
};
