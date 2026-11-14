/**
 * Canonical client route paths. Later plans extend this object; tests and
 * features must read these constants instead of hardcoding paths.
 *
 * `patient` is a builder, not a static path: a patient record destination
 * always carries the record id the server returned and is never assembled by
 * string concatenation at a call site.
 */
export const ROUTES = {
  login: "/clinic/login",
  home: "/clinic",
  search: "/clinic/search",
  system: "/clinic/system",
  profile: "/clinic/profile",
  patients: "/clinic/patients",
  pharmacy: "/clinic/pharmacy",
  finance: "/clinic/finance",
  livePhotos: "/clinic/lab/photos",
  livePhotoPattern: "/clinic/lab/photos/:id",
  livePhoto: (id: string) => `/clinic/lab/photos/${encodeURIComponent(id)}`,
  reception: "/clinic/reception",
  queue: "/clinic/reception/queue",
  checkout: "/clinic/reception/checkout",
  appointments: "/clinic/appointments",
  visitPattern: "/clinic/visits/:id",
  visit: (id: string) => `/clinic/visits/${encodeURIComponent(id)}`,
  appointment: (id: string) => `/clinic/appointments/${encodeURIComponent(id)}`,
  patient: (id: string) => `/clinic/patients/${encodeURIComponent(id)}`,
  photoPattern: "/clinic/patients/:id/photos",
  photoComparePattern: "/clinic/patients/:id/photos/compare",
  photos: (id: string) => `/clinic/patients/${encodeURIComponent(id)}/photos`,
  photoCompare: (id: string) =>
    `/clinic/patients/${encodeURIComponent(id)}/photos/compare`,
} as const;

/** Static route keys only: a builder such as `patient` is never a nav target. */
export type StaticRouteKey = {
  [Key in keyof typeof ROUTES]: (typeof ROUTES)[Key] extends string
    ? Key
    : never;
}[keyof typeof ROUTES];
