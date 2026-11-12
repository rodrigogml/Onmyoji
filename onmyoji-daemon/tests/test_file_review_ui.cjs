"use strict";

const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const assets = path.join(__dirname, "../src/onmyoji_daemon/file_review/assets");
const source = fs.readFileSync(path.join(assets, "review.js"), "utf8");
const html = fs.readFileSync(path.join(assets, "index.html"), "utf8");
const { lineText, inlineSegments, pendingChunks, canApply } = require(path.join(assets, "review.js"));

class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.listeners = {};
    this.value = "";
    this.disabled = false;
    this.hidden = false;
    this.checked = false;
    this.textContent = "";
  }
  set innerHTML(_) { throw new Error("Document content must never be interpreted as HTML"); }
  append(...values) { this.children.push(...values); }
  replaceChildren(...values) { this.children = values; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll(selector) {
    const descendants = this.children.flatMap(child => [child, ...(child.children ? child.querySelectorAll(selector) : [])]);
    return selector === "[data-decision]" ? descendants.filter(child => child.dataset?.decision) : descendants;
  }
  scrollIntoView() {}
  remove() {}
  async click() { if (!this.disabled) await this.listeners.click?.(); }
}

const digest = text => createHash("sha256").update(text).digest("hex");
const settle = () => new Promise(resolve => setImmediate(resolve));

async function application() {
  const ids = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
  ids["file-review"].append(...Object.values(ids).filter(value => value !== ids["file-review"]));
  const document = {
    getElementById: id => ids[id],
    createElement: tag => new Element(tag),
    createTextNode: text => ({ textContent: text }),
    body: new Element("body")
  };
  let review = {
    review_id: "test-review", status: "editing", filename: "<img src=x onerror=alert(1)>.md", proposed_filename: "test.proposed.md",
    original: "Primeiro texto.\n\nRemover isto.\n", proposed: "Texto proposto.\n\n", result: "Primeiro texto.\n\nRemover isto.\n",
    chunks: [
      { id: "chunk-0", tag: "replace", original_start: 1, proposed_start: 1, before: ["Primeiro texto.\n"], after: ["Texto proposto.\n"], decision: "pending" },
      { id: "chunk-1", tag: "delete", original_start: 3, proposed_start: 3, before: ["Remover isto.\n"], after: [], decision: "pending" }
    ], revision: 1, csrf_token: "test-csrf"
  };
  review.result_hash = digest(review.result);
  const calls = [];
  let failure = null;
  const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => structuredClone(data) });
  const fetch = async (url, options) => {
    const endpoint = url.replace("api/", "");
    const body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({ endpoint, body, options });
    if (failure?.endpoint === endpoint) return response({ error: { code: failure.code, message: failure.message } }, failure.status);
    if (body) assert.equal(options.headers["X-Onmyoji-Review-CSRF"], review.csrf_token);
    if (endpoint === "review") return response(review);
    if (endpoint === "preview") return response({ ...review, chunks: review.chunks.filter(chunk => chunk.decision === "accepted") });
    if (endpoint === "draft") {
      assert.equal(body.revision, review.revision);
      review.result = body.result;
      review.result_hash = digest(review.result);
      review.revision += 1;
      return response(review);
    }
    if (endpoint === "decision") {
      assert.equal(body.revision, review.revision);
      const chunk = review.chunks.find(item => item.id === body.chunk_id);
      if (body.decision === "accepted") review.result = review.result.replace(chunk.before.join(""), chunk.after.join(""));
      chunk.decision = body.decision;
      review.revision += 1;
      review.result_hash = digest(review.result);
      return response(review);
    }
    if (endpoint === "apply") {
      assert.equal(body.revision, review.revision);
      assert.equal(body.result_hash, review.result_hash);
      review.status = "applied";
      return response({ status: "applied", proposed_removed: review.result === review.proposed || body.discard_remaining });
    }
    if (endpoint === "cancel") return response({ status: "cancelled" });
    throw Error(`Unexpected endpoint: ${endpoint}`);
  };
  const window = { confirm: () => true, addEventListener() {} };
  vm.runInNewContext(source, { document, window, fetch, module: { exports: {} }, Blob, URL, setTimeout, console });
  await settle();
  const buttons = () => ids.changes.querySelectorAll("[data-decision]");
  return { ids, calls, buttons, get review() { return review; }, fail(value) { failure = value; } };
}

test("diff helpers retain Unicode and mark only changed text", () => {
  assert.equal(lineText("texto\r\n"), "texto");
  assert.equal(lineText("texto"), "texto");
  assert.deepEqual(inlineSegments("A 🐈 azul", "A 🐕 azul"), { before: ["A ", "🐈", " azul"], after: ["A ", "🐕", " azul"] });
  assert.deepEqual(inlineSegments("", "a"), { before: ["", "", ""], after: ["", "a", ""] });
  assert.deepEqual(inlineSegments("igual", "igual"), { before: ["igual", "", ""], after: ["igual", "", ""] });
  assert.deepEqual(pendingChunks(null), []);
  assert.equal(canApply(null, null, false), false);
});

test("manual draft is saved before a block decision and partial apply keeps the proposal", async () => {
  const app = await application();
  assert.equal(app.ids.filename.textContent, "<img src=x onerror=alert(1)>.md");
  app.ids.result.value += "Edição manual.\n";
  app.ids.result.listeners.input();
  await app.buttons()[0].click();
  assert.deepEqual(app.calls.slice(1).map(call => call.endpoint), ["draft", "decision"]);
  assert.ok(app.ids.result.value.includes("Edição manual."));
  await app.buttons()[3].click();
  await app.ids.preview.click();
  assert.equal(app.ids.apply.disabled, false);
  assert.equal(app.ids["discard-label"].hidden, false);
  await app.ids.apply.click();
  assert.equal(app.ids.result.readOnly, true);
  assert.ok(app.ids.receipt.children.some(child => child.textContent.includes("preservado")));
  assert.equal(app.calls.at(-1).endpoint, "apply");
  assert.equal(app.calls.at(-1).body.discard_remaining, false);
});

test("editing after the preview invalidates approval until another preview", async () => {
  const app = await application();
  await app.ids["accept-all"].click();
  await app.ids.preview.click();
  assert.equal(app.ids.apply.disabled, false);
  assert.equal(app.ids["discard-label"].hidden, true);
  app.ids.result.value += "Nova edição.\n";
  app.ids.result.listeners.input();
  assert.equal(app.ids.apply.disabled, true);
  assert.equal(app.ids["preview-section"].hidden, true);
  await app.ids.apply.click();
  assert.equal(app.calls.some(call => call.endpoint === "apply"), false);
  await app.ids.preview.click();
  assert.equal(app.ids.apply.disabled, false);
  assert.deepEqual(app.calls.slice(-2).map(call => call.endpoint), ["draft", "preview"]);
});

test("a revision conflict preserves local edits and keeps the download available", async () => {
  const app = await application();
  const local = app.ids.result.value + "Não perder este texto.\n";
  app.ids.result.value = local;
  app.ids.result.listeners.input();
  app.fail({ endpoint: "draft", status: 409, code: "revision_conflict", message: "Outra aba alterou a revisão." });
  await app.ids.save.click();
  assert.equal(app.ids.result.value, local);
  assert.equal(app.ids.error.hidden, false);
  assert.equal(app.ids["error-message"].textContent, "Outra aba alterou a revisão.");
  assert.equal(app.ids.download.disabled, false);
  assert.equal(app.ids.apply.disabled, true);
});

test("expired access does not discard the result and cancel makes no further request", async () => {
  const app = await application();
  app.ids.result.value += "Local.\n";
  app.ids.result.listeners.input();
  app.fail({ endpoint: "draft", status: 503, code: "expired", message: "A revisão expirou." });
  await app.ids.save.click();
  assert.equal(app.ids.reload.hidden, true);
  assert.ok(app.ids.result.value.endsWith("Local.\n"));
  app.fail(null);
  await app.ids.cancel.click();
  await settle();
  assert.equal(app.calls.at(-1).endpoint, "cancel");
  assert.equal(app.ids.cancel.disabled, true);
  assert.equal(app.ids.receipt.hidden, false);
});

test("text equal to the proposal with ignored suggestions is still a partial approval", async () => {
  const app = await application();
  app.ids.result.value = app.review.proposed;
  app.ids.result.listeners.input();
  await app.buttons()[1].click();
  await app.buttons()[3].click();
  await app.ids.preview.click();
  assert.equal(app.ids["discard-label"].hidden, false);
  assert.ok(app.ids["proposal-fate"].textContent.includes("preservado"));
});

test("a file conflict keeps cancel available and makes the editor read only", async () => {
  const app = await application();
  app.review.status = "conflict";
  await app.ids.reload.click();
  await settle();
  assert.equal(app.ids.result.readOnly, true);
  assert.equal(app.ids.save.disabled, true);
  assert.equal(app.ids.download.disabled, false);
  assert.equal(app.ids.cancel.disabled, false);
});
