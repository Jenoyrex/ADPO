export function StatCard({
  label,
  value,
  accent = "default",
}: {
  label: string;
  value: string | number;
  accent?: "default" | "critical" | "high" | "medium" | "low" | "positive";
}) {
  const valueColor: Record<typeof accent, string> = {
    default: "text-slate-900",
    critical: "text-red-600",
    high: "text-orange-600",
    medium: "text-amber-600",
    low: "text-slate-500",
    positive: "text-emerald-600",
  };

  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-2xl font-bold ${valueColor[accent]}`}>{value}</p>
    </div>
  );
}
