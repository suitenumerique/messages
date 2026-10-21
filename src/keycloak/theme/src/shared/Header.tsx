import type { ComponentProps } from "react";
import { Header as DsfrHeader } from "@codegouvfr/react-dsfr/Header";
import type { Brand } from "./brand";

type Props = Brand & {
  quickAccessItems?: ComponentProps<typeof DsfrHeader>["quickAccessItems"];
};

export function Header(props: Props) {
  const { homeUrl, serviceTitle, brandTopHtml, quickAccessItems } = props;
  return (
    <DsfrHeader
      brandTop={<span dangerouslySetInnerHTML={{ __html: brandTopHtml }} />}
      serviceTitle={serviceTitle}
      homeLinkProps={{ href: homeUrl, title: serviceTitle }}
      quickAccessItems={quickAccessItems}
    />
  );
}
