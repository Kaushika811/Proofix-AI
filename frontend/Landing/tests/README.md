# Landing browser tests

From `frontend/Landing/`:

```sh
npm ci
npx playwright install chromium
npm run test:e2e
```

The runner starts Vite on port 4178. Tests use desktop Chromium, mobile Chromium,
and a reduced-motion viewport. No backend, API keys, or `.env` values are needed.

Coverage includes every visible walkthrough in both directions, large scroll
jumps, all 14 agents in the expanded architecture list and their parent moments,
anchor navigation, wheel scrolling, resizing, and reduced-motion results.
Visual checks cover single-stage visibility on short and tall screens, reversible
animation progress, the active underline, inactive-agent dimming, timeline
opacity during scroll, and navigation hiding/reappearance after rapid reversals.
Assertions check exactly one active item and its accessible `aria-current` state.
Traces and screenshots are retained for failures under `test-results/`.

The landing has four scenes sharing one pinned viewport; the 14 agents are intentionally kept
in its collapsed architecture section. These tests exercise both presentations.

If Chromium is already installed, an explicit local executable can be used:
`PLAYWRIGHT_CHROMIUM_EXECUTABLE=/absolute/path/to/chromium npm run test:e2e`.
The default remains Playwright's bundled browser; the override is optional.
