import { IndianRupee, Package, Receipt, ShoppingCart } from "lucide-react";
import { api } from "../api/endpoints";
import { useApi } from "../hooks/useApi";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { StatCard } from "../components/ui/StatCard";
import { AsyncContent, EmptyState } from "../components/ui/States";
import { DataTable } from "../components/ui/DataTable";
import { ColumnChart, HBarChart, TrendChart } from "../components/ui/charts";
import { formatCompactMoney, formatInt, formatMoney, formatPct } from "../utils/format";

export default function Analytics() {
  const revenue = useApi((s) => api.revenue(s));
  const monthly = useApi((s) => api.monthlySales(s));
  const categories = useApi((s) => api.categorySales(s));
  const products = useApi((s) => api.topProducts(10, s));
  const customers = useApi((s) => api.topCustomers(10, s));

  return (
    <>
      <PageHeader title="Analytics" description="Revenue figures exclude cancelled and returned orders." />
      <div className="space-y-6">
        <AsyncContent state={revenue}>
          {(r) => (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <StatCard icon={IndianRupee} label="Total net revenue" value={formatCompactMoney(r.total_revenue)} hint={`gross ${formatCompactMoney(r.gross_sales)} − discounts ${formatCompactMoney(r.discounts)}`} />
                <StatCard icon={ShoppingCart} label="Orders" value={formatInt(r.orders)} hint={`${formatInt(r.order_lines)} order lines`} />
                <StatCard icon={Receipt} label="Average order value" value={formatMoney(r.average_order_value)} hint="aggregated per order first" />
                <StatCard icon={Package} label="Units sold" value={formatInt(r.units_sold)} />
              </div>
              <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                <Card title="Yearly sales" description="with YoY growth">
                  {r.yearly.length ? (
                    <>
                      <ColumnChart data={r.yearly.map((y) => ({ ...y, label: String(y.year) }))} labelKey="label" valueKey="net_revenue" />
                      <div className="mt-3 flex flex-wrap gap-4 text-xs text-slate-600">
                        {r.yearly.map((y) => <span key={y.year}><b className="text-slate-800">{y.year}</b> {formatMoney(y.net_revenue)} {y.yoy_growth_pct != null && `(${y.yoy_growth_pct > 0 ? "+" : ""}${formatPct(y.yoy_growth_pct)})`}</span>)}
                      </div>
                    </>
                  ) : <EmptyState title="No sales loaded yet" />}
                </Card>
                <Card title="Order status analysis" description="all statuses">
                  <DataTable rows={r.order_status} rowKey={(s) => s.order_status} columns={[
                    { key: "order_status", header: "Status" },
                    { key: "orders", header: "Orders", align: "right", render: (s) => formatInt(s.orders) },
                    { key: "pct_of_orders", header: "% of orders", align: "right", render: (s) => formatPct(s.pct_of_orders) },
                    { key: "order_value", header: "Order value", align: "right", render: (s) => formatMoney(s.order_value) },
                  ]} />
                </Card>
              </div>
              <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                <Card title="City-wise sales" description="top 10, city at time of sale">
                  {r.cities.length ? <HBarChart data={r.cities.slice(0, 10)} labelKey="city" valueKey="net_revenue" /> : <EmptyState title="No sales loaded yet" />}
                </Card>
                <Card title="Revenue by payment method">
                  {r.payment_methods.length ? <HBarChart data={r.payment_methods} labelKey="payment_method" valueKey="net_revenue" /> : <EmptyState title="No sales loaded yet" />}
                </Card>
              </div>
            </div>
          )}
        </AsyncContent>

        <Card title="Monthly sales">
          <AsyncContent state={monthly} isEmpty={(m) => m.length === 0} empty={<EmptyState title="No sales loaded yet" />}>
            {(m) => <TrendChart data={m} xKey="period" yKey="net_revenue" height={300} />}
          </AsyncContent>
        </Card>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <Card title="Category-wise sales">
            <AsyncContent state={categories} isEmpty={(c) => c.length === 0} empty={<EmptyState title="No sales loaded yet" />}>
              {(c) => (
                <>
                  <HBarChart data={c} labelKey="category" valueKey="net_revenue" />
                  <DataTable rows={c} rowKey={(x) => x.category} columns={[
                    { key: "category", header: "Category" },
                    { key: "units_sold", header: "Units", align: "right", render: (x) => formatInt(x.units_sold) },
                    { key: "net_revenue", header: "Revenue", align: "right", render: (x) => formatMoney(x.net_revenue) },
                    { key: "revenue_share_pct", header: "Share", align: "right", render: (x) => formatPct(x.revenue_share_pct) },
                  ]} />
                </>
              )}
            </AsyncContent>
          </Card>
          <div className="space-y-6">
            <Card title="Top products" description="by units">
              <AsyncContent state={products}>
                {(p) => <DataTable rows={p} rowKey={(x) => x.product_id} emptyTitle="No sales loaded yet" columns={[
                  { key: "product_name", header: "Product", render: (x) => <span title={`${x.product_id} · ${x.category}`}>{x.product_name}</span> },
                  { key: "units_sold", header: "Units", align: "right", render: (x) => formatInt(x.units_sold) },
                  { key: "net_revenue", header: "Revenue", align: "right", render: (x) => formatMoney(x.net_revenue) },
                ]} />}
              </AsyncContent>
            </Card>
            <Card title="Top customers" description="by total spend">
              <AsyncContent state={customers}>
                {(c) => <DataTable rows={c} rowKey={(x) => x.customer_id} emptyTitle="No sales loaded yet" columns={[
                  { key: "customer_name", header: "Customer", render: (x) => <span title={x.customer_id}>{x.customer_name}</span> },
                  { key: "current_city", header: "Current city" },
                  { key: "orders", header: "Orders", align: "right", render: (x) => formatInt(x.orders) },
                  { key: "total_spent", header: "Total spent", align: "right", render: (x) => formatMoney(x.total_spent) },
                ]} />}
              </AsyncContent>
            </Card>
          </div>
        </div>
      </div>
    </>
  );
}
