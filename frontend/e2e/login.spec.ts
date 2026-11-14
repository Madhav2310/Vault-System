import { expect, test } from "@playwright/test";

// A local run signs itself in; a deployed one (404 from /v1/session/local) still asks for an API key.
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

async function noData(page: import("@playwright/test").Page) {
  await page.route("**/v1/documents?**", (r) => r.fulfill(json({ items: [], next_cursor: null })));
  await page.route("**/v1/chat/sessions?**", (r) => r.fulfill(json({ items: [], next_cursor: null })));
}

test("a local run signs in without asking for a key", async ({ page }) => {
  await noData(page);
  let signedIn = false;
  await page.route("**/v1/session/local", (r) => {
    signedIn = true;
    return r.fulfill({ status: 204 });
  });
  await page.route("**/v1/session", (r) => r.fulfill({ status: signedIn ? 204 : 401, contentType: "application/json", body: "{}" }));
  await page.goto("/app/");
  await expect(page.getByRole("link", { name: "Library" })).toBeVisible();
  await expect(page.getByLabel("API key")).toHaveCount(0);
});

test("a deployed run still asks for an API key", async ({ page }) => {
  await noData(page);
  await page.route("**/v1/session/local", (r) => r.fulfill(json({ code: "not_found" }, 404)));
  await page.route("**/v1/session", (r) => r.fulfill(json({ code: "unauthenticated" }, 401)));
  await page.goto("/app/");
  await expect(page.getByRole("heading", { name: "Enter your API key" })).toBeVisible();
});
