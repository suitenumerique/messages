type ErrorPageProps = {
  statusCode?: number;
  title?: string;
};

/**
 * Minimal centered status page. Used as a fallback when a route guard or
 * permission check denies access (e.g. 403 on the admin views).
 */
export const ErrorPage = ({ statusCode, title }: ErrorPageProps) => (
  <div
    style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      padding: "4rem 1rem",
      gap: "0.5rem",
      textAlign: "center",
    }}
  >
    <h1 style={{ fontSize: "2rem", margin: 0 }}>{statusCode ?? "Error"}</h1>
    {title ? <p style={{ margin: 0 }}>{title}</p> : null}
  </div>
);
