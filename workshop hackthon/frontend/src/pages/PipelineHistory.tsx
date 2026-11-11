import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { api } from "../api/endpoints";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { AsyncContent, EmptyState } from "../components/ui/States";
import { DataTable } from "../components/ui/DataTable";
import { StatusBadge } from "../components/ui/StatusBadge";
import { Button } from "../components/ui/Button";
import { formatDateTime, formatInt, formatSeconds, shortRunId } from "../utils/format";
import type { PipelineRun } from "../types/api";

export default function PipelineHistory() {
  const state = useApi((signal) => api.pipelineHistory(100, signal));
  return (
    <>
      <PageHeader title="Run history" description="Every pipeline run, newest first."
        actions={<Button variant="secondary" icon={<RefreshCw className="h-4 w-4" />} onClick={state.reload}>Refresh</Button>} />
      <Card>
        <AsyncContent state={state} isEmpty={(r) => r.length === 0}
          empty={<EmptyState title="No pipeline runs yet"><Link to="/pipeline" className="text-brand-600 hover:underline">Run the pipeline</Link> to create the first entry.</EmptyState>}>
          {(runs) => (
            <DataTable<PipelineRun> rows={runs} rowKey={(r) => r.run_id} maxHeight="70vh" columns={[
              { key: "run_id", header: "Run ID", render: (r) => <Link to={`/pipeline?run=${encodeURIComponent(r.run_id)}`} className="font-mono text-xs text-brand-600 hover:underline">{shortRunId(r.run_id)}</Link> },
              { key: "status", header: "Status", render: (r) => <StatusBadge status={r.status} /> },
              { key: "start_time", header: "Start", render: (r) => formatDateTime(r.start_time) },
              { key: "end_time", header: "End", render: (r) => formatDateTime(r.end_time) },
              { key: "duration_seconds", header: "Duration", align: "right", render: (r) => formatSeconds(r.duration_seconds) },
              { key: "input_count", header: "Input", align: "right", render: (r) => formatInt(r.input_count) },
              { key: "valid_count", header: "Valid", align: "right", render: (r) => formatInt(r.valid_count) },
              { key: "rejected_count", header: "Rejected", align: "right", render: (r) => formatInt(r.rejected_count) },
              { key: "inserted_count", header: "Inserted", align: "right", render: (r) => formatInt(r.inserted_count) },
              { key: "updated_count", header: "Updated", align: "right", render: (r) => formatInt(r.updated_count) },
              { key: "error_message", header: "Error", wrap: true, render: (r) => <span className="text-xs text-red-700">{r.error_message ?? ""}</span> },
            ]} />
          )}
        </AsyncContent>
      </Card>
    </>
  );
}
