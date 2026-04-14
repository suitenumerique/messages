import { createFileRoute, useSearch } from "@tanstack/react-router";

import { useAuth } from "@/features/auth";
import { MainLayout } from "@/features/layouts/components/main";
import { MosaLoginPage } from "@/features/home";
import { useDocumentTitle } from "@/hooks/use-document-title";

const HomePage = () => {
  const { user } = useAuth();
  const searchParams = useSearch({ strict: false }) as { next?: string };

  useDocumentTitle();

  if (user) {
    return <MainLayout />;
  }

  const nextParam = searchParams.next;
  return <MosaLoginPage next={typeof nextParam === "string" ? nextParam : undefined} />;
};

export const Route = createFileRoute("/")({
  component: HomePage,
});
