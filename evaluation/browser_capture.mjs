// Direct, local persistence of the browser tool's documented read methods.
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { createHash } from 'node:crypto';

export const CAPTURE_METHOD = 'cua.direct_dom_snapshot_and_rendered_text.v1';

export async function captureSource(tab, outputDir, context = {}) {
  if (!context || typeof context !== 'object' || Array.isArray(context)) {
    throw new TypeError('context must be a JSON object');
  }
  const savedContext = JSON.parse(JSON.stringify(context));
  const directory = resolve(outputDir);
  // No recursive creation: an existing target, including a previous failed capture,
  // is an error. The caller creates its parent directory first.
  await mkdir(directory);
  const startedAt = new Date().toISOString();
  const sourceUrl = await tab.url();
  const sourceTitle = await tab.title();
  const dom = await tab.playwright.domSnapshot();
  const domAt = new Date().toISOString();
  const rendered = await tab.playwright.evaluate(() => document.body.innerText);
  const renderedAt = new Date().toISOString();
  const sourceUrlAfter = await tab.url();
  if (sourceUrl !== sourceUrlAfter) {
    throw new Error('Source URL changed during capture; no capture.json was written');
  }
  if (typeof dom !== 'string' || !dom.trim() || typeof rendered !== 'string' || !rendered.trim()) {
    throw new Error('Browser returned empty or non-string raw evidence');
  }
  async function persist(path, content, capturedAt) {
    const data = Buffer.from(content, 'utf8');
    await writeFile(join(directory, path), data, { flag: 'wx' });
    return { path, bytes: data.length, sha256: createHash('sha256').update(data).digest('hex'), captured_at: capturedAt };
  }
  const files = {
    dom_snapshot: await persist('dom.txt', dom, domAt),
    rendered_text: await persist('rendered.txt', rendered, renderedAt),
  };
  const manifest = {
    schema_version: 1, capture_method: CAPTURE_METHOD,
    started_at: startedAt, completed_at: new Date().toISOString(),
    source_url: sourceUrl, source_url_after: sourceUrlAfter, source_title: sourceTitle,
    context: savedContext, files,
  };
  await writeFile(join(directory, 'capture.json'), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx' });
  return {
    capture_path: join(directory, 'capture.json'), source_url: sourceUrl,
    source_title: sourceTitle, captured_at: manifest.completed_at,
    raw_bytes: files.dom_snapshot.bytes + files.rendered_text.bytes,
  };
}
