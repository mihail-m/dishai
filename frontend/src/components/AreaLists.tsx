import type { AreasResp } from "../types";

/** Best and worst places of the current layer; tapping one flies the map there. */
export default function AreaLists({ ex, titles, colors, fmt, onFly }: {
  ex: AreasResp["extremes"]; titles: [string, string]; colors: [string, string]; fmt: (v: number) => string; onFly: (lon: number, lat: number) => void;
}) {
  return (
    <div className="grid gap-3">
      {([[titles[0], ex.best, colors[0]], [titles[1], ex.worst, colors[1]]] as const).map(([title, list, color]) => (
        <div key={title}>
          <h3 className="eyebrow mb-1">{title}</h3>
          <ol className="grid gap-0.5">
            {list.slice(0, 3).map((p, i) => (
              <li key={i}>
                <button className="row-btn" onClick={() => onFly(p.lon, p.lat)}>
                  <i className="h-7 w-2.5 shrink-0 rounded" style={{ background: color }} />
                  <span className="min-w-0 flex-1 truncate font-semibold">{p.place}</span>
                  <span className="display shrink-0 text-[17px] font-bold tabular-nums">{fmt(p.value)}</span>
                </button>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </div>
  );
}
