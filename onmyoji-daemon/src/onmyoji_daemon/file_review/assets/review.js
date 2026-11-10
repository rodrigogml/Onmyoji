/* A revisão é composta em memória e só é aplicada após a conferência final. */
(function () {
  "use strict";

  function lineText(value) {
    return String(value).replace(/\r?\n$/, "");
  }

  function inlineSegments(before, after) {
    const a = Array.from(before);
    const b = Array.from(after);
    let start = 0;
    let end = 0;
    while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
    while (end < a.length - start && end < b.length - start && a[a.length - end - 1] === b[b.length - end - 1]) end += 1;
    const parts = chars => [chars.slice(0, start).join(""), chars.slice(start, chars.length - end).join(""), end ? chars.slice(-end).join("") : ""];
    return { before: parts(a), after: parts(b) };
  }

  function pendingChunks(review) {
    return (review?.chunks || []).filter(chunk => chunk.decision === "pending");
  }

  function canApply(review, preview, dirty) {
    return Boolean(review && review.status === "editing" && preview && !dirty &&
      pendingChunks(review).length === 0 && preview.revision === review.revision &&
      preview.result_hash === review.result_hash && preview.result === review.result);
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { lineText, inlineSegments, pendingChunks, canApply };
  }
  if (typeof document === "undefined") return;
  const root = document.getElementById("file-review");
  if (!root) return;

  const byId = id => document.getElementById(id);
  const elements = Object.fromEntries([
    "filename", "review-status", "cancel", "error", "error-message", "reload", "receipt",
    "review-content", "progress", "accept-all", "changes", "original-full", "proposed-full",
    "draft-status", "download", "result", "save", "preview", "preview-section", "preview-changes",
    "proposal-fate", "discard-label", "discard", "apply-help", "apply"
  ].map(id => [id, byId(id)]));
  const state = { review: null, preview: null, dirty: false, busy: false, finished: false };

  function node(tag, text, className) {
    const value = document.createElement(tag);
    if (text !== undefined) value.textContent = text;
    if (className) value.className = className;
    return value;
  }

  function invalidatePreview() {
    state.preview = null;
    elements["preview-section"].hidden = true;
  }

  function updateControls() {
    const editable = state.review?.status === "editing" && !state.finished;
    const available = editable && !state.busy;
    const pending = pendingChunks(state.review).length;
    elements.result.readOnly = state.busy || !editable;
    elements.cancel.disabled = state.busy || state.finished || !["editing", "conflict"].includes(state.review?.status);
    elements.save.disabled = !available || !state.dirty;
    elements.preview.disabled = !available;
    elements["accept-all"].disabled = !available || pending === 0;
    elements.download.disabled = !state.review;
    elements.apply.disabled = state.busy || state.finished || !canApply(state.review, state.preview, state.dirty);
    elements.discard.disabled = !available;
    root.querySelectorAll("[data-decision]").forEach(button => {
      button.disabled = !available || button.dataset.resolved === "true";
    });
    if (state.review) {
      elements.progress.textContent = pending ? `${pending} bloco(s) pendente(s) de decisão.` : "Todos os blocos têm uma decisão.";
      elements["draft-status"].textContent = state.dirty ? "Há edições ainda não salvas no rascunho." : "Rascunho salvo.";
    }
    if (state.preview) {
      elements["apply-help"].textContent = pending ? "Decida os blocos pendentes antes de aplicar o resultado." : "Ao aplicar, você autoriza a gravação deste resultado no arquivo original.";
    }
  }

  async function request(endpoint, body) {
    const options = { credentials: "same-origin", cache: "no-store", headers: { "Accept": "application/json" } };
    if (body !== undefined) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.headers["X-Onmyoji-Review-CSRF"] = state.review?.csrf_token || "";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(`api/${endpoint}`, options);
    let data;
    try { data = await response.json(); } catch (_) { data = null; }
    if (!response.ok) {
      const message = data?.error?.message || (response.status === 401 || response.status === 403 || response.status === 410 || response.status === 503
        ? "O acesso à revisão expirou ou foi encerrado. Baixe o resultado local e solicite outro link ao agente."
        : "Não foi possível concluir a operação. O texto do resultado foi preservado nesta tela.");
      const error = new Error(message);
      error.status = response.status;
      error.code = data?.error?.code;
      throw error;
    }
    if (!data || typeof data !== "object") throw new Error("O servidor retornou uma resposta inválida. O resultado local foi preservado.");
    return data;
  }

  async function run(action) {
    if (state.busy || state.finished) return;
    state.busy = true;
    elements.error.hidden = true;
    updateControls();
    try { await action(); }
    catch (error) {
      invalidatePreview();
      elements["error-message"].textContent = error.message;
      elements.error.hidden = false;
      elements.reload.hidden = error.status === 401 || error.status === 403 || error.status === 410 || error.status === 503;
    }
    finally { state.busy = false; updateControls(); }
  }

  function line(value, number, kind, parts) {
    const row = node("div", undefined, `line ${kind}`);
    row.append(node("span", String(number), "line-number"), node("span", kind === "removed" ? "−" : "+", "line-sign"));
    const text = node("span", undefined, "line-text");
    if (parts) {
      text.append(document.createTextNode(parts[0]), node("mark", parts[1]), document.createTextNode(parts[2]));
    } else { text.textContent = value || " "; }
    row.append(text);
    return row;
  }

  function diffPane(title, values, other, start, kind) {
    const section = node("section");
    section.append(node("h3", title));
    if (values.length === 0) section.append(node("p", kind === "removed" ? "Nenhuma linha removida." : "Nenhuma linha adicionada.", "empty"));
    values.forEach((value, i) => {
      const text = lineText(value);
      const comparison = other[i] === undefined ? null : lineText(other[i]);
      const parts = comparison === null ? null : kind === "removed" ? inlineSegments(text, comparison).before : inlineSegments(comparison, text).after;
      section.append(line(text, Number(start) + i, kind, parts));
    });
    if (values.length && !String(values[values.length - 1]).endsWith("\n")) {
      section.append(node("p", "Sem quebra de linha ao final do arquivo.", "empty"));
    }
    return section;
  }

  function renderChunks(container, chunks, interactive) {
    container.replaceChildren();
    if (chunks.length === 0) {
      container.append(node("p", "Não há diferenças entre os arquivos.", "notice"));
      return;
    }
    chunks.forEach((chunk, i) => {
      const article = node("article", undefined, "chunk");
      const header = node("div", undefined, "chunk-header");
      const names = { insert: "Inclusão", delete: "Exclusão", replace: "Substituição" };
      header.append(node("h3", `Bloco ${i + 1} · ${names[chunk.tag] || "Alteração"}`));
      if (interactive) header.append(node("span", ({ pending: "Pendente", accepted: "Incorporado ao resultado", rejected: "Sugestão ignorada" })[chunk.decision] || "Pendente", "decision"));
      const columns = node("div", undefined, "diff-columns");
      columns.append(diffPane("Original · − remoções", chunk.before, chunk.after, chunk.original_start, "removed"));
      columns.append(diffPane(interactive ? "Proposta · + inclusões" : "Resultado · + inclusões", chunk.after, chunk.before, chunk.proposed_start, "added"));
      article.append(header, columns);
      if (interactive) {
        const actions = node("div", undefined, "chunk-actions");
        const accept = node("button", chunk.tag === "delete" ? "Aplicar exclusão ao rascunho" : "Usar alteração no rascunho");
        const reject = node("button", "Ignorar sugestão");
        [accept, reject].forEach(button => {
          button.type = "button";
          button.dataset.decision = "true";
          button.dataset.resolved = String(chunk.decision !== "pending");
        });
        accept.addEventListener("click", () => run(() => decide(chunk.id, "accepted")));
        reject.addEventListener("click", () => run(() => decide(chunk.id, "rejected")));
        actions.append(accept, reject);
        article.append(actions);
      }
      container.append(article);
    });
  }

  function useReview(review) {
    state.review = review;
    state.dirty = false;
    elements.result.value = review.result;
    elements.filename.textContent = review.filename;
    document.title = `${review.filename} · Revisão Onmyōji`;
    elements["original-full"].textContent = review.original;
    elements["proposed-full"].textContent = review.proposed;
    elements["review-content"].hidden = false;
    elements["review-status"].textContent = review.status === "editing"
      ? "Original preservado. Monte e confira o resultado antes de aplicar."
      : review.status === "conflict" ? "Os arquivos mudaram fora desta revisão. Baixe o rascunho e solicite uma nova revisão."
        : "Esta revisão já foi encerrada.";
    state.finished = ["applied", "cancelled", "expired"].includes(review.status);
    renderChunks(elements.changes, review.chunks, true);
    invalidatePreview();
    updateControls();
  }

  async function saveDraft() {
    if (!state.dirty) return;
    const saved = await request("draft", { revision: state.review.revision, result: elements.result.value });
    useReview(saved);
  }

  async function decide(chunkId, decision) {
    await saveDraft();
    const review = await request("decision", { revision: state.review.revision, chunk_id: chunkId, decision });
    useReview(review);
  }

  function showReceipt(result, cancelled) {
    state.finished = true;
    state.review.status = cancelled ? "cancelled" : "applied";
    invalidatePreview();
    const receipt = result.receipt || result;
    const notice = elements.receipt;
    notice.replaceChildren(node("p", cancelled ? "Revisão cancelada. O arquivo original não foi alterado." : "Resultado aplicado ao arquivo original."));
    if (!cancelled) {
      const removed = receipt.proposed_removed === true || receipt.proposal_removed === true;
      notice.append(node("p", removed ? "O arquivo .proposed foi removido." : "O arquivo .proposed foi preservado para as sugestões restantes."));
      const warning = receipt.cleanup_warning || receipt.warning;
      if (warning) notice.append(node("p", typeof warning === "string" ? warning : "A aplicação foi concluída, mas a limpeza da proposta precisa ser verificada pelo agente."));
    }
    notice.hidden = false;
    elements["review-status"].textContent = "Revisão encerrada. O acesso remoto foi finalizado.";
    updateControls();
  }

  elements.result.addEventListener("input", () => {
    state.dirty = elements.result.value !== state.review?.result;
    invalidatePreview();
    updateControls();
  });
  elements.save.addEventListener("click", () => run(saveDraft));
  elements["accept-all"].addEventListener("click", () => run(async () => {
    await saveDraft();
    const ids = pendingChunks(state.review).map(chunk => chunk.id);
    for (const id of ids) await decide(id, "accepted");
  }));
  elements.preview.addEventListener("click", () => run(async () => {
    await saveDraft();
    const preview = await request("preview");
    if (preview.revision !== state.review.revision || preview.result_hash !== state.review.result_hash || preview.result !== state.review.result) {
      const error = new Error("O rascunho mudou em outra aba. Baixe o texto local se necessário e carregue a revisão salva.");
      error.status = 409;
      throw error;
    }
    state.preview = preview;
    renderChunks(elements["preview-changes"], preview.chunks, false);
    const full = preview.result === state.review.proposed && state.review.chunks.every(chunk => chunk.decision === "accepted");
    elements["proposal-fate"].textContent = full
      ? "O resultado corresponde à proposta integral. Ao aplicar, o arquivo .proposed será removido."
      : "O resultado é parcial ou contém edições próprias. O arquivo .proposed será preservado.";
    elements["discard-label"].hidden = full;
    elements.discard.checked = false;
    elements["preview-section"].hidden = false;
    elements["preview-section"].scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  elements.apply.addEventListener("click", () => run(async () => {
    if (!canApply(state.review, state.preview, state.dirty)) return;
    const result = await request("apply", { revision: state.preview.revision, result_hash: state.preview.result_hash, discard_remaining: elements.discard.checked });
    showReceipt(result, false);
    elements.receipt.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  elements.cancel.addEventListener("click", () => {
    if (!window.confirm("Cancelar esta revisão? O original permanecerá intacto. Baixe antes o resultado se quiser guardar edições locais não salvas.")) return;
    run(async () => showReceipt(await request("cancel", {}), true));
  });
  elements.reload.addEventListener("click", () => {
    if (state.dirty && !window.confirm("Carregar o rascunho salvo substituirá as edições locais desta tela. Baixe o resultado antes se quiser preservá-las. Continuar?")) return;
    run(async () => useReview(await request("review")));
  });
  elements.download.addEventListener("click", () => {
    const blob = new Blob([elements.result.value], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = node("a");
    link.href = url;
    link.download = `${state.review.filename.replace(/[\\/<>:"|?*]/g, "_")}.resultado.txt`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  window.addEventListener("beforeunload", event => {
    if (!state.dirty || state.finished) return;
    event.preventDefault();
    event.returnValue = "";
  });

  run(async () => useReview(await request("review")));
}());
