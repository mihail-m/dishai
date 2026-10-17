import type { RefObject } from "react";
import type { Layer, PickingInfo } from "@deck.gl/core";
import type { CaiValues, ReferenceResp, SensorsResp } from "./types";
import type { Geometry } from "./map/cai";
import type { MapHandle } from "./map/BaseMap";

/** What a map view can ask of the shared map stage (the map stays mounted across tabs). */
export interface Stage {
  set: (s: {
    layers: Layer[]; base: "full" | "dim" | "off"; onPick?: (i: PickingInfo) => void; cursor?: string;
    tooltip?: (i: PickingInfo) => { html: string; style?: Record<string, string> } | null; legend?: React.ReactNode;
  }) => void;
  map: RefObject<MapHandle | null>;
  geometry: Geometry | null;
  values: CaiValues | null;
  sensors: SensorsResp | null;
  reference: ReferenceResp | null;
  relative: boolean;
  setRelative: (b: boolean) => void;
}
