import { describe, expect, it } from "vitest";
import en from "./catalog/en.json";
import th from "./catalog/th.json";
import { initI18n, translateClinicalText } from "./index";

describe("i18n catalogue", () => {
  it("keeps native invoice recovery guidance explicit in both languages", () => {
    expect([en["financeWrite.lookupNotice"], th["financeWrite.lookupNotice"]]).toMatchInlineSnapshot(`
      [
        "Check the server after an uncertain submission. Results cannot prove which operation created an invoice. Do not submit again until the outcome is checked.",
        "ตรวจสอบเซิร์ฟเวอร์เมื่อไม่ทราบผลการยืนยัน ผลค้นหาไม่สามารถยืนยันได้ว่าการดำเนินการใดสร้างใบแจ้งหนี้ อย่ายืนยันซ้ำจนกว่าจะตรวจสอบผล",
      ]
    `);
  });

  it("keeps both native inventory descriptions explicit about what is not shown", () => {
    for (const [locale, stock, availability] of [
      ["en", "does not show warehouse stock", "not available or usable stock"],
      ["th", "ไม่แสดงยอดคงเหลือรายคลัง", "ไม่ใช่จำนวนที่พร้อมเบิกหรือใช้ได้"],
    ] as const) {
      const i18n = initI18n(locale);
      expect(i18n.t("inventoryRead.batches.hint")).toContain(stock);
      expect(i18n.t("inventoryRead.balances.hint")).toContain(availability);
      expect(i18n.t("inventoryRead.page", { page: 2 })).toBe(locale === "en" ? "Page 2" : "หน้า 2");
    }
  });
  it("keeps Thai and English catalogue key sets identical", () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(th).sort());
  });

  it.each(["th", "en"] as const)(
    "provides paired auth/shared labels in %s",
    (locale) => {
      const i18n = initI18n(locale);
      for (const key of [
        "field.username",
        "field.password",
        "common.unavailable",
        "profile.localeDeviceOnly",
        "search.pageOf",
        "print.unverifiedTitle",
        "print.unverifiedBody",
        "notif.empty",
      ]) {
        expect(i18n.getResource(locale, "translation", key)).toEqual(
          expect.any(String),
        );
        expect(i18n.t(key)).not.toBe(key);
      }
    },
  );
  it("loads both catalogues with non-empty values", () => {
    for (const dict of [th, en])
      for (const [k, v] of Object.entries(dict))
        expect(v.trim(), k).not.toBe("");
  });

  it("renders required shell keys instead of returning the key", () => {
    const i18n = initI18n("th");
    for (const key of ["nav.today", "nav.patients", "app.name"])
      expect(i18n.t(key)).not.toBe(key);
  });

  it("falls back to English when a Thai value is absent", () => {
    const i18n = initI18n("th", { th: {}, en: { "nav.today": "Today" } });
    expect(i18n.t("nav.today")).toBe("Today");
  });

  it("interpolates page and total in paired pagination copy", () => {
    expect(initI18n("en").t("search.pageOf", { page: 2, total: 5 })).toBe(
      "Page 2 of 5",
    );
    expect(initI18n("th").t("search.pageOf", { page: 2, total: 5 })).toBe(
      "หน้า 2 จาก 5",
    );
  });

  it("keeps clinical free text untranslated", () => {
    expect(translateClinicalText("ผู้ป่วยมีอาการแพ้ยา")).toBe("ผู้ป่วยมีอาการแพ้ยา");
  });

  it("does not translate a record id passed as free text", () => {
    expect(translateClinicalText("PAT-0001")).toBe("PAT-0001");
  });

  it("does not reintroduce fixed transactional notification fixtures", () => {
    const excludedKeys = [
      "notif.bill",
      "notif.bill.meta",
      "notif.expiry",
      "notif.expiry.meta",
      "notif.labReceived",
      "notif.labReceived.meta",
    ];
    const fixedIdentifier = /\b(?:DC-)?INV-\d{3,}\b|\bP-\d{4,}\b/;
    for (const dict of [th, en]) {
      for (const key of excludedKeys) expect(dict, key).not.toHaveProperty(key);
      for (const [key, value] of Object.entries(dict))
        expect(value, key).not.toMatch(fixedIdentifier);
    }
  });
});
