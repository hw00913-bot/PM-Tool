// Project-local browser verification. The prototype itself has no Node dependency.
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { createRequire } = require('node:module');

const assertionOperations = new Set(['visible', 'hidden', 'text', 'value', 'disabled', 'attribute', 'css']);
const dependencyDirectory = path.join('tools', 'browser-verification');
const installDependencies = 'npm ci --prefix tools/browser-verification --ignore-scripts';

function lockedModuleEntry(dependencyRoot, packages, name) {
  const packageRoot = path.join(dependencyRoot, 'node_modules', name);
  const metadataPath = path.join(packageRoot, 'package.json');
  if (fs.realpathSync(metadataPath) !== metadataPath) {
    throw new Error(`${name} must be inside the tool dependency directory without symlinks.`);
  }
  const metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf8'));
  const version = packages?.[`node_modules/${name}`]?.version;
  if (typeof version !== 'string' || !version) {
    throw new Error(`Missing ${name} version in the browser verification lockfile.`);
  }
  if (metadata.name !== name) throw new Error(`Invalid package metadata for ${name}.`);
  if (metadata.version !== version) {
    throw new Error(`${name} version ${metadata.version} does not match the lockfile version ${version}.`);
  }
  const entry = require.resolve(packageRoot);
  const relative = path.relative(packageRoot, entry);
  if (path.isAbsolute(relative) || relative === '..' || relative.startsWith('..' + path.sep)
      || fs.realpathSync(entry) !== entry) {
    throw new Error(`${name} must be inside the tool dependency directory. Resolved to ${entry}.`);
  }
  return entry;
}

function loadPlaywright(override) {
  if (override) {
    return require(override.startsWith('./') || override.startsWith('../') ? path.resolve(override) : override);
  }
  const dependencyRoot = path.join(__dirname, '..', dependencyDirectory);
  try {
    const lockfile = JSON.parse(fs.readFileSync(path.join(dependencyRoot, 'package-lock.json'), 'utf8'));
    const playwright = lockedModuleEntry(dependencyRoot, lockfile.packages, 'playwright');
    const core = lockedModuleEntry(dependencyRoot, lockfile.packages, 'playwright-core');
    const resolvedCore = createRequire(playwright).resolve('playwright-core');
    if (resolvedCore !== core) {
      throw new Error(`playwright-core must be inside the tool dependency directory at ${core}. Resolved to ${resolvedCore}.`);
    }
    return require(playwright);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`${detail}\nRun ${installDependencies} from the 07-RTL root, or set RTL_PLAYWRIGHT_MODULE.`, { cause: error });
  }
}

function requireString(value, field) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(field + ' is required');
  return value;
}

async function step(page, entry, item) {
  const locator = item.selector ? page.locator(item.selector) : null;
  const operation = item.op;
  if (operation === 'open') {
    await page.goto(entry.href, { waitUntil: 'load' });
    return;
  }
  if (operation === 'reload') {
    await page.reload({ waitUntil: 'load' });
    return;
  }
  if (!locator) throw new Error(operation + ' requires a selector');
  switch (operation) {
    case 'click': await locator.click(); return;
    case 'fill': await locator.fill(String(item.value)); return;
    case 'select': await locator.selectOption(String(item.value)); return;
    case 'check': await locator.check(); return;
    case 'uncheck': await locator.uncheck(); return;
    case 'visible': await locator.waitFor({ state: 'visible' }); return;
    case 'hidden': await locator.waitFor({ state: 'hidden' }); return;
    case 'text':
      if (!(await locator.innerText()).includes(String(item.value))) throw new Error('Text differs');
      return;
    case 'value':
      if (await locator.inputValue() !== String(item.value)) throw new Error('Value differs');
      return;
    case 'disabled':
      if (!(await locator.isDisabled())) throw new Error('Control is not disabled');
      return;
    case 'css': {
      const property = requireString(item.property, 'property');
      const actual = await locator.evaluate((element, name) =>
        getComputedStyle(element).getPropertyValue(name), property);
      const expected = String(item.value);
      const pixels = /^-?\d+(?:\.\d+)?px$/;
      let matches = actual === expected;
      if (!matches && pixels.test(actual) && pixels.test(expected)) {
        matches = Math.abs(parseFloat(actual) - parseFloat(expected)) <= 0.1;
      }
      if (!matches) {
        throw new Error(item.selector + ' ' + property + ': expected ' + item.value + ', got ' + actual);
      }
      return;
    }
    case 'attribute':
      if (await locator.getAttribute(requireString(item.attribute, 'attribute')) !== String(item.value)) {
        throw new Error('Attribute differs');
      }
      return;
    default: throw new Error('Unknown browser operation: ' + operation);
  }
}

async function guardedPage(browser, root, viewport) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  page.setDefaultTimeout(5000);
  page.setDefaultNavigationTimeout(8000);
  const failures = [];
  const allowed = url => url.startsWith(root.href) || url.startsWith('data:') || url.startsWith('blob:');
  page.on('pageerror', error => failures.push(error.message));
  page.on('requestfailed', request => failures.push('Resource failed: ' + request.url()));
  page.on('request', request => {
    if (!allowed(request.url())) failures.push('Resource outside delivery directory: ' + request.url());
  });
  await ctx.route('**/*', route => allowed(route.request().url()) ? route.continue() : route.abort());
  return { ctx, page, failures };
}

function observeFeatureMarkers({ features, waitForAll }) {
  const observed = new Map();
  for (const element of document.querySelectorAll('[data-rtl-feature]')) {
    const box = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    if (!box.width || !box.height || style.visibility !== 'visible' || style.display === 'none') continue;
    const scene = element.closest('[data-rtl-scene]')?.getAttribute('data-rtl-scene') || '';
    for (const id of element.getAttribute('data-rtl-feature').trim().split(/\s+/)) {
      if (!id) continue;
      if (!observed.has(id)) observed.set(id, new Set());
      observed.get(id).add(scene);
    }
  }
  const results = Object.fromEntries(features.map(feature => {
    const scenes = observed.get(feature.id);
    if (scenes?.size === 1 && scenes.has(feature.scene_id)) {
      return [feature.id, {
        status: 'pass', errors: [],
        identity: { kind: 'feature-marker', feature_id: feature.id, scene_id: [...scenes][0] },
      }];
    }
    const found = scenes ? [...scenes].map(scene => scene || '(unmarked scene)').join(', ') : 'none';
    return [feature.id, {
      status: 'fail', identity: null,
      errors: [`Visible feature marker ${feature.id} expected in ${feature.scene_id}; observed scenes: ${found}`],
    }];
  }));
  if (waitForAll && Object.values(results).some(result => result.status !== 'pass')) return false;
  return results;
}

async function featureEntryChecks(page, features) {
  try {
    const observed = await page.waitForFunction(
      observeFeatureMarkers, { features, waitForAll: true }, { timeout: 5000 },
    );
    return observed.jsonValue();
  } catch (error) {
    if (error.name !== 'TimeoutError') throw error;
    return page.evaluate(observeFeatureMarkers, { features, waitForAll: false });
  }
}

async function routeCheck(browser, root, viewport, route, features = null) {
  const expected = new URL(route, root);
  const fragment = decodeURIComponent(expected.hash.slice(1));
  if (!expected.href.startsWith(root.href)) throw new Error('Route outside delivery directory: ' + route);
  const { ctx, page, failures } = await guardedPage(browser, root, viewport);
  let identity = null;
  let featureResults = null;
  try {
    await page.goto(expected.href, { waitUntil: 'load' });
    const observed = await page.waitForFunction(({ requested, fragment }) => {
      const target = new URL(requested);
      if (location.protocol !== target.protocol || location.host !== target.host ||
          location.pathname !== target.pathname || location.search !== target.search ||
          (fragment && decodeURIComponent(location.hash.slice(1)) !== fragment)) return false;
      const visible = element => {
        if (!element || !element.getClientRects().length) return false;
        const style = getComputedStyle(element);
        return style.visibility === 'visible' && style.display !== 'none';
      };
      const normalize = text => text.trim().replace(/\s+/g, ' ');
      if (!fragment) {
        const heading = document.querySelector('main h1, h1');
        const content = visible(heading) ? heading.innerText : document.body?.innerText;
        if (content?.trim()) return { kind: 'document', value: normalize(content).slice(0, 120) };
        return false;
      }
      const marker = [...document.querySelectorAll('[data-rtl-route]')].find(element => {
        const value = element.getAttribute('data-rtl-route');
        return visible(element) && (value === fragment || value === '#' + fragment);
      });
      if (marker) return { kind: 'route-marker', value: marker.getAttribute('data-rtl-route') };
      if (!fragment.startsWith('/') && visible(document.getElementById(fragment))) {
        return { kind: 'document-anchor', value: fragment };
      }
      const headings = [...document.querySelectorAll('main h1, main [role="heading"], [role="main"] h1')]
        .filter(visible).map(element => normalize(element.innerText)).filter(Boolean);
      for (const link of document.querySelectorAll('nav a[aria-current="page"]')) {
        if (!visible(link) || link.href !== requested) continue;
        const label = normalize(link.innerText);
        const heading = label && headings.find(value => value.includes(label));
        if (heading) return { kind: 'navigation-heading', value: heading, href: link.href, label };
      }
      return false;
    }, { requested: expected.href, fragment }, { timeout: 5000 });
    identity = await observed.jsonValue();
    if (features !== null) featureResults = await featureEntryChecks(page, features);
  } catch (error) {
    failures.push('Route identity was not confirmed for ' + route + ': ' + error.message);
  }
  const url = page.url();
  await ctx.close();
  const result = { status: failures.length ? 'fail' : 'pass', url, identity, errors: failures };
  if (featureResults !== null) {
    result.features = Object.fromEntries(Object.entries(featureResults).map(
      ([id, proof]) => [id, { ...proof, entry: route, url }],
    ));
  }
  return result;
}

async function main() {
  const [entryPath, suitePath, reportPath, routePlanPath] = process.argv.slice(2);
  if (!entryPath || !suitePath || !reportPath || process.argv.length > 6) {
    throw new Error('Usage: node scripts/browser-check.cjs INDEX_HTML SUITE_JSON REPORT_JSON [ROUTES_JSON]');
  }
  const source = fs.realpathSync(entryPath);
  if (!fs.statSync(source).isFile()) throw new Error('Entry must be a file');
  const entry = pathToFileURL(source);
  const root = pathToFileURL(path.dirname(source) + path.sep);
  const suite = JSON.parse(fs.readFileSync(suitePath, 'utf8'));
  const fixture = requireString(suite.fixture_revision, 'fixture_revision');
  const suiteRevision = requireString(suite.suite_revision, 'suite_revision');
  if (!Array.isArray(suite.cases) || !suite.cases.length) throw new Error('No cases supplied');
  const ids = suite.cases.map(item => requireString(item.id, 'case ID'));
  if (new Set(ids).size !== ids.length) throw new Error('Duplicate case ID');
  for (const item of suite.cases) {
    if (!Array.isArray(item.steps) || !item.steps.length) throw new Error('Case has no steps: ' + item.id);
    if (!item.steps.some(action => assertionOperations.has(action.op))) {
      throw new Error('Case has no observable assertion: ' + item.id);
    }
  }
  const engine = suite.engine || 'chromium';
  if (!['chromium', 'firefox', 'webkit'].includes(engine)) throw new Error('Unsupported engine: ' + engine);
  const viewport = suite.viewport || { width: 1440, height: 900 };
  const routePlan = routePlanPath ? JSON.parse(fs.readFileSync(routePlanPath, 'utf8')) : [];
  const routes = Array.isArray(routePlan) ? routePlan.map(route => [route, null]) : Object.entries(routePlan);
  const rootFeaturePlans = new Map();
  for (const [route, features] of routes) {
    const url = new URL(route, root);
    if (features !== null && url.hash === '#/') {
      url.hash = '';
      rootFeaturePlans.set(url.href, features);
    }
  }
  const override = process.env.RTL_PLAYWRIGHT_MODULE;
  const playwright = loadPlaywright(override);
  const options = { headless: true };
  if (process.env.RTL_BROWSER_EXECUTABLE) options.executablePath = process.env.RTL_BROWSER_EXECUTABLE;
  let browser;
  try {
    browser = await playwright[engine].launch(options);
  } catch (error) {
    if (!override && !options.executablePath && error instanceof Error && error.message.includes("Executable doesn't exist at")) {
      const detail = error.message.split('\n', 1)[0];
      throw new Error(`${detail}\nRun ./tools/browser-verification/node_modules/.bin/playwright install ${engine} from the 07-RTL root, or set RTL_BROWSER_EXECUTABLE to a local browser executable.`, { cause: error });
    }
    throw error;
  }
  const report = {
    protocol: 'file:', path: source, entry: entry.href, fixture_revision: fixture,
    suite_revision: suiteRevision, browser: engine + ' ' + browser.version(), cases: {},
  };
  try {
    for (const item of suite.cases) {
      const { ctx, page, failures } = await guardedPage(browser, root, viewport);
      const executed = [];
      const stepLocations = [];
      try {
        await page.goto(entry.href, { waitUntil: 'load' });
        for (const action of item.steps) {
          const before = page.url();
          await step(page, entry, action);
          executed.push(action.op + (action.selector ? ' ' + action.selector : ''));
          const after = page.url();
          const location = { before, after };
          if (assertionOperations.has(action.op) && before === after && rootFeaturePlans.has(after)) {
            const features = rootFeaturePlans.get(after);
            const observed = await page.evaluate(observeFeatureMarkers, { features, waitForAll: false });
            location.root_features = Object.keys(observed).filter(id => observed[id].status === 'pass');
          }
          stepLocations.push(location);
        }
        if (!page.url().startsWith(root.href)) throw new Error('Page navigated outside delivery directory');
      } catch (error) {
        failures.push(error.message);
      } finally {
        await ctx.close();
      }
      Object.defineProperty(report.cases, item.id, {
        value: { status: failures.length ? 'fail' : 'pass', executed, errors: failures,
          step_locations: stepLocations },
        enumerable: true,
      });
    }
    if (routePlanPath) {
      report.route_checks = Object.create(null);
      if (routes.length === 0) {
        report.entry_check = await routeCheck(browser, root, viewport, path.basename(source));
      }
      for (const [route, features] of routes) {
        report.route_checks[route] = await routeCheck(browser, root, viewport, route, features);
      }
    }
  } finally {
    await browser.close();
  }
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
  const routeResults = Object.values(report.route_checks || {});
  const results = [
    ...Object.values(report.cases), ...routeResults, ...routeResults.flatMap(route => Object.values(route.features || {})),
  ];
  if (report.entry_check) results.push(report.entry_check);
  if (results.some(item => item.status !== 'pass')) process.exitCode = 1;
}

main().catch(error => {
  console.error(error.message);
  process.exitCode = 2;
});
