# Lab report preview implementation plan

**Goal:** View a private lab report without leaving Appointment, Encounter, or Patient History, and download the Frappe-stored file.

**Approved design:** View opens an on-demand overlay with the filename, a PDF or image preview, Download, and Close. Keep the existing report list and encounter editor mounted. The existing private File, exact `fid` URL, storage settings, and native permissions remain authoritative. No new endpoint or upload behavior.

- [ ] Add failing tests in `app/src/features/clinical/native-lab-reports.test.tsx` for View versus Download, exact private reads, no eager file requests, and close/failure handling.
- [ ] Implement the shared viewer through `app/src/features/clinical/NativeLabReports.tsx` using the existing dialog and styling patterns. Preserve the current native URL validation; render only supported PDF/JPEG/PNG content, clean up preview resources, and retain native download when preview fails.
- [ ] Run focused tests, typecheck and a browser check with real synthetic PDF and image bytes. Cover keyboard dismissal, preserved draft state and denied or mismatched content. Then run the app/proxy suite and native build.
