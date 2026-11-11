import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/layout/Layout";
import { LoadingState, EmptyState } from "./components/ui/States";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const DataSources = lazy(() => import("./pages/DataSources"));
const DataGenerator = lazy(() => import("./pages/DataGenerator"));
const Pipeline = lazy(() => import("./pages/Pipeline"));
const DataQuality = lazy(() => import("./pages/DataQuality"));
const DataWarehouse = lazy(() => import("./pages/DataWarehouse"));
const Analytics = lazy(() => import("./pages/Analytics"));
const PipelineHistory = lazy(() => import("./pages/PipelineHistory"));
const Reports = lazy(() => import("./pages/Reports"));
const Settings = lazy(() => import("./pages/Settings"));

export default function App() {
  return (
    <Suspense fallback={<LoadingState />}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="data-sources" element={<DataSources />} />
          <Route path="generator" element={<DataGenerator />} />
          <Route path="pipeline" element={<Pipeline />} />
          <Route path="quality" element={<DataQuality />} />
          <Route path="warehouse" element={<DataWarehouse />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="history" element={<PipelineHistory />} />
          <Route path="reports" element={<Reports />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<EmptyState title="Page not found" />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
