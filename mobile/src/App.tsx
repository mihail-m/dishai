import { useEffect, useId, useRef, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import {
  ArrowDown, ArrowRight, ArrowUpRight, AudioLines, Check, CheckCheck,
  ChevronDown, ChevronRight, CloudSun, Compass, Expand, Footprints, House, Info, Layers2,
  Leaf, LoaderCircle, MapPin, Maximize2, Minimize2, Navigation, PanelTop,
  Play, RefreshCw, Route, ShieldCheck, SlidersHorizontal, Sparkles, Sun, Sunrise,
  Timer, Waves, Wind, X,
} from 'lucide-react';
import { loadForecast, preloadRoutes, formatTime, windowEndTime } from './data';
import type { DataMode, ForecastData, ForecastHour, RouteData, RouteId } from './types';
import ForecastChart from './components/ForecastChart';
import RouteMap from './components/RouteMap';

type Tab = 'today' | 'routes' | 'insights';
type Sheet = 'settings' | 'air' | 'window' | 'break' | null;
type SavedPlan = { kind: 'window' | 'break'; time: string; end: string; duration?: number; mode: DataMode };
const native = Capacitor.isNativePlatform();
const safeRead = <T,>(key: string, fallback: T): T => { try { return JSON.parse(localStorage.getItem(key) ?? 'null') ?? fallback; } catch { return fallback; } };
const safeSave = (key: string, value: unknown) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Private browsing can disable persistence. */ } };
const qualityLabel = (hour?: ForecastHour) => hour?.quality === 'lower' ? 'Lower levels.' : hour?.quality === 'moderate' ? 'Typical levels.' : hour?.quality === 'elevated' ? 'Higher levels.' : 'Reading pending.';
const qualityDetail = (hour?: ForecastHour) => hour?.quality === 'unavailable' ? 'Reading unavailable' : 'Compared within this 24-hour outlook';
const pm = (value?: number | null) => value === null || value === undefined ? '—' : value.toFixed(1);
const calendarLabel = (time?: string) => time ? new Date(`${time.slice(0, 10)}T12:00:00Z`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }) : '';

function Orb({ hour, small = false }: { hour: number; small?: boolean }) {
  return <div className={`air-orb ${small ? 'orb-small' : ''}`} style={{ '--orb-turn': `${hour * 9}deg` } as React.CSSProperties} aria-hidden="true"><div className="orb-halo"/><div className="orb-core"><div className="orb-ribbon ribbon-one"/><div className="orb-ribbon ribbon-two"/><div className="orb-highlight"/></div><span className="orb-spark spark-one"/><span className="orb-spark spark-two"/></div>;
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => { const dialog = ref.current; dialog?.showModal(); return () => dialog?.close(); }, []);
  return <dialog ref={ref} className="glass-dialog" aria-labelledby={id} onCancel={onClose} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="sheet-grabber"/><div className="sheet-head"><h2 id={id}>{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={20}/></button></div>{children}
  </dialog>;
}

export default function App() {
  const [tab, setTab] = useState<Tab>('today');
  const [mode, setMode] = useState<DataMode>(() => safeRead<DataMode>('airpath-mobile-mode', 'demo') === 'live' ? 'live' : 'demo');
  const [snapshot, setSnapshot] = useState<ForecastData | null>(null);
  const forecast = snapshot?.mode === mode ? snapshot : null;
  const [hourIndex, setHourIndex] = useState(0);
  const hour = forecast?.hours[hourIndex];
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const previousRefresh = useRef(0);
  const [routes, setRoutes] = useState<{ id: string; reversed: boolean; data: RouteData[] } | null>(null);
  const [routeProgress, setRouteProgress] = useState(0);
  const [routeError, setRouteError] = useState<string | null>(null);
  const [routeRetry, setRouteRetry] = useState(0);
  const [selectedRoute, setSelectedRoute] = useState<RouteId>('clean');
  const [reversed, setReversed] = useState(false);
  const [overlay, setOverlay] = useState(true);
  const [routePreview, setRoutePreview] = useState(false);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [focus, setFocus] = useState(new URLSearchParams(location.search).has('focus'));
  const [toast, setToast] = useState('');
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const scroller = useRef<HTMLDivElement>(null);
  const [savedPlan, setSavedPlan] = useState<SavedPlan | null>(() => safeRead('airpath-mobile-plan', null));
  const [breakDuration, setBreakDuration] = useState(30);
  const [tour, setTour] = useState(false);
  const [tourStep, setTourStep] = useState(0);
  const currentRoutes = forecast && routes?.id === forecast.id && routes.reversed === reversed ? routes.data[hourIndex] : null;
  const routesReady = !!currentRoutes && routes?.data.length === 24;
  const chosen = currentRoutes?.routes[selectedRoute];
  const best = forecast?.bestWindow;
  const bestStart = best ? forecast?.hours[best.startIndex] : null;
  const bestEnd = best ? forecast?.hours[best.endIndex] : null;
  const windowText = bestStart && bestEnd ? `${bestStart.label}–${formatTime(windowEndTime(bestEnd.time))}` : 'Forecast pending';
  const nextDay = hour && forecast && hour.time.slice(0, 10) !== forecast.hours[0].time.slice(0, 10);
  const selectedTime = `${nextDay ? 'Tomorrow · ' : ''}${hour?.label ?? ''}`;
  const activePlan = savedPlan?.mode === mode ? savedPlan : null;

  function notify(message: string) { setToast(message); clearTimeout(toastTimer.current); toastTimer.current = setTimeout(() => setToast(''), 3200); }
  useEffect(() => () => clearTimeout(toastTimer.current), []);
  useEffect(() => {
    const controller = new AbortController();
    const force = previousRefresh.current !== refresh;
    previousRefresh.current = refresh;
    setLoading(true); setError(null);
    loadForecast({ mode, force, signal: controller.signal }).then(next => {
      if (controller.signal.aborted) return;
      setHourIndex(index => {
        const priorTime = snapshot?.mode === next.mode ? snapshot.hours[index]?.time : null;
        return Math.max(0, next.hours.findIndex(item => item.time === priorTime));
      });
      setSnapshot(next);
      if (force) notify(mode === 'demo' ? 'Demo forecast refreshed. All 24 hours are ready.' : 'Forecast updated. All 24 hours are ready.');
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'The forecast is unavailable.'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
    // Refreshes preserve the selected timestamp using the last committed snapshot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, refresh]);

  useEffect(() => {
    if (!forecast || (mode === 'live' && tab !== 'routes')) return;
    if (routes?.id === forecast.id && routes.reversed === reversed && routes.data.length === 24) { setRouteError(null); return; }
    const controller = new AbortController();
    setRouteError(null); setRouteProgress(0);
    preloadRoutes({ forecast, reversed, signal: controller.signal, onProgress: (loaded: number) => setRouteProgress(loaded) })
      .then(data => { if (!controller.signal.aborted) setRoutes({ id: forecast.id, reversed, data }); })
      .catch(reason => { if (!controller.signal.aborted) setRouteError(reason instanceof Error ? reason.message : 'Route comparison is unavailable.'); });
    return () => controller.abort();
  }, [forecast, mode, tab, reversed, routeRetry]);

  function go(next: Tab) { setTab(next); setRoutePreview(false); scroller.current?.scrollTo({ top: 0, behavior: 'instant' }); }
  function changeMode(next: DataMode) { setMode(next); safeSave('airpath-mobile-mode', next); setHourIndex(0); setSheet(null); setTour(false); }
  function savePlan(kind: SavedPlan['kind']) {
    if (!bestStart || !bestEnd) return;
    const next: SavedPlan = { kind, time: bestStart.time, end: windowEndTime(bestEnd.time), mode, ...(kind === 'break' ? { duration: breakDuration } : {}) };
    setSavedPlan(next); safeSave('airpath-mobile-plan', next); setSheet(null); notify('Saved to your day.');
  }
  function startTour() { changeMode('demo'); setTour(true); setTourStep(0); setHourIndex(0); go('today'); }
  function advanceTour() {
    const next = tourStep + 1;
    if (next === 1) { setHourIndex(best?.startIndex ?? 7); go('today'); }
    else if (next === 2) { go('routes'); setSelectedRoute('fast'); }
    else if (next === 3) { setSelectedRoute('clean'); }
    else if (next === 4) { go('insights'); }
    else { setTour(false); notify('Demo complete. Explore at your own pace.'); return; }
    setTourStep(next);
  }
  const tourTitles = ['Your day, at a glance', 'Find a clearer part of the day', 'Compare the fastest route', 'See the lower-exposure option', 'Turn the forecast into a plan'];

  return <div className={`presentation ${native ? 'is-native' : ''} ${focus ? 'is-focused' : ''}`}>
    {!native && <>
      <div className="presentation-grain" aria-hidden="true"/>
      <header className="desktop-brand"><span className="brand-mark"><Waves size={22}/></span><span>airpath<span className="brand-dot">.</span></span><span className="desktop-edition">A LITTLE MORE FRESH AIR</span></header>
      <aside className="presentation-intro"><span className="launch-tag"><span/> YOUR EVERYDAY AIR COMPANION</span><h1>A clearer outlook.<br/><span>A better everyday.</span></h1><p>Discover the right moment.<br/>Take a more thoughtful route.<br/>Make a little room for fresh air.</p><div className="presentation-actions"><button className="desktop-primary" onClick={startTour}><Play size={16} fill="currentColor"/> Play the demo</button><button className="desktop-secondary" onClick={() => setFocus(true)}><Maximize2 size={16}/> Focus view</button></div><div className="presentation-footnote"><span><CheckCheck size={14}/> 24-hour outlook</span><span><Compass size={14}/> Routes with perspective</span></div></aside>
      <button className="focus-control" onClick={() => setFocus(value => !value)} aria-label={focus ? 'Exit focus view' : 'Enter focus view'}>{focus ? <Minimize2 size={18}/> : <Expand size={18}/>}</button>
    </>}
    <div className={`device ${tab === 'routes' ? 'device-map' : ''}`}>
      <div className="device-status" aria-hidden="true"><span>9:41</span><span className="device-camera"/><span className="device-signal"><i/><i/><i/><i/><span className="device-battery"/></span></div>
      <header className={`app-header ${tab === 'routes' ? 'header-on-map' : ''}`}>
        <button className="location-control" onClick={() => setSheet('settings')} aria-label="Sofia, data settings"><span className="location-symbol"><MapPin size={16}/></span><span>Sofia <ChevronDown size={12}/><small>{mode === 'demo' ? 'A day in the city' : 'Your city, connected'}</small></span></button>
        <div className="header-actions"><button className={`data-chip ${mode}`} onClick={() => setSheet('settings')}><span/>{mode === 'demo' ? 'Demo' : 'Connected'}</button><button className="profile-button" aria-label="App settings" onClick={() => setSheet('settings')}><SlidersHorizontal size={17}/></button></div>
      </header>
      <div className={`app-scroll ${tab === 'routes' ? 'map-scroll' : ''}`} ref={scroller}>
        {error && <div className="error-banner" role="alert"><Info size={18}/><div><strong>{forecast ? 'Couldn’t refresh the forecast' : 'Let’s reconnect'}</strong><p>{error}</p><button onClick={() => setRefresh(n => n + 1)}>Try again</button>{mode === 'live' && <button onClick={() => changeMode('demo')}>Open demo</button>}</div></div>}
        {!forecast && !error && <div className="loading-state"><Orb hour={0}/><h2>A fresh perspective.</h2><p><LoaderCircle size={16} className="spin"/> Preparing your 24-hour outlook</p></div>}
        {forecast && tab === 'today' && <main className="today-page page-enter">
          <div className="welcome"><div><p className="overline">{calendarLabel(hour?.time).toUpperCase()} · YOUR OUTLOOK</p><h1>Your day,<br/><span>a little clearer.</span></h1></div><div className="weather-pill"><CloudSun size={25}/><span>{hour?.temperature === null ? '—' : Math.round(hour?.temperature ?? 0)}°<small>Sofia</small></span></div></div>
          <section className={`air-card glass quality-${hour?.quality}`} aria-label="Air quality summary">
            <div className="air-card-top"><span><span className="status-dot"/>Outlook at {selectedTime}</span><button className="air-info" aria-label="Understand air quality" onClick={() => setSheet('air')}><ArrowUpRight size={18}/></button></div>
            <Orb hour={hourIndex}/><div className="air-reading"><h2>{qualityLabel(hour)}</h2><p>{qualityDetail(hour)}</p><div className="particle-reading"><strong>{pm(hour?.pm25)}</strong><span>µg/m³<small>PM2.5</small></span></div></div>
            <div className="air-card-bottom"><span><Wind size={14}/>{hour?.wind === null ? 'Wind unavailable' : `${Math.round(hour?.wind ?? 0)} km/h breeze`}</span><span>{mode === 'demo' ? 'Illustrative reading' : 'Forecast estimate'}</span></div>
          </section>
          <section className="forecast-card glass" aria-label="24-hour forecast">
            <div className="section-heading"><h2>The next 24 hours</h2><button className={`icon-button refresh-button ${loading ? 'is-refreshing' : ''}`} disabled={loading} onClick={() => setRefresh(n => n + 1)} aria-label="Refresh 24-hour forecast"><RefreshCw size={16}/></button></div>
            <div className="forecast-meta"><span className="selected-time">{selectedTime}<span> · {pm(hour?.pm25)} µg/m³</span></span><span className="ready-label"><CheckCheck size={12}/> All hours ready</span></div>
            <ForecastChart hours={forecast.hours} selected={hourIndex} onSelect={setHourIndex}/>
          </section>
          <button className="window-teaser glass" onClick={() => setSheet('window')} disabled={!best}>
            <span className="window-icon"><PanelTop size={23}/></span><span><small>A LITTLE FRESH AIR AT HOME</small><strong>{windowText}</strong><span>Suggested ventilation window</span></span><ArrowUpRight size={20}/>
          </button>
          <button className="route-teaser" onClick={() => go('routes')}><span className="route-teaser-icon"><Route size={23}/></span><span><strong>A better way through the city</strong><small>Compare your walking routes</small></span><ArrowRight size={20}/></button>
          <p className={`data-footnote ${mode === 'live' && forecast.source === 'fixture' ? 'source-warning' : ''}`}>{mode === 'demo' ? 'Demo scenario · illustrative forecast & estimates' : forecast.source === 'fixture' ? 'Saved offline data · not a current live forecast' : `${forecast.source} · updated ${calendarLabel(forecast.generatedAt)} ${formatTime(forecast.generatedAt)}`}</p>
        </main>}
        {forecast && tab === 'routes' && <main className={`routes-page page-enter ${routePreview ? 'route-is-previewing' : ''}`}>
          <div className="map-stage"><RouteMap selectedRoute={selectedRoute} routes={currentRoutes} selectedHour={hourIndex} pollutionVisible={overlay} mode={mode} sensors={forecast.sensors} compact={!routePreview} onSelectRoute={setSelectedRoute}/>
            <div className="map-title"><span className="overline">A LITTLE LESS EXPOSURE</span><h1>Enjoy the journey.</h1></div>
            <div className="map-tools"><button className={overlay ? 'active' : ''} onClick={() => setOverlay(value => !value)} aria-label="Show air quality overlay" aria-pressed={overlay}><Layers2 size={19}/></button><button onClick={() => setRoutePreview(value => !value)} aria-label={routePreview ? 'Expand route details' : 'Expand map'}><Expand size={19}/></button></div>
            <div className="map-key"><span className="key-dot"/>{overlay ? mode === 'demo' ? 'Illustrative air layer' : forecast.source === 'fixture' ? 'Saved sensor readings' : 'Latest sensor readings' : 'Walking routes'}</div>
          </div>
          <section className="route-sheet glass">
            <button className="route-sheet-handle" aria-label={routePreview ? 'Expand route details' : 'Collapse route details'} onClick={() => setRoutePreview(value => !value)}><span/></button>
            <div className="trip-line"><span className="trip-markers"><span/><i/><MapPin size={15}/></span><div><span>{reversed ? 'City Garden' : 'Prince’s Garden'}</span><strong>{reversed ? 'Prince’s Garden' : 'City Garden'}</strong></div><button className="icon-button" onClick={() => { setReversed(value => !value); setRoutePreview(false); }} aria-label="Reverse walking route"><ArrowDown size={18}/><ArrowUpRight size={14}/></button></div>
            <div className="route-time-row"><span><Footprints size={14}/> Walking</span><span>{selectedTime} departure</span></div>
            {!routePreview && <>
              {routeError ? <div className="route-error" role="alert"><p>{routeError}</p><button onClick={() => setRouteRetry(n => n + 1)}>Retry routes</button></div> : !routesReady ? <div className="route-loading" role="status"><LoaderCircle size={18} className="spin"/><span>Preparing your routes<small>{routeProgress}/24 hours ready</small></span></div> : <>
                <div className="route-options">{(['fast', 'clean'] as RouteId[]).map(id => { const option = currentRoutes!.routes[id]; return <button key={id} className={`route-option ${selectedRoute === id ? 'is-selected' : ''}`} onClick={() => setSelectedRoute(id)} aria-pressed={selectedRoute === id}><span className="route-option-name">{id === 'clean' ? <Leaf size={14}/> : <Timer size={14}/>} {id === 'clean' ? option.title : 'Fastest'}{selectedRoute === id && <Check size={13}/>}</span><strong>{Math.round(option.minutes)}<span>min</span></strong><span className="route-option-distance">{option.distanceKm.toFixed(1)} km · walk</span></button>; })}</div>
                <div className="exposure-insight"><span className="insight-symbol"><Waves size={19}/></span><p>{selectedRoute === 'clean' && chosen && chosen.doseChangePct < 0 ? <><strong>{currentRoutes?.confidence?.cleanerExists === false ? 'A similar exposure estimate' : `${Math.round(Math.abs(chosen.doseChangePct))}% less estimated exposure`}</strong><span>{mode === 'demo' ? 'Illustrative comparison for this demo' : currentRoutes?.confidence?.cleanerExists === false ? 'Difference is within model uncertainty' : 'Compared with the fastest route'}</span></> : selectedRoute === 'clean' ? <><strong>No lower exposure predicted</strong><span>Compare the time and distance of each option.</span></> : <><strong>The shortest time to your destination</strong><span>Compare the alternative to explore the trade-off</span></>}</p></div>
              </>}
              <div className="route-slider"><label htmlFor="route-departure">Departure <strong>{hour?.label}</strong></label><input id="route-departure" aria-label="Route departure hour" type="range" min="0" max="23" value={hourIndex} disabled={!routesReady} onChange={e => setHourIndex(Number(e.target.value))}/></div>
              <button className="primary-button" disabled={!routesReady} onClick={() => setRoutePreview(true)}><Navigation size={17}/> Preview this route<ArrowRight size={17}/></button>
            </>}
            {routePreview && <button className="map-preview-return" onClick={() => setRoutePreview(false)}>View route comparison <ChevronDown size={16}/></button>}
          </section>
        </main>}
        {forecast && tab === 'insights' && <main className="insights-page page-enter">
          <div className="welcome"><div><p className="overline">MAKE IT PART OF YOUR DAY</p><h1>Small shifts.<br/><span>Fresh perspective.</span></h1></div><span className="insights-sun"><Sunrise size={34}/></span></div>
          <section className="window-feature glass"><div className="window-feature-art" aria-hidden="true"><div className="sun-disc"/><div className="window-frame"><span/><span/></div><div className="window-shine"/></div><span className="feature-tag"><House size={12}/> AT HOME</span><h2>Let a little<br/>fresh air in.</h2><p>Your suggested ventilation window</p><div className="window-time">{windowText}</div><button className="light-button" disabled={!best} onClick={() => setSheet('window')}>Explore this window <ArrowUpRight size={17}/></button></section>
          <div className="section-heading day-heading"><h2>Made for your everyday</h2><Sparkles size={17}/></div>
          <button className="activity-card glass" onClick={() => setSheet('break')} disabled={!best}><span className="activity-visual"><Footprints size={28}/><span/></span><span><small>OUT & ABOUT</small><strong>Make time for a walk.</strong><span>Find a lower-pollution moment.</span></span><ChevronRight size={18}/></button>
          <button className="activity-card glass" onClick={() => go('routes')}><span className="activity-visual lavender"><Compass size={28}/><span/></span><span><small>ON YOUR WAY</small><strong>See another side of your city.</strong><span>A little more thought in every route.</span></span><ChevronRight size={18}/></button>
          {activePlan && <section className="saved-plan glass"><div><span className="saved-check"><Check size={15}/></span><span><small>SAVED TO YOUR DAY</small><strong>{activePlan.kind === 'window' ? 'Open-window moment' : `${activePlan.duration}-minute outdoor break`}</strong><span>{calendarLabel(activePlan.time)} · {formatTime(activePlan.time)}{activePlan.kind === 'window' ? `–${formatTime(activePlan.end)}` : ''}</span></span></div><button className="icon-button" aria-label="Remove saved plan" onClick={() => { setSavedPlan(null); safeSave('airpath-mobile-plan', null); }}><X size={16}/></button></section>}
          <div className="insight-transparency"><ShieldCheck size={18}/><p>A forecast helps you plan.<br/><span>Local and indoor conditions still matter.</span></p></div>
        </main>}
      </div>
      <nav className="glass-tabbar" aria-label="Mobile navigation">{([{ id: 'today', title: 'Today', icon: Sun }, { id: 'routes', title: 'Routes', icon: Compass }, { id: 'insights', title: 'My day', icon: Sparkles }] as const).map(({ id, title, icon: Icon }) => <button key={id} aria-current={tab === id ? 'page' : undefined} onClick={() => go(id)}><span><Icon size={21}/>{tab === id && <i/>}</span><span>{title}</span></button>)}</nav>
      {!native && <div className="home-indicator" aria-hidden="true"/>}
      {toast && <div className="toast" role="status"><Check size={16}/>{toast}</div>}
    </div>
    {!native && <aside className="presentation-side"><span className="side-line"/><span>DESIGNED AROUND<br/>YOUR EVERYDAY.</span><div className="palette-dots"><i/><i/><i/><i/></div></aside>}
    {tour && <div className="demo-director"><div><small>DEMO WALKTHROUGH · {tourStep + 1}/5</small><strong>{tourTitles[tourStep]}</strong></div><button className="tour-next" onClick={advanceTour}>{tourStep === 4 ? 'Finish' : 'Next'}<ArrowRight size={16}/></button><button className="tour-close" onClick={() => setTour(false)} aria-label="Close walkthrough"><X size={16}/></button></div>}
    {sheet === 'settings' && <Modal title="Your AirPath" onClose={() => setSheet(null)}><p className="sheet-intro">A clearer outlook, wherever your day takes you.</p><p className="field-label">DATA SOURCE</p><div className="mode-options"><button aria-pressed={mode === 'demo'} onClick={() => changeMode('demo')}><Sparkles size={23}/><span><strong>Explore the demo</strong><small>A complete day in Sofia. Ready offline.</small></span>{mode === 'demo' && <Check size={18}/>}</button><button aria-pressed={mode === 'live'} onClick={() => changeMode('live')}><AudioLines size={23}/><span><strong>Connect to AirPath</strong><small>Use forecasts from your running service.</small></span>{mode === 'live' && <Check size={18}/>}</button></div><div className="settings-note"><Info size={17}/><p>Demo readings and exposure comparisons are illustrative. Connected mode uses the project’s existing forecasting and routing services.</p></div><button className="primary-button" onClick={() => { setSheet(null); startTour(); }}><Play size={16}/> Take the demo tour</button></Modal>}
    {sheet === 'air' && <Modal title="A little more clarity" onClose={() => setSheet(null)}><div className="reading-detail"><Orb hour={hourIndex} small/><span>Fine particles · PM2.5<strong>{pm(hour?.pm25)}<small>µg/m³</small></strong><span>{hour?.label} · {qualityDetail(hour)}</span></span></div><h3>What am I looking at?</h3><p className="sheet-copy">PM2.5 describes tiny particles in the air. This reading is the estimated concentration outside. A lower number means fewer particles.</p><h3>Put the trend first.</h3><p className="sheet-copy">The 24-hour outlook helps you compare times of day. The labels are simple app indicators, not an official air-quality index or a personal health assessment.</p><div className="source-note"><ShieldCheck size={16}/><span>{mode === 'demo' ? 'Illustrative demo data' : forecast?.source}</span></div><button className="primary-button" onClick={() => setSheet(null)}>Back to my outlook <ArrowRight size={17}/></button></Modal>}
    {sheet === 'window' && <Modal title="A fresher space" onClose={() => setSheet(null)}><div className="window-sheet-hero"><span><PanelTop size={26}/></span><small>SUGGESTED VENTILATION WINDOW</small><strong>{windowText}</strong></div><h3>Why this moment?</h3><p className="sheet-copy">This window has one of the lowest average PM2.5 levels in your next 24 hours. It’s a useful starting point when deciding when to air your space.</p><div className="window-metrics"><span>Forecast average<strong>{best ? pm(best.meanPm25) : '—'}<small>µg/m³ PM2.5</small></strong></span><span>Based on<strong>24<small>hourly forecasts</small></strong></span></div><p className="small-note">Outdoor forecasts don’t measure your indoor air. Consider nearby traffic, weather, and your own circumstances.</p><button className="primary-button" disabled={!best} onClick={() => savePlan('window')}><Check size={17}/> Save to my day</button><button className="text-button" onClick={() => { setSheet(null); go('today'); if (best) setHourIndex(best.startIndex); }}>See it in the forecast <ArrowUpRight size={15}/></button></Modal>}
    {sheet === 'break' && <Modal title="A moment outside" onClose={() => setSheet(null)}><div className="break-illustration"><Footprints size={38}/><Sun size={28}/></div><p className="sheet-intro">Make room for an outdoor break during a lower-pollution part of the day.</p><p className="field-label">HOW MUCH TIME DO YOU HAVE?</p><div className="duration-options">{[15, 30, 60].map(duration => <button key={duration} onClick={() => setBreakDuration(duration)} aria-pressed={breakDuration === duration}>{duration}<span>minutes</span></button>)}</div><div className="break-time"><span>Suggested start</span><strong>{bestStart?.label ?? '—'}</strong></div><button className="primary-button" disabled={!best} onClick={() => savePlan('break')}><Check size={17}/> Add to my day</button></Modal>}
  </div>;
}
