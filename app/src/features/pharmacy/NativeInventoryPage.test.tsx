import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { useSession } from "../../app/SessionProvider";
import { SURFACE_ROUTES } from "../../app/surface-routes";
import { LOCALE_KEY } from "../../i18n/locale-storage";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession } from "../../test/network-spy";
import { renderWithDataRouter } from "../../test/render";
import { clearNativeCsrfToken } from "../../transport/native/session";

const batchRecords = [
  { name: "PCM-P6L042", batch_id: "P6L042", item: "PARACETAMOL-500MG-TAB", expiry_date: "2027-02-28", disabled: 0 },
  { name: "AMX-A7P193", batch_id: "A7P193", item: "AMOXICILLIN-500MG-CAP", expiry_date: "2027-09-30", disabled: 1 },
  { name: "NS-S8H071", batch_id: "S8H071", item: "SODIUM-CHLORIDE-09-100ML", expiry_date: "2028-06-30", disabled: 0 },
];
const balance = { name: "BIN-88", item_code: "PARACETAMOL-500MG-TAB", warehouse: "Main Pharmacy - CL", stock_uom: "Tablet", actual_qty: 125.5 };
const movement = {
  name: "SLE-204", item_code: "PARACETAMOL-500MG-TAB", warehouse: "Main Pharmacy - CL",
  posting_date: "2026-11-20", posting_time: "09:42:11", actual_qty: -2.5,
  qty_after_transaction: 125.5, voucher_type: "Stock Entry", voucher_no: "MAT-STE-2026-00061", is_cancelled: 0,
};

function rows(doctype: string, data: unknown[]) {
  server.use(http.get(`/api/resource/${encodeURIComponent(doctype)}`, () => HttpResponse.json({ data })));
}

function open(path: string, withSessionChange = false) {
  const route = SURFACE_ROUTES.find((candidate) => candidate.path === path)!;
  return renderWithDataRouter([{
    path,
    element: <>{withSessionChange && <SessionChange />}{route.element}</>,
  }], { path, role: "PHA" });
}

function SessionChange() {
  const { refresh } = useSession();
  return <button type="button" onClick={() => { void refresh(); }}>Change session</button>;
}

beforeEach(() => {
  localStorage.setItem(LOCALE_KEY, "en");
  vi.stubEnv("VITE_CLINIC_NATIVE", "1");
  server.use(http.get(NATIVE_BOOTSTRAP, () => nativeSession("PHA")));
});

afterEach(() => {
  vi.unstubAllEnvs();
  clearNativeCsrfToken();
  localStorage.removeItem(LOCALE_KEY);
});

it.each([
  [ROUTES.pharmacyItems, "Medicines, supplies & services"],
  [ROUTES.pharmacyBatches, "Positive physical stock batches"],
  [ROUTES.pharmacyBalances, "Item–location stock & thresholds"],
  [ROUTES.pharmacyMovements, "Immutable ledger"],
])("keeps fixture content and makes no native read at %s in fixture mode", async (path, caption) => {
  vi.stubEnv("VITE_CLINIC_NATIVE", "0");
  const read = vi.fn();
  server.use(http.get("/api/resource/:doctype", () => {
    read();
    return HttpResponse.json({ data: [] });
  }));
  open(path);
  expect(await screen.findByRole("table", { name: caption })).toBeVisible();
  expect(screen.getByText("Demo data", { exact: true })).toBeVisible();
  expect(read).not.toHaveBeenCalled();
});

it("shows stock item master fields and never a fixture balance", async () => {
  rows("Item", [{ name: "IV-SALINE", item_code: "NS-100ML", item_name: "Normal saline 100 mL", stock_uom: "Bag", disabled: 0, is_stock_item: 1, has_batch_no: 1 }]);
  open(ROUTES.pharmacyItems);
  const row = within(await screen.findByRole("row", { name: /NS-100ML/ }));
  for (const value of ["Normal saline 100 mL", "Bag", "Yes", "Enabled"]) {
    expect(row.getByText(value)).toBeVisible();
  }
  expect(screen.queryByRole("columnheader", { name: "On hand" })).not.toBeInTheDocument();
  expect(screen.queryByText("Demo data", { exact: true })).not.toBeInTheDocument();
});

it("shows each batch's item, expiry and record status without implying stock", async () => {
  rows("Batch", batchRecords);
  open(ROUTES.pharmacyBatches);
  for (const batch of batchRecords) {
    const row = within(await screen.findByRole("row", { name: new RegExp(batch.batch_id) }));
    expect(row.getByRole("cell", { name: batch.item })).toBeVisible();
    expect(row.getByRole("cell", { name: batch.expiry_date })).toBeVisible();
    expect(row.getByRole("cell", { name: batch.disabled ? "Disabled" : "Enabled" })).toBeVisible();
  }
  expect(screen.getByText(/does not show warehouse stock/i)).toBeVisible();
  expect(screen.queryByRole("columnheader", { name: /warehouse|quantity/i })).not.toBeInTheDocument();
  expect(screen.queryByText("Demo data", { exact: true })).not.toBeInTheDocument();
});

it("shows an empty live batch list without substituting demo lots", async () => {
  rows("Batch", []);
  open(ROUTES.pharmacyBatches);
  expect(await screen.findByText("No batch records found.")).toBeVisible();
  expect(screen.getAllByRole("row")).toHaveLength(2);
  expect(screen.queryByText("Demo data", { exact: true })).not.toBeInTheDocument();
});

it("shows the recorded actual balance and unit, not available stock", async () => {
  rows("Bin", [balance]);
  open(ROUTES.pharmacyBalances);
  const row = within(await screen.findByRole("row", { name: /PARACETAMOL-500MG-TAB/ }));
  for (const value of ["Main Pharmacy - CL", "Tablet", "125.5"]) {
    expect(row.getByRole("cell", { name: value })).toBeVisible();
  }
  expect(screen.getByRole("columnheader", { name: "Actual quantity" })).toBeVisible();
  expect(screen.getByText(/not available or usable stock/i)).toBeVisible();
  expect(screen.queryByText("Demo data", { exact: true })).not.toBeInTheDocument();
});

it("keeps ledger quantities signed and separate from post-movement balances and sample operations", async () => {
  rows("Stock Ledger Entry", [movement, { ...movement, name: "SLE-205", actual_qty: 4, qty_after_transaction: 129.5, voucher_no: "MAT-STE-2026-00062" }]);
  open(ROUTES.pharmacyMovements);
  const outgoing = within(await screen.findByRole("row", { name: /MAT-STE-2026-00061/ }));
  expect(outgoing.getByRole("cell", { name: "-2.5" })).toBeVisible();
  expect(outgoing.getByRole("cell", { name: "125.5" })).toBeVisible();
  expect(outgoing.getByText("2026-11-20 09:42:11")).toBeVisible();
  expect(outgoing.getByText("Stock Entry")).toBeVisible();
  expect(within(screen.getByRole("row", { name: /MAT-STE-2026-00062/ })).getByRole("cell", { name: "+4" })).toBeVisible();
  expect(screen.getByText(/item’s stock unit/i)).toBeVisible();
  expect(screen.queryByTestId("stock-write-form")).not.toBeInTheDocument();
  expect(screen.queryByText("Demo data", { exact: true })).not.toBeInTheDocument();
});

it("keeps independently enabled movement write forms when the ledger is denied", async () => {
  server.use(
    http.get(NATIVE_BOOTSTRAP, () => nativeSession("PHA", { "write.stock.transfer": "enabled", "write.stock.adjust": "enabled" })),
    http.get("/api/resource/Stock%20Ledger%20Entry", () => new HttpResponse(null, { status: 403 })),
  );
  open(ROUTES.pharmacyMovements);
  expect(await screen.findByRole("alert")).toHaveTextContent("You don’t have permission to view these inventory records.");
  expect(screen.getAllByTestId("stock-write-form")).toHaveLength(2);
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.queryByText("Demo data", { exact: true })).not.toBeInTheDocument();
});

it("treats a malformed actual quantity as unavailable and offers a read retry", async () => {
  rows("Bin", [{ ...balance, actual_qty: "0" }]);
  open(ROUTES.pharmacyBalances);
  expect(await screen.findByRole("alert")).toHaveTextContent("Inventory records are unavailable.");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.queryByText("Demo data", { exact: true })).not.toBeInTheDocument();
  rows("Bin", [balance]);
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("cell", { name: "125.5" })).toBeVisible();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("pages 25 rows, hides the sentinel and reads the previous page again", async () => {
  const offsets: string[] = [];
  const pageOne = Array.from({ length: 26 }, (_, i) => ({ ...batchRecords[0], name: `batch-${i}`, batch_id: `BATCH-${i}` }));
  server.use(http.get("/api/resource/Batch", ({ request }) => {
    const offset = new URL(request.url).searchParams.get("limit_start");
    offsets.push(offset!);
    return HttpResponse.json({ data: offset === "0" ? pageOne : [pageOne[25]] });
  }));
  open(ROUTES.pharmacyBatches);
  expect(await screen.findByRole("cell", { name: "BATCH-24" })).toBeVisible();
  expect(screen.getAllByRole("row")).toHaveLength(26);
  expect(screen.queryByRole("cell", { name: "BATCH-25" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByRole("cell", { name: "BATCH-25" })).toBeVisible();
  expect(screen.queryByRole("cell", { name: "BATCH-24" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Previous" }));
  expect(await screen.findByRole("cell", { name: "BATCH-24" })).toBeVisible();
  expect(offsets).toEqual(["0", "25", "0"]);
});

it("clears old rows and ignores a pending page when the session changes", async () => {
  let release!: () => void;
  const slow = new Promise<void>((resolve) => { release = resolve; });
  let pending: AbortSignal | undefined;
  let replacement = false;
  server.use(
    http.get(NATIVE_BOOTSTRAP, () => nativeSession(replacement ? "ADM" : "PHA")),
    http.get("/api/resource/Batch", async ({ request }) => {
      if (new URL(request.url).searchParams.get("limit_start") === "25") {
        pending = request.signal;
        await slow;
        return HttpResponse.json({ data: [batchRecords[1]] });
      }
      return HttpResponse.json({ data: replacement ? [batchRecords[2]] :
        Array.from({ length: 26 }, (_, i) => ({ ...batchRecords[0], name: `batch-${i}`, batch_id: `OLD-${i}` })) });
    }),
  );
  open(ROUTES.pharmacyBatches, true);
  expect(await screen.findByRole("cell", { name: "OLD-0" })).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(pending).toBeDefined());
  expect(screen.getByText("Loading…")).toBeVisible();
  expect(screen.queryByText("OLD-0")).not.toBeInTheDocument();
  replacement = true;
  await userEvent.click(screen.getByRole("button", { name: "Change session" }));
  expect(await screen.findByRole("cell", { name: "S8H071" })).toBeVisible();
  expect(pending?.aborted).toBe(true);
  await act(async () => { release(); });
  expect(screen.queryByText("A7P193")).not.toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "S8H071" })).toBeVisible();
});
