import { useEffect, useRef, type CSSProperties, type KeyboardEvent } from 'react';
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react';
import type { ForecastHour, RouteData, RouteId } from '../types';
import '../departure-wheel.css';

const STEP_WIDTH = 48;

type Props = {
  hours: ForecastHour[];
  selected: number;
  onSelect: (index: number) => void;
  disabled?: boolean;
  routeHours?: RouteData[];
  selectedRoute?: RouteId;
  onRefresh?: () => void;
  refreshing?: boolean;
};

/** Dates already belong to the forecast's local timezone; do not reinterpret them as device time. */
function dayLabel(hour: ForecastHour | undefined, first: ForecastHour | undefined) {
  if (!hour || !first) return '';
  if (hour.time.slice(0, 10) !== first.time.slice(0, 10)) return 'Tomorrow';
  return Number(hour.time.slice(11, 13)) >= 18 ? 'Tonight' : 'Today';
}

export default function DepartureWheel({
  hours, selected, onSelect, disabled = false, routeHours, selectedRoute = 'clean', onRefresh, refreshing = false,
}: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const selectedRef = useRef(selected);
  const scrollSelection = useRef<number | null>(null);
  const last = Math.max(0, hours.length - 1);
  const current = hours[selected];
  // Use a route outlook only when the entire current forecast is available. Partial/stale
  // route batches must never mix route estimates with area readings in one comparison.
  const routeReady = hours.length === 24 && routeHours?.length === 24 && routeHours.every((item, index) =>
    item.time === hours[index].time && Number.isFinite(item.routes[selectedRoute]?.meanPm25));
  const values = hours.map((item, index) => routeReady ? routeHours![index].routes[selectedRoute].meanPm25 : item.pm25);
  const finite = values.filter((value): value is number => value !== null && Number.isFinite(value));
  const min = finite.length ? Math.min(...finite) : 0;
  const max = finite.length ? Math.max(...finite) : 0;
  const lowest = finite.length ? values.findIndex(value => value === min) : -1;
  const value = values[selected];
  const unavailable = value === null || value === undefined || !Number.isFinite(value);
  const day = dayLabel(current, hours[0]);
  const rangeText = `${day}, ${current?.label ?? 'forecast pending'}, ${unavailable ? 'reading unavailable' : `${value.toFixed(1)} micrograms per cubic metre, ${routeReady ? 'route' : 'area'} estimate`}`;

  useEffect(() => {
    selectedRef.current = selected;
    if (scrollSelection.current === selected) {
      scrollSelection.current = null;
      return;
    }
    scrollSelection.current = null;
    scroller.current?.scrollTo({ left: selected * STEP_WIDTH, behavior: 'instant' });
  }, [selected]);

  function choose(index: number) {
    if (disabled || !hours.length) return;
    const next = Math.max(0, Math.min(last, index));
    scrollSelection.current = null;
    selectedRef.current = next;
    scroller.current?.scrollTo({ left: next * STEP_WIDTH, behavior: 'instant' });
    onSelect(next);
  }

  function handleScroll() {
    if (disabled || !scroller.current) return;
    const next = Math.max(0, Math.min(last, Math.round(scroller.current.scrollLeft / STEP_WIDTH)));
    if (next === selectedRef.current) return;
    scrollSelection.current = next;
    selectedRef.current = next;
    onSelect(next);
  }

  function handleKeys(event: KeyboardEvent<HTMLDivElement>) {
    // The native range owns its arrow-key behavior.
    if (event.target instanceof HTMLInputElement) return;
    const next = event.key === 'ArrowRight' ? selected + 1 : event.key === 'ArrowLeft' ? selected - 1 : event.key === 'Home' ? 0 : event.key === 'End' ? last : null;
    if (next === null) return;
    event.preventDefault();
    choose(next);
  }

  return <section className={`departure-wheel${disabled ? ' is-disabled' : ''}`} aria-label="Choose a departure time" onKeyDown={handleKeys}>
    <div className="departure-wheel-heading">
      <div><h3>Leave at <span>· Next 24 hours</span></h3><p>{unavailable ? 'Reading unavailable' : <><strong>{value.toFixed(1)}</strong> µg/m³ · {routeReady ? 'Route' : 'Area'} PM2.5 estimate</>}</p></div>
      <div className="departure-wheel-current"><span>{day}</span><strong>{current?.label ?? '—'}</strong></div>
      {onRefresh && <button type="button" className="departure-wheel-refresh" aria-label="Refresh 24-hour forecast" title="Refresh 24-hour forecast" disabled={refreshing} onClick={onRefresh}><RefreshCw size={14} className={refreshing ? 'is-refreshing' : undefined}/></button>}
    </div>
    <div className="departure-wheel-dial">
      <button type="button" className="departure-wheel-arrow" aria-label="Previous departure hour" disabled={disabled || selected <= 0} onClick={() => choose(selected - 1)}><ChevronLeft size={16}/></button>
      <div className="departure-wheel-window">
        <div className="departure-wheel-marker" aria-hidden="true"/>
        <div className="departure-wheel-scroll" ref={scroller} onScroll={handleScroll} aria-label="Hourly departure forecast" aria-disabled={disabled}>
          {hours.map((item, index) => {
            const reading = values[index];
            const missing = reading === null || !Number.isFinite(reading);
            const fraction = missing || max === min ? 0 : (reading - min) / (max - min);
            const color = missing ? '#a7b1c1' : fraction < .34 ? '#599c92' : fraction < .67 ? '#668dc7' : '#c48e68';
            const itemDay = dayLabel(item, hours[0]);
            const newDay = index > 0 && item.time.slice(0, 10) !== hours[index - 1].time.slice(0, 10);
            return <button type="button" key={item.time} className={`departure-wheel-hour${index === selected ? ' is-selected' : ''}`} disabled={disabled}
              aria-label={`${itemDay} ${item.label}${missing ? ', reading unavailable' : `, ${reading.toFixed(1)} micrograms per cubic metre`}`}
              aria-pressed={index === selected} onClick={() => choose(index)}
              style={{ '--hour-color': color, '--hour-height': `${7 + fraction * 13}px` } as CSSProperties}>
              <span className="departure-wheel-day">{newDay ? 'TOMORROW' : index === 0 ? 'START' : ''}</span>
              <span className="departure-wheel-bar" aria-hidden="true"/><span className="departure-wheel-time">{item.label}</span>
            </button>;
          })}
        </div>
      </div>
      <button type="button" className="departure-wheel-arrow" aria-label="Next departure hour" disabled={disabled || selected >= last} onClick={() => choose(selected + 1)}><ChevronRight size={16}/></button>
    </div>
    <input className="departure-wheel-range" type="range" aria-label="Route departure hour" min="0" max={last} step="1" value={selected} disabled={disabled || !hours.length} aria-valuetext={rangeText} onChange={event => choose(Number(event.target.value))}/>
    <div className="departure-wheel-foot"><span>{disabled ? 'Preparing all 24 hours…' : 'Swipe to compare departure times'}</span>{lowest >= 0 && <button type="button" disabled={disabled} onClick={() => choose(lowest)} aria-label={`Select lowest ${routeReady ? 'route' : 'area'} PM2.5, ${dayLabel(hours[lowest], hours[0])} ${hours[lowest].label}`}><i aria-hidden="true"/>Lowest · {hours[lowest].time.slice(0, 10) !== hours[0].time.slice(0, 10) ? 'tomorrow ' : ''}{hours[lowest].label}</button>}</div>
  </section>;
}
