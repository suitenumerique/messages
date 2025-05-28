import { HeaderProps } from "@gouvfr-lasuite/ui-kit";
import { useAuth } from "@/features/auth";
import { AuthenticatedHeader } from "./authenticated";
import { AnonymousHeader } from "./anonymous";

export const Header = (props: HeaderProps) => {
  const { user } = useAuth();

  if (user) {
    return <AuthenticatedHeader {...props} />;
  }

  return <AnonymousHeader {...props} />;
};
