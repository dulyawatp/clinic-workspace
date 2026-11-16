const ICON_PATHS: Record<string, string> = {
  users:
    "M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6M3 20a6 6 0 0 1 12 0M16 7a3 3 0 1 1 3 5M17 14a5 5 0 0 1 4 4",
  pulse: "M2 12h4l2-6 4 12 2.5-6H22",
  clipboard: "M9 4h6v3H9zM7 6h10v15H7zM10 12h4M10 16h4",
  "check-circle": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M8 12l3 3 5-6",
  flask: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3",
  camera: "M4 8h3l2-3h6l2 3h3v11H4zM12 16a3 3 0 1 0 0-6 3 3 0 0 0 0 6",
  alert: "M12 4l9 16H3zM12 10v4M12 17h.01",
  download: "M12 4v10M8 11l4 4 4-4M5 20h14",
  box: "M3 8l9-5 9 5-9 5-9-5M3 8v8l9 5 9-5V8",
};

/** Minimal inline icon set for the designed tiles; decorative by contract. */
export function SurfaceIcon({ name }: { name: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={ICON_PATHS[name] ?? ICON_PATHS.box} />
    </svg>
  );
}
