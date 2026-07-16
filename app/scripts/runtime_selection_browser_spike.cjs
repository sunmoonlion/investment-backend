const assert = require("node:assert/strict");
const { chromium } = require("@playwright/test");

async function main() {
  const baseUrl = process.argv[2];
  if (!baseUrl) {
    throw new Error("base URL is required");
  }

  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(
      () => document.body.dataset.runtimeHarness === "passed",
      null,
      { timeout: 15_000 },
    );
    const state = JSON.parse(await page.locator("#state").textContent());
    assert.deepEqual(state.eventIds, ["event-1", "event-2", "event-3"]);
    assert.deepEqual(state.eventTypes, [
      "TimelineRunStarted",
      "TimelineWaitInputDisplayed",
      "TimelineRunCompleted",
    ]);
    assert.equal(state.lastEventId, "event-3");
    assert.equal(state.status, "completed");
    process.stdout.write(
      `${JSON.stringify({
        browser: "chromium",
        result: "passed",
        cursor_reconciliation: true,
        duplicate_events: 0,
        event_ids: state.eventIds,
      })}\n`,
    );
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
