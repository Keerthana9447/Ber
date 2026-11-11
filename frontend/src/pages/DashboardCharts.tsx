import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, EmptyState } from "../components/ui";
import { formatDate, formatINR } from "../lib/format";

type Num = string | number;
export type DashboardChartData = {
  sales_trend?: { date: string; amount: Num }[];
  purchase_trend?: { date: string; amount: Num }[];
  revenue_vs_expenses?: { period: string; revenue: Num; expenses: Num }[];
  payment_methods?: { method: string; amount: Num }[];
};

const COLORS = ["#18563a", "#c9a43c", "#4f9f71", "#a9862c", "#7dbc96", "#14442f"];
const n = (value: Num) => Number(value);
const compactINR = (value: number) => new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 }).format(value);

function ChartCard({ title, empty, children }: { title: string; empty: boolean; children: React.ReactNode }) {
  return (
    <Card className="p-4">
      <h3 className="mb-3 text-sm font-semibold text-slate-800">{title}</h3>
      {empty ? <EmptyState title="No data in this period" /> : <div className="h-64">{children}</div>}
    </Card>
  );
}

export function DashboardCharts({ data }: { data: DashboardChartData }) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <ChartCard title="Sales trend" empty={!data.sales_trend?.length}>
        <ResponsiveContainer>
          <LineChart data={(data.sales_trend ?? []).map((point) => ({ date: point.date, amount: n(point.amount) }))}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e8e1cc" />
            <XAxis dataKey="date" tickFormatter={(value: string) => formatDate(value).slice(0, 6)} fontSize={12} />
            <YAxis tickFormatter={compactINR} fontSize={12} />
            <Tooltip formatter={(value: number) => formatINR(value)} labelFormatter={(value: string) => formatDate(value)} />
            <Line type="monotone" dataKey="amount" name="Sales" stroke="#18563a" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>
      <ChartCard title="Purchase trend" empty={!data.purchase_trend?.length}>
        <ResponsiveContainer>
          <LineChart data={(data.purchase_trend ?? []).map((point) => ({ date: point.date, amount: n(point.amount) }))}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e8e1cc" />
            <XAxis dataKey="date" tickFormatter={(value: string) => formatDate(value).slice(0, 6)} fontSize={12} />
            <YAxis tickFormatter={compactINR} fontSize={12} />
            <Tooltip formatter={(value: number) => formatINR(value)} labelFormatter={(value: string) => formatDate(value)} />
            <Line type="monotone" dataKey="amount" name="Purchases" stroke="#c9a43c" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>
      <ChartCard title="Revenue vs expenses" empty={!data.revenue_vs_expenses?.length}>
        <ResponsiveContainer>
          <BarChart data={(data.revenue_vs_expenses ?? []).map((point) => ({ period: point.period, revenue: n(point.revenue), expenses: n(point.expenses) }))}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e8e1cc" />
            <XAxis dataKey="period" fontSize={12} />
            <YAxis tickFormatter={compactINR} fontSize={12} />
            <Tooltip formatter={(value: number) => formatINR(value)} />
            <Legend />
            <Bar dataKey="revenue" name="Revenue" fill="#18563a" />
            <Bar dataKey="expenses" name="Expenses" fill="#c9a43c" />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>
      <ChartCard title="Payment methods" empty={!data.payment_methods?.length}>
        <ResponsiveContainer>
          <PieChart>
            <Pie data={(data.payment_methods ?? []).map((point) => ({ name: point.method, value: n(point.amount) }))} dataKey="value" nameKey="name" outerRadius={90} label>
              {(data.payment_methods ?? []).map((_, index) => <Cell key={index} fill={COLORS[index % COLORS.length]} />)}
            </Pie>
            <Tooltip formatter={(value: number) => formatINR(value)} />
            <Legend />
          </PieChart>
        </ResponsiveContainer>
      </ChartCard>
    </div>
  );
}
