import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { JSDOM } from 'jsdom';

const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const script = readFileSync(new URL('../public/startup.js', import.meta.url), 'utf8');
function fixture() {
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'https://www.158722.xyz/dashboard' });
  let timeout;
  dom.window.setTimeout = callback => { timeout = callback; return 1; };
  dom.window.clearTimeout = () => { timeout = undefined; };
  dom.window.eval(script);
  return { dom, window: dom.window, timeout: () => timeout?.(),
    panel: dom.window.document.getElementById('app-startup') };
}

test('normal startup does not display a loading notice', () => {
  const dom = new JSDOM(html, { url: 'https://www.158722.xyz/dashboard' });
  const panel = dom.window.document.getElementById('app-startup');
  assert.equal(panel.hidden, true);
  assert.equal(panel.querySelector('a').href, 'https://www.158722.xyz/dashboard');
  assert.doesNotMatch(html, /正在加载数据平台|首次访问需要下载/);
  dom.window.close();
});

test('a missing entry or a rejected bootstrap shows an error with retry', () => {
  for (const type of ['resource', 'hc:startup-error', 'vite:preloadError']) {
    const { window, panel, dom } = fixture();
    if (type === 'resource') {
      window.document.querySelector('script[type=module]').dispatchEvent(new window.Event('error'));
    } else {
      window.dispatchEvent(new window.Event(type));
    }
    assert.equal(panel.getAttribute('role'), 'alert');
    assert.equal(panel.hidden, false);
    assert.match(panel.textContent, /页面加载失败/);
    assert.ok(panel.querySelector('a'));
    dom.window.close();
  }
});

test('waiting alone does not display an error, and React owns errors after rendering', async () => {
  const { window, panel, timeout, dom } = fixture();
  timeout();
  assert.equal(panel.hidden, true);
  window.document.getElementById('root').innerHTML = '<main>工作台</main>';
  await Promise.resolve();
  assert.equal(panel.hidden, true);
  window.dispatchEvent(new window.Event('hc:startup-error'));
  assert.equal(panel.hidden, true);
  dom.window.close();
});
