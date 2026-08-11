import { test, expect } from "playwright/test";

test("landing loads in Persian", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /بازار خودروی ایران/ })).toBeVisible();
});

test("login page is reachable", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "ورود" })).toBeVisible();
});

test("explore route renders filters", async ({ page }) => {
  await page.goto("/explore");
  await expect(page.getByPlaceholder("جستجو…")).toBeVisible();
});
