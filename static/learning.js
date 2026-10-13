"use strict";

// Notes are untrusted text. Build formatting with DOM nodes, never innerHTML.
function inlineNote(node, text) {
  for (const part of text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
    if (part.startsWith("**") && part.endsWith("**")) node.append(element("strong", "", part.slice(2, -2)));
    else if (part.startsWith("`") && part.endsWith("`")) node.append(element("code", "", part.slice(1, -1)));
    else node.append(document.createTextNode(part));
  }
}

function renderNote(target, markdown) {
  target.replaceChildren();
  const text = markdown.replace(/^\uFEFF?---\r?\n[\s\S]*?\r?\n---\s*\r?\n/, "").replace(/<!--[\s\S]*?-->/g, "");
  const lines = text.split(/\r?\n/);
  let destination = target, table = null, list = null, quote = null, code = null;
  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (code) code = null;
      else { code = element("pre"); destination.append(code); }
      continue;
    }
    if (code) { code.textContent += line + "\n"; continue; }
    const heading = line.match(/^#{1,6}\s+(.+)/);
    if (heading) {
      table = list = quote = null;
      if (/Original transcript|Spoken Dialogue|Session transcript|Полный диалог/i.test(heading[1])) {
        const details = element("details", "note-transcript");
        details.append(element("summary", "", "Полный диалог"));
        destination = element("div"); details.append(destination); target.append(details);
      } else {
        destination = target;
        const node = element("h3"); inlineNote(node, heading[1]); destination.append(node);
      }
      continue;
    }
    if (line.trim().startsWith("|")) {
      list = quote = null;
      const cells = line.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map(x => x.trim().replace(/\\\|/g, "|"));
      if (cells.every(x => /^[-: ]+$/.test(x))) continue;
      const first = !table;
      if (first) { const wrap = element("div", "note-table"); table = element("table"); wrap.append(table); destination.append(wrap); }
      const row = element("tr");
      for (const value of cells) { const cell = element(first ? "th" : "td"); if (first) cell.scope = "col"; inlineNote(cell, value); row.append(cell); }
      table.append(row); continue;
    }
    table = null;
    if (/^>/.test(line)) {
      list = null;
      if (!quote) { quote = element("blockquote"); destination.append(quote); }
      const value = line.replace(/^>\s*/, "").replace(/^\[![^\]]+\]\s*/, "");
      const paragraph = element("p"); inlineNote(paragraph, value); quote.append(paragraph); continue;
    }
    quote = null;
    if (/^\s*[-*]\s/.test(line)) {
      if (!list) { list = element("ul"); destination.append(list); }
      const item = element("li"); inlineNote(item, line.replace(/^\s*[-*]\s/, "")); list.append(item); continue;
    }
    list = null;
    if (!line.trim() || /^---+$/.test(line.trim())) continue;
    const paragraph = element("p"); inlineNote(paragraph, line); destination.append(paragraph);
  }
}

const learning = { sessions: [], selected: null, note: null, loadVersion: 0, noteVersion: 0 };
function lessonDate(value) {
  const date = new Date(value + "T12:00:00");
  return Number.isNaN(date.getTime()) ? "Дата не указана" : new Intl.DateTimeFormat("ru", { day: "numeric", month: "long", year: "numeric" }).format(date);
}
function sessionLabel(session) { return lessonDate(session.date) + (session.time ? `, ${session.time}` : ""); }
function cleanExcerpt(text) { return text.replace(/\*\*/g, "").replace(/\s+/g, " ").trim(); }

function renderLessonList() {
  const query = $("lessonSearch").value.trim().toLocaleLowerCase();
  const sessions = learning.sessions.filter(s => [s.date, s.summary, ...s.vocab, ...s.errors].join(" ").toLocaleLowerCase().includes(query));
  $("lessonList").replaceChildren();
  for (const session of sessions) {
    const button = element("button", "lesson-row");
    button.type = "button";
    button.setAttribute("aria-pressed", String(session.id === learning.selected));
    button.append(element("span", "lesson-row-date", sessionLabel(session)));
    button.append(element("span", "lesson-row-excerpt", cleanExcerpt(session.summary) || "Разговор с Sarah. Открой запись, чтобы прочитать заметку."));
    button.append(element("span", "lesson-row-meta", (session.corrections.length ? `Подсказок: ${session.corrections.length}` : "Заметка занятия") + (session.legacy ? " · Ранний архив" : "")));
    button.addEventListener("click", () => openLesson(session));
    $("lessonList").append(button);
  }
  if (!sessions.length) $("lessonList").append(element("p", "library-empty", query ? "Ничего не найдено. Попробуй другую фразу или дату." : "После первого разговора нажми «Завершить». Запись появится здесь."));
}

async function openLesson(session) {
  $("deletePrompt").hidden = true;
  $("deleteLesson").disabled = true;
  const version = ++learning.noteVersion;
  learning.selected = session.id; learning.note = null;
  $("downloadLesson").disabled = true;
  $("lessonDate").textContent = sessionLabel(session);
  $("lessonContent").replaceChildren(element("p", "reader-empty", "Открываю заметку…"));
  $("lessonsView").classList.add("reading"); renderLessonList();
  try {
    const response = await fetch(`/api/lessons/${encodeURIComponent(session.id)}`);
    if (!response.ok) throw new Error();
    const note = await response.json();
    if (version !== learning.noteVersion) return;
    learning.note = note;
    renderNote($("lessonContent"), note.markdown);
    $("downloadLesson").disabled = false;
    $("deleteLesson").disabled = false;
    $("lessonContent").scrollTop = 0;
    $("lessonContent").focus({ preventScroll: true });
  } catch {
    if (version !== learning.noteVersion) return;
    const retry = element("button", "button outline", "Попробовать ещё раз");
    retry.addEventListener("click", () => openLesson(session));
    $("lessonContent").replaceChildren(element("p", "library-empty", "Не удалось открыть запись. Возможно, файл перемещён. Обнови список или попробуй снова."), retry);
  }
}

function renderReview() {
  $("reviewCards").replaceChildren();
  let count = 0;
  for (const session of learning.sessions) {
    for (const correction of session.corrections) {
      count++;
      const card = element("article", "review-card");
      const source = element("button", "text-button review-source", sessionLabel(session));
      source.addEventListener("click", () => { showLearningView(false); openLesson(session); });
      if (session.legacy) card.append(element("p", "legacy-note", "Рекомендация из раннего архива"));
      const original = element("p", "review-original"); inlineNote(original, correction.original);
      const details = element("details", "review-answer");
      details.append(element("summary", "", "Показать исправление"));
      const answer = element("p", "review-corrected"); inlineNote(answer, correction.correction);
      const rule = element("p", "review-rule"); inlineNote(rule, correction.rule || "");
      details.append(answer, rule); card.append(source, original, details); $("reviewCards").append(card);
    }
  }
  if (!count) $("reviewCards").append(element("p", "library-empty", "Пока нет сохранённых исправлений. Они появятся после занятия, если Sarah заметит ошибки."));
}

function showLearningView(review) {
  $("trashView").hidden = true;
  $("trashViewBtn").setAttribute("aria-pressed", "false");
  $("lessonsView").hidden = review; $("reviewView").hidden = !review;
  $("lessonsViewBtn").setAttribute("aria-pressed", String(!review));
  $("reviewViewBtn").setAttribute("aria-pressed", String(review));
}
async function loadLessons() {
  const version = ++learning.loadVersion;
  $("learningStatus").textContent = "Загружаю записи…";
  $("refreshLessons").disabled = true;
  try {
    const response = await fetch("/api/lessons");
    if (!response.ok) throw new Error();
    const data = await response.json();
    if (version !== learning.loadVersion) return;
    learning.sessions = data.sessions;
    $("memoryCount").textContent = `Сохранённых занятий: ${data.sessions.length}`;
    $("learningStatus").textContent = "";
    renderLessonList(); renderReview();
    if (learning.selected && !data.sessions.some(s => s.id === learning.selected)) {
      learning.noteVersion++; learning.selected = null; learning.note = null;
      $("downloadLesson").disabled = true; $("lessonDate").textContent = "";
      $("deleteLesson").disabled = true;
      $("deletePrompt").hidden = true;
      $("lessonContent").replaceChildren(element("p", "reader-empty", "Запись перемещена. Выбери другое занятие."));
      $("lessonsView").classList.remove("reading");
    }
  } catch {
    if (version !== learning.loadVersion) return;
    $("learningStatus").textContent = "Не удалось обновить архив. Проверь подключение к серверу и нажми «Обновить».";
    $("memoryCount").textContent = learning.sessions.length ? "Показаны ранее загруженные записи" : "Архив временно недоступен";
  } finally { if (version === learning.loadVersion) $("refreshLessons").disabled = false; }
}
function openLearning() {
  $("settingsDialog").close();
  if (!$("memoryDialog").open) $("memoryDialog").showModal();
  loadLessons();
  if (!$("trashView").hidden) loadTrash();
}
$("memoryBtn").addEventListener("click", openLearning);
$("learningBtn").addEventListener("click", openLearning);
$("refreshLessons").addEventListener("click", () => { loadLessons(); if (!$("trashView").hidden) loadTrash(); });
$("lessonSearch").addEventListener("input", renderLessonList);
$("lessonsViewBtn").addEventListener("click", () => showLearningView(false));
$("reviewViewBtn").addEventListener("click", () => showLearningView(true));
$("backToLessons").addEventListener("click", () => { $("lessonsView").classList.remove("reading"); $("lessonSearch").focus(); });
$("downloadLesson").addEventListener("click", () => { if (learning.note) download(learning.note.markdown, learning.note.filename); });

let archiveBusy = false;
let trashVersion = 0;
async function archiveRequest(url, method) {
  const response = await fetch(url, { method, headers: {"X-Sarah-Archive": "1"} });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "Не удалось изменить запись. Попробуй ещё раз.");
  }
  return response.json();
}
$("deleteLesson").addEventListener("click", () => {
  $("deletePrompt").hidden = false;
  $("cancelDeleteLesson").focus();
});
$("cancelDeleteLesson").addEventListener("click", () => {
  $("deletePrompt").hidden = true;
  $("deleteLesson").focus();
});
$("confirmDeleteLesson").addEventListener("click", async () => {
  if (archiveBusy || !learning.selected) return;
  const id = learning.selected;
  archiveBusy = true;
  $("confirmDeleteLesson").disabled = true;
  try {
    await archiveRequest(`/api/lessons/${encodeURIComponent(id)}`, "DELETE");
    // Update locally even if the subsequent archive refresh is unavailable.
    learning.loadVersion++;
    $("refreshLessons").disabled = false;
    learning.sessions = learning.sessions.filter(s => s.id !== id);
    if (learning.selected === id) {
      learning.noteVersion++;
      learning.selected = null; learning.note = null;
      $("deleteLesson").disabled = $("downloadLesson").disabled = true;
      $("deletePrompt").hidden = true;
      $("lessonDate").textContent = "";
      $("lessonContent").replaceChildren(element("p", "reader-empty", "Занятие в корзине. Его можно восстановить во вкладке «Корзина»."));
      $("lessonsView").classList.remove("reading");
    }
    renderLessonList(); renderReview();
    $("memoryCount").textContent = `Сохранённых занятий: ${learning.sessions.length}`;
    $("learningStatus").textContent = "Занятие перемещено в корзину. Для отмены открой «Корзина» и нажми «Восстановить».";
    $("trashViewBtn").focus();
  } catch (error) { $("learningStatus").textContent = error.message; }
  finally { archiveBusy = false; $("confirmDeleteLesson").disabled = false; }
});

async function loadTrash() {
  const version = ++trashVersion;
  $("trashList").replaceChildren(element("p", "library-empty", "Загружаю корзину…"));
  try {
    const response = await fetch("/api/lesson-trash");
    if (!response.ok) throw new Error();
    const data = await response.json();
    if (version !== trashVersion) return;
    $("trashList").replaceChildren();
    for (const session of data.sessions) {
      const row = element("div", "trash-row");
      const restore = element("button", "button outline", "Восстановить");
      restore.addEventListener("click", async () => {
        if (archiveBusy) return;
        archiveBusy = true; restore.disabled = true;
        try {
          await archiveRequest(`/api/lesson-trash/${encodeURIComponent(session.id)}/restore`, "POST");
          await loadLessons(); await loadTrash();
          $("learningStatus").textContent = "Занятие восстановлено в архиве и доступно Sarah для следующих разговоров.";
        } catch (error) { $("learningStatus").textContent = error.message; }
        finally { archiveBusy = false; restore.disabled = false; }
      });
      row.append(element("span", "", session.filename), restore);
      $("trashList").append(row);
    }
    if (!data.sessions.length) $("trashList").append(element("p", "library-empty", "Корзина пуста."));
  } catch {
    if (version === trashVersion) $("trashList").replaceChildren(element("p", "library-empty", "Не удалось загрузить корзину. Нажми «Обновить», чтобы попробовать снова."));
  }
}
$("trashViewBtn").addEventListener("click", () => {
  $("lessonsView").hidden = $("reviewView").hidden = true;
  $("trashView").hidden = false;
  $("lessonsViewBtn").setAttribute("aria-pressed", "false");
  $("reviewViewBtn").setAttribute("aria-pressed", "false");
  $("trashViewBtn").setAttribute("aria-pressed", "true");
  loadTrash();
});
