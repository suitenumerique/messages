import { HeaderProps } from "@gouvfr-lasuite/ui-kit";
import { LanguagePicker } from "../language-picker";
import { LagaufreButton } from "@/features/ui/components/lagaufre";
import { isNativePlatform } from "@/features/native/platform";


export const AnonymousHeader = ({
  leftIcon,
}: HeaderProps) => {
  return (
    <div className="c__header c__header--anonymous">
      <div className="c__header__left">
        {leftIcon}
      </div>
      <div className="c__header__right">
          <LanguagePicker />
          { !isNativePlatform() && <LagaufreButton />}
      </div>
    </div>
  );
};
