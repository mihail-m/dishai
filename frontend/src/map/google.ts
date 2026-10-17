import { GOOGLE_KEY } from "./config";

// Maps and Places may be opened in either order, including after a renderer switch.
let loader: Promise<typeof import("@googlemaps/js-api-loader")> | undefined;
export function googleLoader() {
  return loader ??= import("@googlemaps/js-api-loader").then((module) => {
    module.setOptions({ key: GOOGLE_KEY, v: "weekly" });
    return module;
  });
}
