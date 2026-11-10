import { test, expect } from "@playwright/test";
import path from "node:path";
test("real defective and clean inspections, viewer, downloads and refresh", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.getByText("OPTICAL ARRAY ONLINE")).toBeVisible();
  await page.getByRole("button", { name: /COLLECTIBLE CARD PACK/ }).click();
  await expect(
    page.getByRole("button", { name: "2 · RUN INSPECTION" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "2 · RUN INSPECTION" }).click();
  await expect(page.locator(".status-card h2")).toHaveText(
    /REVIEW REQUIRED|FAIL/,
    { timeout: 60000 },
  );
  await expect(page.locator(".findings button").first()).toBeVisible();
  await page.getByRole("button", { name: "overlay", exact: true }).click();
  await page.getByRole("button", { name: "Zoom in" }).click();
  await expect(page.locator(".zoom span")).toHaveText("125%");
  await page.locator(".findings button").first().click();
  await expect(page.locator(".finding-detail")).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "PDF REPORT" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/report.pdf$/);
  await page.screenshot({
    path: "../docs/console-inspection.png",
    fullPage: true,
  });
  await page.reload();
  await expect(page.locator(".status-card h2")).toHaveText(
    /REVIEW REQUIRED|FAIL/,
    { timeout: 20000 },
  );
  await page.getByLabel("Use matching files (no defects)").check();
  await page.getByRole("button", { name: /CONSUMER PRODUCT LABEL/ }).click();
  await page.getByRole("button", { name: "2 · RUN INSPECTION" }).click();
  await expect(page.locator(".status-card h2")).toHaveText("PASS", {
    timeout: 60000,
  });
  expect(errors).toEqual([]);
});
test("uploads, multipage selection and mobile layout", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page
    .getByLabel("Upload approved master")
    .setInputFiles(path.resolve("../dataset/multipage.pdf"));
  await expect(page.getByLabel("reference page")).toHaveCount(1);
  await page.getByLabel("reference page").selectOption("1");
  await page
    .getByLabel("Upload candidate artwork")
    .setInputFiles(path.resolve("../dataset/cmyk.tiff"));
  await expect(
    page.getByRole("button", { name: "2 · RUN INSPECTION" }),
  ).toBeEnabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({ path: "../docs/console-mobile.png", fullPage: true });
});
test("clean initial console screenshot", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expect(page.locator(".status-card h2")).toHaveText("STANDING BY");
  await page.screenshot({ path: "../docs/console.png", fullPage: true });
});
