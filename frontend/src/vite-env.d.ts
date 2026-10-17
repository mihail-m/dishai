/// <reference types="vite/client" />
declare module "*.mjs?url" { const url: string; export default url; }
interface ImportMetaEnv {
  readonly MAP_PROVIDER?: string;
  readonly GOOGLE_MAPS_BROWSER_KEY?: string;
}
