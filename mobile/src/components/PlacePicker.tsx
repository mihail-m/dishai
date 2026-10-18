import { useEffect, useId, useRef, useState } from 'react';
import { ArrowUpRight, Check, Crosshair, LoaderCircle, MapPin, Search, X } from 'lucide-react';
import { ROUTE_PLACES, searchRoutePlaces } from '../data';
import { getGeocoderUrl, searchStreetAddresses } from '../place-search';
import type { DataMode, Place } from '../types';
import '../place-picker.css';

interface PlacePickerProps {
  endpoint: 'from' | 'to';
  current: Place;
  mode: DataMode;
  onSelect: (place: Place) => void;
  onPickMap: () => void;
  onClose: () => void;
}

export default function PlacePicker({ endpoint, current, mode, onSelect, onPickMap, onClose }: PlacePickerProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const request = useRef<AbortController | null>(null);
  const requestVersion = useRef(0);
  const headingId = useId();
  const inputId = useId();
  const descriptionId = useId();
  const [query, setQuery] = useState('');
  const [onlinePlaces, setOnlinePlaces] = useState<Place[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onlineEnabled = !!getGeocoderUrl();
  const localPlaces = query.trim() ? searchRoutePlaces(query) : ROUTE_PLACES;
  const samePlace = (place: Place) => place.coordinates.every((coordinate, index) => coordinate === current.coordinates[index]);

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => { request.current?.abort(); requestVersion.current += 1; element?.close(); };
  }, []);

  const changeQuery = (value: string) => {
    request.current?.abort();
    requestVersion.current += 1;
    setQuery(value); setOnlinePlaces(null); setError(null); setSearching(false);
  };

  const searchOnline = async () => {
    if (!onlineEnabled || query.trim().length < 3 || searching) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const version = ++requestVersion.current;
    setSearching(true); setError(null); setOnlinePlaces(null);
    try {
      const places = await searchStreetAddresses(query, controller.signal);
      if (!controller.signal.aborted && version === requestVersion.current) setOnlinePlaces(places);
    } catch (caught) {
      if (!controller.signal.aborted && version === requestVersion.current) {
        setError(caught instanceof Error ? caught.message : 'Address search is unavailable. Choose a local place or a point on the map.');
      }
    } finally { if (!controller.signal.aborted && version === requestVersion.current) setSearching(false); }
  };

  const placeList = (places: readonly Place[]) => <ul className="place-picker-results">
    {places.map((place, index) => <li key={`${place.coordinates.join(',')}-${index}`}>
      <button type="button" className={samePlace(place) ? 'is-current' : ''} onClick={() => onSelect(place)}>
        <span className="place-picker-pin"><MapPin size={17}/></span>
        <span><strong>{place.label}</strong><small>{samePlace(place) ? 'Current selection' : 'Sofia, Bulgaria'}</small></span>
        {samePlace(place) ? <Check size={16}/> : <ArrowUpRight size={16}/>}
      </button>
    </li>)}
  </ul>;

  return <dialog ref={dialog} className="glass-dialog place-picker" aria-labelledby={headingId} aria-describedby={descriptionId}
    onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); onClose(); } }}
    onCancel={event => { event.preventDefault(); onClose(); }} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="sheet-grabber"/>
    <div className="sheet-head"><h2 id={headingId}>{endpoint === 'from' ? 'Starting point' : 'Destination'}</h2><button type="button" className="icon-button" onClick={onClose} aria-label="Close address search"><X size={20}/></button></div>
    <p id={descriptionId} className="place-picker-intro">Search central Sofia streets and places, or choose a point on the map.</p>
    <form onSubmit={event => { event.preventDefault(); void searchOnline(); }}>
      <label className="place-picker-label" htmlFor={inputId}>Place or street</label>
      <div className="place-picker-input"><Search size={18}/><input id={inputId} type="search" placeholder="e.g. Vitosha or City Garden" value={query} onChange={event => changeQuery(event.target.value)} autoComplete="off" maxLength={180} autoFocus/></div>
      {onlineEnabled && <button type="submit" className="place-picker-online" disabled={searching || query.trim().length < 3}>{searching ? <LoaderCircle size={15} className="spin"/> : <Search size={15}/>}Search addresses</button>}
    </form>
    <button type="button" className="place-picker-map" onClick={onPickMap}><span><Crosshair size={20}/></span><span><strong>Choose on map</strong><small>Set the {endpoint === 'from' ? 'starting point' : 'destination'} precisely</small></span><ArrowUpRight size={17}/></button>
    <div className="place-picker-section-title">{query.trim() ? 'Matching places' : 'Explore Sofia'}<span>{localPlaces.length}</span></div>
    {localPlaces.length ? placeList(localPlaces) : <p className="place-picker-empty">No local match. Choose your address on the map{onlineEnabled ? ' or search addresses above' : ''}.</p>}
    <div aria-live="polite" aria-atomic="true" className="place-picker-status">
      {searching && <p>Searching Sofia addresses…</p>}
      {error && <p className="place-picker-error" role="alert">{error}</p>}
      {onlinePlaces !== null && <><div className="place-picker-section-title">Address results<span>{onlinePlaces.length}</span></div>{onlinePlaces.length ? placeList(onlinePlaces) : <p className="place-picker-empty">No address found in Sofia. Try a street name and number, or choose a point on the map.</p>}</>}
    </div>
    <p className="place-picker-footnote">{mode === 'demo' ? 'Demo routing covers central Sofia.' : 'Route availability depends on the connected road network.'}</p>
    {onlineEnabled && <p className="place-picker-attribution">Address searches use your configured service. <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a></p>}
  </dialog>;
}
