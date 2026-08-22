import type { MetricTile as MetricTileData } from "../lib/analyzers";

export function MetricTileRow({ tiles }: { tiles: MetricTileData[] }) {
  if (tiles.length === 0) return null;
  return (
    <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
      {tiles.map((tile) => (
        <div key={tile.label} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{tile.label}</p>
          <p className="mt-0.5 text-sm font-semibold text-slate-900">{tile.value}</p>
        </div>
      ))}
    </div>
  );
}
