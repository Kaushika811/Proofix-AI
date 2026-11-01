import { expect, test, type Page } from '@playwright/test'

async function waitForScrollToSettle(page: Page) {
  await page.evaluate(() => new Promise<void>((resolve) => {
    let previous = scrollY
    let stableFrames = 0
    const check = () => {
      stableFrames = Math.abs(scrollY - previous) < 0.5 ? stableFrames + 1 : 0
      previous = scrollY
      if (stableFrames >= 3) resolve()
      else requestAnimationFrame(check)
    }
    requestAnimationFrame(check)
  }))
}

async function centerAtReadingLine(page: Page, selector: string) {
  await page.locator(selector).evaluate(async (el) => {
    // Let viewport/media-query changes commit before measuring responsive content.
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    const rect = el.getBoundingClientRect()
    window.scrollTo({
      top:
        window.scrollY + rect.top + rect.height / 2 - window.innerHeight * 0.38,
      behavior: 'instant'
    })
  })
}
async function expectMoment(page: Page, index: number) {
  await expect(page.locator('[data-stage]:visible')).toHaveCount(1)
  await expect(page.locator(`#scene-${index}`)).toBeVisible()
  await expect(page.locator('[data-moment][data-active="true"]')).toHaveCount(1)
  await expect(page.locator(`#moment-${index}`)).toHaveAttribute(
    'data-active',
    'true'
  )
  const nav = page.getByRole('navigation', { name: 'Timeline position' })
  await expect(nav.locator('[aria-current="step"]')).toHaveCount(1)
  await expect(nav.locator('[aria-current="step"]')).toHaveAttribute(
    'href',
    `#moment-${index}`
  )
}

test.beforeEach(async ({ page }) => {
  await page.route('https://fonts.googleapis.com/**', (route) => route.abort())
  await page.goto('/')
  await page.evaluate(() => document.fonts.ready)
})

test('all four visible moments highlight forwards, backwards, and after large jumps', async ({
  page
}) => {
  await expect(
    page.getByRole('navigation', { name: 'Timeline position' })
  ).toHaveCount(0)
  for (const index of [1, 2, 3, 4, 3, 2, 1, 4, 1]) {
    await centerAtReadingLine(page, `#moment-${index}`)
    await expectMoment(page, index)
  }
  await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }))
  await expect(page.locator('[data-moment][data-active="true"]')).toHaveCount(0)
  await expect(
    page.getByRole('navigation', { name: 'Timeline position' })
  ).toHaveCount(0)
})

test('all 14 expanded agents highlight individually in both directions', async ({
  page
}) => {
  const architecture = page.locator('#architecture')
  await architecture.locator('summary').click()
  const names = [
    'Environment Precheck',
    'Repository Indexing',
    'Repository Intelligence',
    'Dependency Analyzer',
    'Static Analysis',
    'Failure Reproduction',
    'Root Cause Analysis',
    'Blast Radius',
    'Context Engineering',
    'Repair Planner',
    'Patch Generator',
    'Mutation Validation',
    'Security Re-scan',
    'Mergeability Assessment'
  ]
  const parents = [1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 4, 4]
  await expect(architecture.locator('[data-agent]')).toHaveCount(14)
  for (const index of [...names.keys(), ...[...names.keys()].reverse()]) {
    const id = `agent-${String(index + 1).padStart(2, '0')}`
    await centerAtReadingLine(page, `#${id}`)
    await expect(architecture.locator('[aria-current="step"]')).toHaveCount(1)
    await expect(page.locator(`#${id}`)).toHaveAttribute('aria-current', 'step')
    await expect(page.locator(`#${id} strong`)).toHaveText(names[index])
    await expect(page.locator(`#${id} a`)).toHaveAttribute(
      'href',
      `#moment-${parents[index]}`
    )
  }
  await architecture.locator('summary').click()
  await expect(architecture.locator('[aria-current="step"]')).toHaveCount(0)
})

test('anchor navigation, resize, and section navigation never leave stale highlights', async ({
  page
}) => {
  await centerAtReadingLine(page, '#moment-1')
  const nav = page.getByRole('navigation', { name: 'Timeline position' })
  await nav.locator('a[href="#moment-4"]').click()
  await expectMoment(page, 4)
  // The next stage becomes active before native smooth scrolling has finished.
  await waitForScrollToSettle(page)
  await page.setViewportSize({ width: 390, height: 700 })
  await centerAtReadingLine(page, '#moment-2')
  await expectMoment(page, 2)
  await page.setViewportSize({ width: 1440, height: 900 })
  await centerAtReadingLine(page, '#moment-3')
  await expectMoment(page, 3)
  await centerAtReadingLine(page, '#innovations')
  const sectionNav = page.getByRole('navigation', { name: 'Page sections' })
  await expect(
    sectionNav.getByRole('button', { name: 'Why ProoFix' })
  ).toHaveAttribute('aria-current', 'location')
  await expect(nav).toHaveCount(0)
  await expect(page.locator('[data-moment][data-active="true"]')).toHaveCount(0)
})

test('wheel scrolling updates highlighting and reduced motion preserves readable results', async ({
  page
}) => {
  await centerAtReadingLine(page, '#moment-1')
  await expectMoment(page, 1)
  const distance = await page
    .locator('#moment-2')
    .evaluate(
      (el) =>
        el.getBoundingClientRect().top +
        el.getBoundingClientRect().height / 2 -
        innerHeight * 0.38
    )
  await page.mouse.wheel(0, distance)
  await expectMoment(page, 2)
  await waitForScrollToSettle(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await expect(page.locator('#pipeline')).toHaveClass(/repair-reduced/)
  await centerAtReadingLine(page, '#moment-3')
  await expectMoment(page, 3)
  await expect(page.getByTestId('patch-challenge')).toHaveAttribute(
    'data-progress',
    '1000'
  )
  await expect(page.getByTestId('failure-trace')).toHaveAttribute(
    'data-trace-state',
    'failed'
  )
  await expect
    .poll(() =>
      page.evaluate(
        () => getComputedStyle(document.documentElement).scrollBehavior
      )
    )
    .toBe('auto')
  await expect
    .poll(() =>
      page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
    )
    .toBe(true)
})

test('the highlight changes once at each adjacent stage boundary', async ({
  page
}) => {
  for (const next of [2, 3, 4]) {
    for (const offset of [-2, 2, -2]) {
      await page.locator(`#moment-${next}`).evaluate((el, offset) => {
        const top = el.getBoundingClientRect().top + scrollY
        scrollTo({
          top: top - innerHeight * 0.38 + offset,
          behavior: 'instant'
        })
      }, offset)
      await expectMoment(page, offset < 0 ? next - 1 : next)
    }
  }
})

test('underline and dimming follow every agent and reset when collapsed', async ({ page }) => {
  const list = page.locator('.architecture-agents')
  await page.locator('#architecture summary').click()
  for (const index of [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 7, 1]) {
    const id = `agent-${String(index).padStart(2, '0')}`
    await centerAtReadingLine(page, `#${id}`)
    await expect(page.locator(`#${id}`)).toHaveAttribute('aria-current', 'step')
    await expect(page.locator(`#${id} strong`)).toHaveCSS('text-decoration-line', 'underline')
    await expect(page.locator(`#${id} strong`)).toHaveCSS('opacity', '1')
    const inactive = list.locator('li:not([aria-current]) strong')
    await expect(inactive).toHaveCount(13)
    for (const name of await inactive.all()) {
      await expect(name).toHaveCSS('text-decoration-line', 'none')
      await expect(name).toHaveCSS('opacity', '0.6')
    }
  }
  await page.locator('#architecture summary').click()
  await expect(list).toHaveAttribute('data-has-active', 'false')
  await page.locator('#architecture summary').click()
  await centerAtReadingLine(page, '#agent-09')
  await expect(page.locator('#agent-09 strong')).toHaveCSS('text-decoration-line', 'underline')
})

test('scroll dimming and navigation reappearance use the current stage, never the previous one', async ({ page }) => {
  const timeline = page.locator('nav[aria-label="Timeline position"]')
  const pageNav = page.locator('nav[aria-label="Page sections"]')
  await centerAtReadingLine(page, '#moment-2')
  await expectMoment(page, 2)
  // Sustain real scrolling long enough to verify the visual state mid-motion.
  await page.evaluate(async () => {
    for (let frame = 0; frame < 25; frame++) {
      scrollBy({ top: 2, behavior: 'instant' })
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
    }
  })
  await expect(timeline).toHaveAttribute('data-scrolling', 'true')
  await expect(timeline).toHaveCSS('opacity', '0.55')
  await expect(pageNav).toHaveAttribute('aria-hidden', 'true')
  await expectMoment(page, 2)
  await expect(timeline).toHaveAttribute('data-scrolling', 'false')
  await expect(timeline).toHaveCSS('opacity', '1')
  await expect(page.locator('#scene-2 .repair-moment-copy > p').first()).toHaveCSS('text-decoration-line', 'underline')

  // Exit and return before the idle timer can reveal a stale page menu.
  await centerAtReadingLine(page, '#innovations')
  await centerAtReadingLine(page, '#moment-4')
  await expectMoment(page, 4)
  await expect(timeline).toHaveAttribute('data-scrolling', 'false')
  await expect(pageNav).toHaveAttribute('aria-hidden', 'true')
  await centerAtReadingLine(page, '#innovations')
  await expect(timeline).toHaveCount(0)
  await expect(pageNav).toHaveAttribute('aria-hidden', 'false')
  await expect(pageNav.locator('[aria-current="location"]')).toHaveText('Why ProoFix')
  await expect(pageNav.locator('[aria-current="location"]')).toHaveCSS('opacity', '1')
  await centerAtReadingLine(page, '#moment-1')
  await expectMoment(page, 1)
  await expect(pageNav).toHaveAttribute('aria-hidden', 'true')
})

test('one pinned scene occupies the viewport on short and tall screens', async ({ page }, testInfo) => {
  for (const height of [720, 1000]) {
    await page.setViewportSize({ width: page.viewportSize()!.width, height })
    for (const index of [1, 2, 3, 4, 3, 2, 1]) {
      await centerAtReadingLine(page, `#moment-${index}`)
      await expectMoment(page, index)
      await expect(page.locator('.repair-viewport')).toHaveCSS('position', 'sticky')
      const box = await page.locator(`#scene-${index}`).boundingBox()
      expect(box!.y).toBeGreaterThanOrEqual(64)
      expect(box!.y + box!.height).toBeLessThanOrEqual(height)
      await expect(page.locator(`[data-stage]:not(#scene-${index})`)).toHaveCount(3)
      for (const other of await page.locator(`[data-stage]:not(#scene-${index})`).all()) {
        await expect(other).toBeHidden()
      }
    }
  }
  if (testInfo.project.name === 'desktop') {
    await page.setViewportSize({ width: 1440, height: 720 })
    await centerAtReadingLine(page, '#moment-2')
    await expectMoment(page, 2)
    await expect(page.locator('#scene-2')).toHaveCSS('opacity', '1')
    await page.screenshot({ path: '/tmp/proofix-pinned-walkthrough.png' })
  }
})

test('each pinned stage animates forward and backward before the next appears', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'reduced-motion', 'Reduced motion intentionally shows completed scenes')
  for (const index of [1, 2, 3]) {
    const scene = page.locator(`#scene-${index} [data-progress]`).first()
    for (const progress of [0, 0.25, 0.5, 0.75, 1, 0.5, 0]) {
      await page.locator(`#moment-${index}`).evaluate((track, { progress, index }) => {
        const rect = track.getBoundingClientRect()
        const start = index === 1 ? 0.28 : 0.08
        scrollTo({ top: scrollY + rect.top - innerHeight * 0.38 + rect.height * (start + (0.88 - start) * progress), behavior: 'instant' })
      }, { progress, index })
      await expectMoment(page, index)
      const scale = index === 3 ? 1000 : 100
      await expect.poll(async () => Math.abs(Number(await scene.getAttribute('data-progress')) / scale - progress)).toBeLessThan(0.03)
    }
  }
})
