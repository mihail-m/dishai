import { useId } from 'react';
import { Moon, Sun } from 'lucide-react';
import type { ForecastHour } from '../types';

export default function ForecastChart({ hours, selected, onSelect, disabled = false }: {
  hours: ForecastHour[]; selected: number; onSelect: (hour: number) => void; disabled?: boolean;
}) {
  const id = useId().replace(/:/g, '');
  const values = hours.map(hour => hour.pm25);
  const finite = values.filter((v): v is number => v !== null && Number.isFinite(v));
  const max = Math.max(20, ...finite) * 1.18;
  const x = (i: number) => 10 + i / Math.max(1, hours.length - 1) * 320;
  const y = (v: number) => 83 - v / max * 64;
  const segments: string[] = [];
  let segment = '';
  values.forEach((value, i) => {
    if (value === null) { if (segment) segments.push(segment); segment = ''; }
    else { segment += `${segment ? ' L' : 'M'} ${x(i)} ${y(value)}`; }
  });
  if (segment) segments.push(segment);
  const current = hours[selected];
  return <div className={`forecast-chart ${disabled ? 'is-disabled' : ''}`}>
    <svg viewBox="0 0 340 104" role="img" aria-label="PM2.5 forecast over the next 24 hours, in micrograms per cubic metre">
      <defs>
        <linearGradient id={`line-${id}`} x1="0" x2="1"><stop stopColor="#3184e7"/><stop offset=".46" stopColor="#54b9ae"/><stop offset="1" stopColor="#b597e5"/></linearGradient>
        <linearGradient id={`fill-${id}`} x1="0" y1="0" x2="0" y2="1"><stop stopColor="#7eb7e7" stopOpacity=".3"/><stop offset="1" stopColor="#9dd9f4" stopOpacity="0"/></linearGradient>
      </defs>
      {[25, 53, 82].map(level => <line key={level} x1="10" x2="330" y1={level} y2={level} stroke="#d7e1ea" strokeWidth=".6" strokeDasharray="2 5"/>)}
      {segments.map((path, i) => <path key={i} d={path} fill="none" stroke={`url(#line-${id})`} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>)}
      {current?.pm25 !== null && current?.pm25 !== undefined && <>
        <line x1={x(selected)} x2={x(selected)} y1="8" y2="88" stroke="#517aab" strokeOpacity=".35" strokeDasharray="3 3"/>
        <circle cx={x(selected)} cy={y(current.pm25)} r="8" fill="#fff"/>
        <circle cx={x(selected)} cy={y(current.pm25)} r="4.5" fill="#407dcc"/>
      </>}
      {hours.map((hour, i) => <circle key={hour.time} cx={x(i)} cy="99" r={i === selected ? 2.5 : 1.5} fill={i === selected ? '#447acc' : '#aebdce'}/>)}
    </svg>
    <input type="range" aria-label="Forecast hour" min="0" max={hours.length - 1} step="1" value={selected} disabled={disabled}
      aria-valuetext={`${current?.label ?? ''}, ${current?.pm25 === null ? 'reading unavailable' : `${current?.pm25} micrograms per cubic metre`}`}
      onChange={e => onSelect(Number(e.target.value))}/>
    <div className="forecast-ticks"><span><Sun size={11}/> {hours[0]?.label}</span><span>{hours[8]?.label}</span><span><Moon size={11}/> {hours[16]?.label}</span><span>{hours[23]?.label}</span></div>
  </div>;
}
