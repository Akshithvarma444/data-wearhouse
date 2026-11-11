import { RefreshCw } from "lucide-react";
import { api } from "../api/endpoints";
import { API_BASE_URL } from "../api/client";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { AsyncContent } from "../components/ui/States";
import { StatusBadge } from "../components/ui/StatusBadge";
import { Button } from "../components/ui/Button";
import { formatDateTime, formatInt } from "../utils/format";

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-1 border-b border-slate-100 py-2.5 last:border-0 sm:grid-cols-[220px_1fr]">
      <dt className="text-sm text-slate-500">{k}</dt>
      <dd className="min-w-0 break-all text-sm text-slate-800">{v}</dd>
    </div>
  );
}

export default function Settings() {
  const health = useApi((s) => api.health(s));
  const config = useApi((s) => api.config(s));
  return (
    <>
      <PageHeader title="Settings" description="Connection status and the app's configuration."
        actions={<Button variant="secondary" icon={<RefreshCw className="h-4 w-4" />} onClick={() => { health.reload(); config.reload(); }}>Re-check</Button>} />
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card title="API connection">
          <dl>
            <Row k="API base URL" v={<span className="font-mono text-xs">{API_BASE_URL}</span>} />
            <Row k="Status" v={health.error ? <StatusBadge status="FAILED" /> : health.data ? <StatusBadge status="SUCCESS" /> : "Checking…"} />
            {health.error && <Row k="Error" v={<span className="text-red-700">{health.error.message}</span>} />}
            {health.data && <Row k="API version" v={health.data.api_version} />}
            {health.data && <Row k="Server time" v={formatDateTime(health.data.time)} />}
          </dl>
        </Card>
        <Card title="Database health">
          <AsyncContent state={health}>
            {(h) => (
              <dl>
                <Row k="Connected" v={<StatusBadge status={h.database.connected ? "SUCCESS" : "FAILED"} />} />
                <Row k="Database" v={h.database.name ?? "–"} />
                <Row k="PostgreSQL version" v={h.database.server_version ?? "–"} />
                {h.database.error && <Row k="Error" v={<span className="text-red-700">{h.database.error}</span>} />}
              </dl>
            )}
          </AsyncContent>
        </Card>
        <Card title="Application configuration" className="xl:col-span-2">
          <AsyncContent state={config}>
            {(c) => (
              <dl>
                <Row k="Application" v={c.app_name} />
                <Row k="Environment" v={c.environment} />
                <Row k="Python" v={c.python_version} />
                <Row k="Database host / name" v={`${c.database_host} / ${c.database_name}`} />
                <Row k="Batch size" v={formatInt(c.batch_size)} />
                <Row k="Random seed" v={c.random_seed} />
                <Row k="Source system" v={c.source_system} />
                <Row k="Datasets directory" v={<span className="font-mono text-xs">{c.datasets_dir}</span>} />
                <Row k="Reports directory" v={<span className="font-mono text-xs">{c.reports_dir}</span>} />
                <Row k="Allowed CORS origins" v={c.cors_origins.join(", ")} />
              </dl>
            )}
          </AsyncContent>
        </Card>
      </div>
    </>
  );
}
