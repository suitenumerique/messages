import { HeaderProps } from "@gouvfr-lasuite/ui-kit";
import { useAuth } from "@/features/auth";
import { AuthenticatedHeader } from "./authenticated";
import { AnonymousHeader } from "./anonymous";

type ProxyHeaderProps = HeaderProps & {
  hideSearch?: boolean;
}

export const Header = (props: ProxyHeaderProps) => {
  const { user } = useAuth();

  if (user) {
    return <AuthenticatedHeader {...props} />;
  }

  return <AnonymousHeader {...props} />;
};
