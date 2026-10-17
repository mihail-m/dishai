const Dot = ({ c, ring = "#fff" }: { c: string; ring?: string }) => <i className="mr-1.5 inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: c, border: `1.5px solid ${ring}`, boxShadow: ring === "#fff" ? undefined : "none" }} />;
const Line = ({ c, dashed, w = 3 }: { c: string; dashed?: boolean; w?: number }) => (
  <i className="mr-1.5 inline-block w-5 shrink-0 align-middle" style={{ height: 0, borderTop: `${w}px ${dashed ? "dashed" : "solid"} ${c}` }} />
);

/** What every mark on the map means. Shown over the map on the three map tabs. */
export default function MapLegend({ tab, custom }: { tab: "map" | "route" | "network"; custom?: React.ReactNode }) {
  const rows: React.ReactNode[] =
    tab === "map"
      ? [
          <><Line c="#2a9d6f" /><Line c="#e0b030" /><Line c="#d6453d" /> Streets: Clean Air Index (scale on the right)</>,
          <><Dot c="#2f9e63" /><Dot c="#e0b030" /><Dot c="#c0392f" /> Citizen air sensors, coloured by PM2.5</>,
          <><i className="mr-1.5 inline-block h-[5px] w-5 shrink-0 rounded-full align-middle" style={{ background: "#fff", boxShadow: "0 0 0 1.5px #6b7280" }} /> Street you clicked (outlined)</>,
          <><i className="mr-1.5 inline-block h-3 w-3 shrink-0 rounded-full" style={{ background: "#fff", border: "3px solid #111827" }} /> Municipal reference station (name, PM10 µg/m³)</>,
        ]
      : tab === "route"
        ? [
            <><Dot c="#1f6f5c" /> A start <Dot c="#c0392f" /><span className="ml-1">B end</span></>,
            <><Line c="#4a5563" w={4} /> Fastest <Line c="#e0961e" w={4} /> Balanced <Line c="#1f8a70" w={4} /> Cleanest</>,
            <><Line c="#2563eb" w={4} /> Your slider setting <Line c="#5a5a5a" dashed w={4} /> Google</>,
            <><span className="faint">Faint streets underneath: Clean Air Index</span></>,
          ]
        : [
            <><Line c="#d6453d" w={2} /><Line c="#d6453d" w={7} /> Wider = more of the 400 sample walks use it on their cleanest route</>,
            <><Line c="#2a9d6f" /><Line c="#e0b030" /><Line c="#d6453d" /> Colour = Clean Air Index</>,
            <><i className="mr-1.5 grid h-4 w-4 shrink-0 place-items-center rounded-full text-[9px] font-bold text-white" style={{ background: "#d6288c" }}>1</i> Chokepoint: many fastest routes, low CAI</>,
            <><Dot c="#fff" ring="#6b7280" /><Dot c="#fff" ring="#6b7280" /> Moving white dots: animated illustration of the cleanest routes&apos; direction. Not live traffic or people.</>,
          ];
  return (
    <div className="card pointer-events-none absolute bottom-3 left-3 z-10 max-w-[min(360px,80%)] p-2.5 text-[11px] leading-snug shadow-sm" aria-label="Map legend">
      <div className="mb-1 font-semibold">How to read this map</div>
      {tab === "network" && custom ? custom : <ul className="grid gap-1">{rows.map((r, i) => <li key={i} className="flex flex-wrap items-center">{r}</li>)}</ul>}
    </div>
  );
}
