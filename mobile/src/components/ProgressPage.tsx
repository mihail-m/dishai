import { useEffect, useId, useRef, useState, type CSSProperties } from 'react';
import { ArrowRight, Check, ChevronRight, Flame, Footprints, Info, Leaf, RotateCcw, Sparkles, Trophy, Wind, X } from 'lucide-react';
import { getProgressSummary } from '../progress-data';
import '../progress.css';

const STORAGE_KEY = 'airpath-mobile-progress-v1';
const SAMPLE_WEEK = '2026-10-12';

function readProgress() {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null');
    return !!value && typeof value === 'object' && 'version' in value && value.version === 1
      && 'week' in value && value.week === SAMPLE_WEEK && 'completedDemoWalk' in value && value.completedDemoWalk === true;
  } catch { return false; }
}

function saveProgress(completed: boolean) {
  try {
    if (completed) localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, week: SAMPLE_WEEK, completedDemoWalk: true }));
    else localStorage.removeItem(STORAGE_KEY);
    return true;
  } catch { return false; }
}

function dateLabel(date: string, long = false) {
  return new Date(`${date}T12:00:00Z`).toLocaleDateString('en-GB', { weekday: long ? 'long' : 'short', day: 'numeric', month: long ? 'long' : 'short', timeZone: 'UTC' });
}

function arc(index: number) {
  const polar = (angle: number) => [90 + 70 * Math.cos(angle * Math.PI / 180), 90 + 70 * Math.sin(angle * Math.PI / 180)];
  const start = polar(-86 + index * 60), end = polar(-34 + index * 60);
  return `M ${start[0]} ${start[1]} A 70 70 0 0 1 ${end[0]} ${end[1]}`;
}

export default function ProgressPage({ onPlanRoute }: { onPlanRoute: () => void }) {
  const [completedDemoWalk, setCompletedDemoWalk] = useState(readProgress);
  const [selectedDay, setSelectedDay] = useState(() => readProgress() ? 6 : 5);
  const [methods, setMethods] = useState(false);
  const [celebrating, setCelebrating] = useState(false);
  const [message, setMessage] = useState('');
  const [storageWarning, setStorageWarning] = useState(false);
  const summary = getProgressSummary(completedDemoWalk);
  const day = summary.days[selectedDay];
  const maxMinutes = Math.max(1, ...summary.days.map(item => item.minutes));
  const carbonReduction = summary.totals.baselineCo2Kg > 0
    ? Math.round(summary.totals.avoidedCo2Kg / summary.totals.baselineCo2Kg * 100) : 0;
  const levelTarget = summary.pointsIntoLevel + summary.pointsToNextLevel;
  const id = useId().replace(/:/g, '');
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    if (!methods) return;
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, [methods]);

  useEffect(() => {
    if (!celebrating) return;
    const timer = setTimeout(() => setCelebrating(false), 2600);
    return () => clearTimeout(timer);
  }, [celebrating]);

  function addDemoWalk() {
    if (completedDemoWalk) return;
    setCompletedDemoWalk(true); setSelectedDay(6); setCelebrating(true);
    const saved = saveProgress(true);
    setStorageWarning(!saved);
    setMessage(`Weekly six complete. You earned 60 points and unlocked a new milestone.${saved ? '' : ' Progress is available for this session; browser storage is unavailable.'}`);
  }

  function resetProgress() {
    setCompletedDemoWalk(false); setSelectedDay(5); setCelebrating(false);
    setStorageWarning(!saveProgress(false)); setMethods(false);
    setMessage('Demo progress reset to the sample starting point.');
  }

  return <main className="progress-page">
    <header className="progress-header">
      <div><p className="progress-eyebrow">DEMO ACTIVITY · SAMPLE WEEK</p><h1>Your choices<br/><span>are adding up.</span></h1><p className="progress-week">12–18 October 2026 · Sofia</p></div>
      <button className="progress-info" aria-label="How estimates are calculated" onClick={() => setMethods(true)}><Info size={19}/></button>
    </header>

    <section className={`progress-hero${celebrating ? ' progress-is-celebrating' : ''}`} aria-label="Weekly walking challenge">
      <div className="progress-hero-heading"><span><Footprints size={13}/> THE WEEKLY SIX</span><span>{summary.challenge.unlocked ? 'Milestone unlocked' : 'One walk to go'}<i/></span></div>
      <div className="progress-hero-layout">
        <div className="progress-side-stat progress-side-streak"><span className="progress-stat-icon"><Flame size={20}/></span><strong className="progress-streak">{summary.streakDays}</strong><span>day streak</span></div>
        <div className="progress-ring">
          <div className="progress-ring-glow"/>
          <svg viewBox="0 0 180 180" aria-hidden="true"><defs>
            <linearGradient id={`progress-ring-${id}`} x1="0" y1="0" x2="1" y2="1"><stop stopColor="#8ccbbd"/><stop offset=".5" stopColor="#9ac5e4"/><stop offset="1" stopColor="#b5a7e0"/></linearGradient>
            <linearGradient id={`progress-centre-${id}`} x1="0" y1="0" x2=".8" y2="1"><stop stopColor="#ffffff" stopOpacity=".94"/><stop offset="1" stopColor="#eef6fd" stopOpacity=".55"/></linearGradient>
          </defs><circle cx="90" cy="90" r="82" fill="none" stroke="#ffffff" strokeOpacity=".8"/><circle cx="90" cy="90" r="57" fill={`url(#progress-centre-${id})`} stroke="#ffffff" strokeWidth="1.5"/>
            {Array.from({ length: 6 }, (_, index) => <path key={index} d={arc(index)} fill="none" stroke={index < summary.challenge.completed ? `url(#progress-ring-${id})` : '#d9e4ee'} strokeWidth="11" strokeLinecap="round" opacity={index < summary.challenge.completed ? 1 : .6}/>)}
          </svg>
          <div className="progress-ring-reading"><span className="progress-ring-leaf"><Leaf size={18}/></span><div><strong className="progress-challenge-count">{summary.challenge.completed}</strong><span>/ 6</span></div><small>walks this week</small></div>
          <span className="progress-orbit-spark progress-spark-one"/><span className="progress-orbit-spark progress-spark-two"/>
        </div>
        <div className="progress-side-stat progress-side-points"><span className="progress-stat-icon"><Sparkles size={20}/></span><strong className="progress-points">{summary.points}</strong><span>points earned</span></div>
      </div>
      <div className="progress-level"><div><span>Level {summary.level} <i>·</i> Everyday explorer</span><span>{summary.pointsIntoLevel} / {levelTarget} XP</span></div><div className="progress-level-track" role="progressbar" aria-label="Level progress" aria-valuemin={0} aria-valuemax={levelTarget} aria-valuenow={summary.pointsIntoLevel}><span style={{ width: `${levelTarget ? summary.pointsIntoLevel / levelTarget * 100 : 0}%` }}/></div></div>
      <button className={`progress-add-walk${completedDemoWalk ? ' is-complete' : ''}`} onClick={addDemoWalk} disabled={completedDemoWalk}>{completedDemoWalk ? <Check size={16}/> : <Footprints size={16}/>}<span>{completedDemoWalk ? 'Demo walk added' : 'Add demo walk'}</span><span>{completedDemoWalk ? '6 / 6' : '+60 XP'}</span></button>
      {celebrating && <div className="progress-celebration" aria-hidden="true">{Array.from({ length: 9 }, (_, index) => <i key={index} style={{ '--confetti-x': `${10 + index * 10}%`, '--confetti-delay': `${index * 35}ms`, '--confetti-color': ['#99cbb9', '#b8a9df', '#eabf9f'][index % 3] } as CSSProperties}/>)}</div>}
    </section>

    <div className="progress-metrics" aria-label="Estimated weekly impact">
      <button className="progress-metric progress-metric--air" onClick={() => setMethods(true)} aria-label="How estimated air exposure is calculated"><span className="progress-metric-title"><Wind size={16}/>Air exposure<Info size={12}/></span><strong>−{summary.totals.exposureReductionPct.toFixed(1)}<small>%</small></strong><span>Estimated reduction<br/>vs. your usual routes</span></button>
      <button className="progress-metric progress-metric--carbon" onClick={() => setMethods(true)} aria-label="How estimated carbon savings are calculated"><span className="progress-metric-title"><Leaf size={16}/>Travel CO₂<Info size={12}/></span><strong>{summary.totals.avoidedCo2Kg.toFixed(1)}<small>kg saved</small></strong><span>{carbonReduction}% below your sample<br/>commute baseline</span></button>
    </div>
    <p className="progress-estimate-note">Illustrative activity. Estimates, not personal measurements.</p>

    <section className="progress-week-card" aria-label="Your activity this week">
      <div className="progress-section-heading"><div><h2>A week in motion</h2><p>Cleaner walks, one day at a time.</p></div><span>{summary.totals.walkMinutes}<small>min walked</small></span></div>
      <div className="progress-days" aria-label="Choose a day">
        {summary.days.map((item, index) => <button className={`progress-day${selectedDay === index ? ' is-selected' : ''}${item.completed ? ' is-completed' : ''}`} key={item.date} aria-label={`${dateLabel(item.date, true)}, ${item.minutes} minutes walked`} aria-pressed={selectedDay === index} onClick={() => setSelectedDay(index)}><span className="progress-day-amount">{item.minutes > 0 ? item.minutes : '—'}</span><span className="progress-day-track"><i style={{ height: `${item.minutes > 0 ? 12 + item.minutes / maxMinutes * 88 : 6}%` }}/></span><span className="progress-day-label">{item.label.slice(0, 1)}</span><i className="progress-day-dot"/></button>)}
      </div>
      <div className="progress-day-detail" aria-live="polite"><span className="progress-day-detail-icon"><Footprints size={18}/></span><div><strong>{dateLabel(day.date, true)}</strong><span>{day.walks ? `${day.minutes} min · ${day.distanceKm.toFixed(1)} km · ${day.walks === 1 ? 'Cleaner walk' : `${day.walks} cleaner walks`}` : 'No sample walk recorded'}</span></div>{day.exposureReductionPct !== null && <span className="progress-day-exposure">−{Math.round(day.exposureReductionPct)}%<small>est. exposure</small></span>}</div>
    </section>

    <section className="progress-badges" aria-label="Your milestones">
      <div className="progress-section-heading"><div><h2>Make it a habit</h2><p>A little recognition along the way.</p></div><Trophy size={19}/></div>
      <div className="progress-badge-grid">
        <div className="progress-badge"><span className="progress-badge-medal progress-medal-mint"><Footprints size={25}/><i><Check size={9}/></i></span><strong>First steps</strong><small>Earned</small></div>
        <div className="progress-badge"><span className="progress-badge-medal progress-medal-peach"><Flame size={25}/><i><Check size={9}/></i></span><strong>Finding a rhythm</strong><small>5-day streak</small></div>
        <div className={`progress-badge progress-milestone${summary.challenge.unlocked ? ' is-earned' : ''}`} aria-label="Weekly six milestone" data-earned={summary.challenge.unlocked}><span className="progress-badge-medal progress-medal-lavender"><Trophy size={25}/>{summary.challenge.unlocked && <i><Check size={9}/></i>}</span><strong>The weekly six</strong><small>{summary.challenge.unlocked ? 'Unlocked' : 'In progress'}</small></div>
      </div>
    </section>

    <button className="progress-plan-route" onClick={onPlanRoute}><span className="progress-plan-icon"><Leaf size={21}/></span><span><strong>Plan a cleaner route</strong><small>Find the next step that suits your day.</small></span><ArrowRight size={19}/></button>
    <button className="progress-method-link" onClick={() => setMethods(true)}>About these estimates<ChevronRight size={14}/></button>
    {storageWarning && <p className="progress-storage-note">Browser storage is unavailable. Changes last for this session.</p>}
    {message && <p className="progress-feedback" role="status">{message}</p>}

    {methods && <dialog className="progress-dialog" ref={dialog} aria-labelledby={`progress-methods-${id}`} onCancel={() => setMethods(false)} onClick={event => { if (event.target === event.currentTarget) setMethods(false); }}>
      <div className="progress-dialog-heading"><div><p className="progress-eyebrow">THE DETAILS</p><h2 id={`progress-methods-${id}`}>About your progress</h2></div><button aria-label="Close progress details" onClick={() => setMethods(false)}><X size={20}/></button></div>
      <p className="progress-dialog-intro">A sample week in Sofia, 12–18 October 2026. This page uses illustrative activity in both Demo and Connected modes.</p>
      <h3>Estimated air exposure</h3><p>We compare the route’s estimated PM2.5 concentration multiplied by walking time with the same calculation for your usual route. This is a route comparison, not a measurement of what you breathed or a health outcome.</p>
      <h3>Estimated travel CO₂</h3><p>The sample commute choices are compared with a sample car baseline. Adding the demo walk does not assume that it replaces a car journey.</p>
      <div className="progress-assumptions">{summary.assumptions.map(assumption => <p key={assumption}><Info size={13}/><span>{assumption}</span></p>)}</div>
      <h3>Points and milestones</h3><p>Points celebrate participation in this demo. Add the Sunday walk once to complete six walks, extend your streak, and earn 60 points. Progress is saved in this browser.</p>
      <button className="progress-dialog-done" onClick={() => setMethods(false)}>Back to my progress<ArrowRight size={17}/></button>
      <button className="progress-reset" onClick={resetProgress}><RotateCcw size={14}/>Reset demo progress</button>
    </dialog>}
  </main>;
}
