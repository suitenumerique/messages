import { AdminLayout } from "@/features/layouts/components/admin/admin-layout";
import { useState, useEffect, useRef } from "react";
import { Button, DataGrid } from "@openfun/cunningham-react";
import { useTranslation, Trans } from "react-i18next";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { MailDomainAdmin, DNSRecordCheck } from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import { useMaildomainsCheckDnsCreate, useMaildomainsRetrieve } from "@/features/api/gen/maildomains/maildomains";
import { useRouter } from "next/router";
import { CopyableInput } from "@/features/ui/components/copyable-input";
import { Icon } from "@gouvfr-lasuite/ui-kit";

type DNSRecordWithId = DNSRecordCheck & { id: string };

type AdminDNSDataGridProps = {
  domain: MailDomainAdmin;
  dnsRecords: DNSRecordWithId[];
  isLoading: boolean;
  error: string | null;
}

function AdminDNSDataGrid({ domain, dnsRecords, isLoading, error }: AdminDNSDataGridProps) {
  const { t } = useTranslation();
  const [justCopied, setJustCopied] = useState(false);

  const getStatusColor = (status: string) => {
    switch (status) {
      case "correct":
        return "var(--c--theme--colors--success-600)";
      case "incorrect":
        return "var(--c--theme--colors--warning-600)";
      case "missing":
        return "var(--c--theme--colors--danger-600)";
      default:
        return "var(--c--theme--colors--info-600)";
    }
  };

  const getStatusText = (status: string) => {
    switch (status) {
      case "correct":
        return t("Correct");
      case "incorrect":
        return t("Incorrect");
      case "missing":
        return t("Missing");
      default:
        return t("Unknown");
    }
  };

  const columns = [
    {
      id: "type",
      headerName: t("Type"),
      size: 80,
      renderCell: ({ row }: { row: DNSRecordWithId }) => (
        <strong>
          {row.type.toUpperCase()}
        </strong>
      ),
    },
    {
      id: "target",
      headerName: t("Target"),
      size: 200,
      renderCell: ({ row }: { row: DNSRecordWithId }) => (
        <CopyableInput value={row.target || "@"} />
      ),
    },
    {
      id: "value",
      headerName: t("Value"),
      renderCell: ({ row }: { row: DNSRecordWithId }) => (
        <CopyableInput value={row.value} />
      ),
    },
    {
      id: "status",
      headerName: t("Current status"),
      size: 120,
      renderCell: ({ row }: { row: DNSRecordWithId }) => {
        const status = row._check?.status || "unknown";
        return (
          <span style={{ color: getStatusColor(status) }}>
            {getStatusText(status)}
          </span>
        );
      },
    },
  ];

  if (isLoading) {
    return (
      <div className="admin-data-grid">
        <Banner type="info" icon={<Spinner />}>
          {t("Checking DNS records...")}
        </Banner>
      </div>
    );
  }

  if (error) {
    return (
      <div className="admin-data-grid">
        <Banner type="error">
          {error}
        </Banner>
      </div>
    );
  }

  if (dnsRecords.length === 0) {
    return (
      <div className="admin-data-grid">
        <Banner type="info">
          {t("No DNS records found")}
        </Banner>
      </div>
    );
  }

  return (
    <div className="admin-data-grid">
      <div style={{ marginBottom: "1.5rem" }}>
        <Banner type="info">
          <Trans i18nKey="These DNS records must be configured on the domain <strong>{{domain}}</strong> for the mail system to work properly. If you don't know how to update them, please contact your technical service provider or system administrator." values={{ domain: domain.name }} components={{ strong: <strong /> }} />
        </Banner>
      </div>
      <DataGrid
        columns={columns}
        rows={dnsRecords}
      />
      <div style={{ marginTop: "1.5rem" }}>
        <Button icon={justCopied?<Icon name="check" />:<Icon name="content_copy" />} color="secondary" onClick={() => {
          const headerRow = t("Type") + "\t" + t("Target") + "\t" + t("Value") + "\n";
          const records = headerRow + dnsRecords.map((record) => `${record.type.toUpperCase()}\t${record.target || "@"}\t${record.value}`).join("\n");
          navigator.clipboard.writeText(records);
          setJustCopied(true);
          setTimeout(() => {
            setJustCopied(false);
          }, 2000);
        }}>
          {t("Copy all DNS records")}
        </Button>
      </div>
    </div>
  );
}

/**
 * Admin page which list expected DNS records for a given domain.
 */
export default function AdminDNSPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const maildomainId = router.query.maildomainId as string;
  const [dnsRecords, setDnsRecords] = useState<DNSRecordWithId[]>([]);
  const hasInitialCheck = useRef(false);

  // Fetch the specific domain
  const { data: domainData, isLoading: domainLoading, error: domainError } = useMaildomainsRetrieve(maildomainId, {
    query: {
      enabled: !!maildomainId,
    },
  });

  const selectedMailDomain = domainData?.data;

  // DNS check mutation
  const dnsCheckMutation = useMaildomainsCheckDnsCreate({
    mutation: {
      onSuccess: (response) => {
        const data = response.data;
        const recordsWithIds = data.records.map((record, index) => ({
          ...record,
          id: `${record.type}-${record.target}-${index}`,
        }));
        setDnsRecords(recordsWithIds);
      },
      onError: (error) => {
        console.error('DNS check failed:', error);
      },
    },
  });

  // Auto-check DNS when page loads (only once)
  useEffect(() => {
    if (selectedMailDomain && !hasInitialCheck.current && !dnsCheckMutation.isPending) {
      hasInitialCheck.current = true;
      dnsCheckMutation.mutate({ maildomainPk: selectedMailDomain.id });
    }
  }, [selectedMailDomain, dnsCheckMutation]);

  const handleCheckDNS = () => {
    if (!selectedMailDomain) return;
    dnsCheckMutation.mutate({ maildomainPk: selectedMailDomain.id });
  };

  if (domainLoading) {
    return (
        <div className="admin-page__loading">
          <Spinner />
        </div>
    )
  }

  if (domainError || !selectedMailDomain) {
    return (
        <Banner type="error">
          {t("Domain not found")}
        </Banner>
    );
  }

  const isChecking = dnsCheckMutation.isPending;
  const error = dnsCheckMutation.error ? t("Error while checking DNS records") : null;

  return (
    <AdminLayout
      currentTab="dns"
      actions={
        <>
          <Button
            color="primary"
            onClick={handleCheckDNS}
            disabled={dnsCheckMutation.isPending}
          >
            {dnsCheckMutation.isPending ? t("Checking DNS records...") : t("Check DNS again")}
          </Button>
        </>
      }
    >
      <AdminDNSDataGrid
        domain={selectedMailDomain}
        dnsRecords={dnsRecords}
        isLoading={isChecking}
        error={error}
      />
    </AdminLayout>
  );
}
