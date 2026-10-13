"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  phase: "idle",
  mode: "voice",
  focus: "Free conversation",
  muted: false,
  ws: null,
  context: null,
  stream: null,
  worklet: null,
  analyser: null,
  sources: new Set(),
  nextAudio: 0,
  generating: false,
  suppressAudio: false,
  cooldown: 0,
  timer: null,
  started: 0,
  timeout: null,
  animation: null,
  turns: [],
  activeTurns: {},
  corrections: [],
  upgrades: [],
  takeaways: [],
  notes: "",
  filename: "",
  failed: false,
  streamPaused: true,
  textPending: false,
  level: "B1+",
  recording: false,
  committing: false,
  hasVoice: false,
  utteranceId: 0,
  inputLevel: 0,
  speechFrames: 0,
  preRoll: [],
  awaitingSilence: false,
  silentFrames: 0,
  captionText: "",
  captionLocked: false,
};
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

function notice(text, error = false) {
  $("notice").textContent = text;
  $("notice").classList.toggle("error", error);
  $("notice").hidden = !text;
}
function send(packet) {
  if (state.ws?.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(packet));
    return true;
  }
  return false;
}
function setPhase(phase) {
  state.phase = phase;
  document.body.dataset.phase = phase;
  const active = phase === "active",
    idle = phase === "idle";
  $("startBtn").hidden = active;
  $("startBtn").disabled = !idle;
  $("startBtn").querySelector("span").textContent =
    phase === "connecting"
      ? "Подключение…"
      : phase === "saving"
        ? "Сохраняю…"
        : "Начать разговор";
  $("modeBtn").hidden = !idle && !active;
  $("commitBtn").hidden = !active || state.mode !== "voice";
  $("muteBtn").hidden = !active || state.mode !== "voice";
  $("endBtn").hidden = !active;
  $("textInput").disabled = !active;
  $("sendBtn").disabled = !active || state.textPending || state.recording;
  $("levelSelect").disabled = !idle;
  for (const id of ["learningBtn", "memoryBtn"]) {
    $(id).disabled = !idle;
    $(id).title = idle ? "" : "Заверши разговор, чтобы открыть архив занятий";
  }
  document
    .querySelectorAll("[data-focus]")
    .forEach((button) => (button.disabled = !idle));
  $("connection").classList.toggle("active", active);
  $("feedbackBadge").classList.toggle("active", active);
  $("connectionText").textContent = {
    idle: "Готова слушать",
    connecting: "Подключение…",
    active: "Разговор идёт",
    saving: "Сохраняю заметки…",
  }[phase];
  refreshRoom();
}
function refreshRoom() {
  const speaking =
    !state.suppressAudio && (state.generating || state.sources.size > 0);
  const active = state.phase === "active";
  $("interruptBtn").hidden = !active || !speaking;
  $("commitBtn").disabled =
    !state.hasVoice || !state.recording || state.committing;
  $("sendBtn").disabled = !active || state.textPending || state.recording;
  $("voiceArt").classList.toggle("speaking", speaking);
  $("voiceArt").classList.toggle("listening", state.recording);
  let title = "Поговорим?",
    description = "На английском. С поддержкой на русском.";
  if (state.phase === "connecting") {
    title = "Секунду…";
    description = "Подключаю Sarah и микрофон.";
  }
  if (state.phase === "saving") {
    title = "Хорошая практика.";
    description = "Сохраняю разговор и заметки.";
  }
  if (active) {
    title = speaking
      ? "Sarah говорит"
      : state.recording
        ? "Я слушаю"
        : "Твой ход";
    description = speaking
      ? "Начни говорить, чтобы перебить."
      : state.mode === "text"
        ? "Можно написать по-английски или по-русски."
        : state.muted
          ? "Микрофон выключен."
          : state.recording
            ? "Не торопись. Отправь, когда закончишь мысль."
            : "Начни говорить. Я дождусь отправки.";
  }
  $("roomTitle").textContent = title;
  $("roomDescription").textContent = description;
  $("roomState").lastChild.textContent = description;
  const micOpen = active && state.mode === "voice" && !state.muted;
  $("micLabel").textContent = !active
    ? "Микрофон выключен"
    : state.mode === "text"
      ? "Текстовый режим"
      : state.muted
        ? "Микрофон выключен"
        : "Микрофон слышит тебя";
  $("micSignal").classList.toggle("active", micOpen);
}
function resetSession() {
  state.turns = [];
  state.activeTurns = {};
  state.corrections = [];
  state.upgrades = [];
  state.takeaways = [];
  state.recording = false;
  state.committing = false;
  state.hasVoice = false;
  state.inputLevel = 0;
  state.speechFrames = 0;
  state.preRoll = [];
  state.awaitingSilence = false;
  state.silentFrames = 0;
  state.captionText = "";
  $("liveSpeech").textContent = "Здесь появится то, что ты говоришь.";
  $("speechLabel").textContent = "Ты";
  $("liveReply").textContent = "";
  $("sarahPreview").hidden = true;
  state.notes = "";
  state.filename = "";
  state.failed = false;
  state.textPending = false;
  $("transcript").replaceChildren();
  $("correctionsList").replaceChildren();
  $("upgradesList").replaceChildren();
  $("correctionsEmpty").hidden = false;
  $("upgradesEmpty").hidden = false;
  $("correctionCount").textContent = "0";
  $("upgradeCount").textContent = "0";
  $("feedbackStatus").textContent = "";
  $("takeaways").replaceChildren(
    element(
      "p",
      "takeaway-placeholder",
      "Полезные фразы из разговора.",
    ),
  );
  $("downloadTranscript").disabled = true;
  $("timer").textContent = "00:00";
  state.muted = false;
  state.suppressAudio = false;
  state.generating = false;
  $("muteBtn").setAttribute("aria-pressed", "false");
  $("muteBtn").querySelector("span").textContent = "Mute";
}
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function updateTurn(speaker, text, final) {
  if (!text) return;
  const log = $("transcript");
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 70;
  let turn = state.activeTurns[speaker];
  if (!turn) {
    $("transcriptEmpty")?.remove();
    const row = element("article", `turn ${speaker}`);
    const content = element("div");
    row.append(
      element("span", "turn-avatar", speaker === "user" ? "M" : "S"),
      content,
    );
    content.append(
      element("div", "turn-name", speaker === "user" ? "Ты" : "Sarah"),
    );
    const body = element("p", "turn-text");
    content.append(body);
    log.append(row);
    turn = { role: speaker, text: "", body };
    state.activeTurns[speaker] = turn;
    state.turns.push(turn);
  }
  turn.text = text;
  turn.body.textContent = text;
  if (final) delete state.activeTurns[speaker];
  if (nearBottom) log.scrollTop = log.scrollHeight;
  $("downloadTranscript").disabled = false;
}
function renderCoaching(data) {
  $("feedbackStatus").textContent = "";
  for (const kind of ["corrections", "upgrades"]) {
    const items = Array.isArray(data[kind]) ? data[kind] : [];
    for (const item of items) {
      state[kind].push(item);
      const card = element(
        "article",
        `feedback-card ${kind === "upgrades" ? "upgrade" : ""}`,
      );
      card.append(
        element(
          "small",
          "",
          kind === "corrections" ? "Твоя фраза" : "YOUR PHRASING",
        ),
      );
      card.append(
        element(
          kind === "corrections" ? "del" : "p",
          "original",
          item.original,
        ),
      );
      card.append(
        element(
          "small",
          "",
          kind === "corrections" ? "Правильнее" : "ANOTHER WAY",
        ),
      );
      card.append(element("strong", "", item.correction || item.better));
      card.append(element("p", "", item.rule || item.context));
      $(kind + "List").prepend(card);
    }
    $(kind + "Empty").hidden = state[kind].length > 0;
  }
  $("correctionCount").textContent = String(state.corrections.length);
  $("upgradeCount").textContent = String(state.upgrades.length);
  if (!data.corrections?.length)
    $("feedbackStatus").textContent =
      "В последней реплике исправлений нет. Продолжай разговор.";
  if (data.takeaway && !state.takeaways.includes(data.takeaway)) {
    if (!state.takeaways.length) $("takeaways").replaceChildren();
    state.takeaways.push(data.takeaway);
    $("takeaways").prepend(element("p", "takeaway", data.takeaway));
  }
}
function stopPlayback() {
  for (const source of state.sources) {
    source.onended = null;
    try {
      source.stop();
      source.disconnect();
    } catch {}
  }
  state.sources.clear();
  state.nextAudio = state.context?.currentTime || 0;
  state.cooldown = performance.now() + 400;
}
function beginVoiceTurn() {
  if (
    state.phase !== "active" ||
    state.recording ||
    state.mode !== "voice" ||
    state.muted
  )
    return;
  state.suppressAudio = true;
  stopPlayback();
  state.generating = false;
  state.recording = true;
  state.hasVoice = false;
  state.utteranceId++;
  state.captionText = "";
  state.captionLocked = false;
  state.speechFrames = 0;
  $("liveSpeech").textContent = "Слушаю…";
  $("speechLabel").textContent = "Ты говоришь";
  send({ type: "activity_start", turn_id: state.utteranceId });
  refreshRoom();
}
function interrupt() {
  if (state.phase !== "active") return;
  if (state.mode === "voice" && !state.muted) beginVoiceTurn();
  else {
    state.suppressAudio = true;
    stopPlayback();
    state.generating = false;
    refreshRoom();
    $("textForm").hidden = false;
    $("textInput").focus();
  }
}
function sendPcm(buffer) {
  if (state.ws?.readyState !== WebSocket.OPEN) return;
  if (state.ws.bufferedAmount > 262144) {
    notice(
      "Соединение не успевает передавать звук. Завершаю сессию и сохраняю диалог.",
      true,
    );
    endSession();
    return;
  }
  state.ws.send(buffer);
}
function processMicrophone(buffer) {
  const pcm = new Int16Array(buffer);
  const rms = Math.sqrt(
    pcm.reduce((sum, x) => sum + (x / 32768) ** 2, 0) / pcm.length,
  );
  state.inputLevel = state.muted ? 0 : rms;
  if (state.phase !== "active" || state.muted) return;
  const threshold = $("echoShield").checked ? 0.012 : 0.006;
  if (state.recording) {
    if (rms > threshold) state.hasVoice = true;
    sendPcm(buffer);
    refreshRoom();
    return;
  }
  if (state.awaitingSilence) {
    state.silentFrames = rms < threshold ? state.silentFrames + 1 : 0;
    if (state.silentFrames >= 6) state.awaitingSilence = false;
    return;
  }
  state.preRoll.push(buffer);
  if (state.preRoll.length > 8) state.preRoll.shift();
  state.speechFrames = rms > threshold ? state.speechFrames + 1 : 0;
  if (state.speechFrames >= 3) {
    beginVoiceTurn();
    state.hasVoice = true;
    for (const chunk of state.preRoll) sendPcm(chunk);
    state.preRoll = [];
    refreshRoom();
  }
}
function commitVoiceTurn() {
  if (!state.recording || !state.hasVoice || state.committing) return;
  state.committing = true;
  refreshRoom();
  state.worklet.port.postMessage({ type: "flush" });
}
function finishVoiceTurn() {
  if (!state.committing) return;
  send({ type: "audio_stream_end" });
  state.recording = false;
  state.committing = false;
  state.hasVoice = false;
  state.suppressAudio = false;
  state.preRoll = [];
  state.speechFrames = 0;
  state.awaitingSilence = true;
  state.silentFrames = 0;
  $("speechLabel").textContent = "Отправлено";
  refreshRoom();
}
function queueAudio(buffer) {
  if (
    !state.context ||
    state.phase !== "active" ||
    state.suppressAudio ||
    state.recording ||
    !buffer.byteLength
  )
    return;
  state.generating = true;
  const samples = new Int16Array(buffer);
  const audio = state.context.createBuffer(1, samples.length, 24000);
  const output = audio.getChannelData(0);
  for (let i = 0; i < samples.length; i++) output[i] = samples[i] / 32768;
  const source = state.context.createBufferSource();
  source.buffer = audio;
  source.connect(state.analyser);
  state.sources.add(source);
  const start = Math.max(state.context.currentTime + 0.025, state.nextAudio);
  state.nextAudio = start + audio.duration;
  source.onended = () => {
    state.sources.delete(source);
    source.disconnect();
    if (!state.sources.size) {
      state.cooldown = performance.now() + 400;
      refreshRoom();
    }
  };
  source.start(start);
  refreshRoom();
}
function animateVoice() {
  if (!state.analyser || !state.context) return;
  const values = new Uint8Array(state.analyser.frequencyBinCount);
  state.analyser.getByteFrequencyData(values);
  const output = values.reduce((a, b) => a + b, 0) / values.length / 128;
  const mic = state.muted ? 0 : Math.min(1, state.inputLevel * 10);
  const level = Math.max(mic, state.suppressAudio ? 0 : output);
  // Audio amplitude is functional feedback, not a decorative motion loop.
  $("voiceArt").style.setProperty(
    "--voice-level",
    String(0.45 + Math.min(1.3, level * 2)),
  );
  if (!reducedMotion.matches)
    $("voiceArt").style.setProperty(
      "--orb-scale",
      String(1 + Math.min(0.12, level * 0.12)),
    );
  $("micSignal").style.setProperty("--mic-level", String(0.5 + mic * 2));
  state.animation = requestAnimationFrame(animateVoice);
}
function stopMedia() {
  stopPlayback();
  state.generating = false;
  state.recording = false;
  state.committing = false;
  if (state.animation) cancelAnimationFrame(state.animation);
  state.animation = null;
  if (state.worklet) {
    state.worklet.port.onmessage = null;
    state.worklet.disconnect();
  }
  state.worklet = null;
  state.stream?.getTracks().forEach((track) => track.stop());
  state.stream = null;
  const context = state.context;
  state.context = null;
  state.analyser = null;
  if (context && context.state !== "closed") context.close().catch(() => {});
  clearInterval(state.timer);
  state.timer = null;
  $("voiceArt").style.removeProperty("--voice-level");
}
async function startSession() {
  if (state.phase !== "idle") return;
  setPhase("connecting");
  notice("");
  try {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context)
      throw new Error(
        "Браузер не поддерживает звук. Открой приложение в Chrome или Edge.",
      );
    state.context = new Context();
    await state.context.resume();
    state.analyser = state.context.createAnalyser();
    state.analyser.fftSize = 64;
    state.analyser.connect(state.context.destination);
    if (state.mode === "voice") {
      if (!navigator.mediaDevices?.getUserMedia)
        throw new Error("Для микрофона открой приложение через localhost или HTTPS.");
      state.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      await state.context.audioWorklet.addModule("/static/pcm-worklet.js");
      const source = state.context.createMediaStreamSource(state.stream);
      state.worklet = new AudioWorkletNode(state.context, "pcm-capture");
      source.connect(state.worklet);
      state.worklet.connect(state.context.destination);
      state.worklet.port.onmessage = (event) => {
        if (event.data?.type === "flushed") {
          finishVoiceTurn();
          return;
        }
        processMicrophone(event.data);
      };
    }
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(
      `${protocol}//${location.host}/ws/voice?focus=${encodeURIComponent(state.focus)}&level=${encodeURIComponent(state.level)}`,
    );
    state.ws = socket;
    socket.binaryType = "arraybuffer";
    state.timeout = setTimeout(() => {
      if (state.phase === "connecting") {
        state.failed = true;
        notice(
          "Sarah не подключилась. Проверь соединение и попробуй ещё раз.",
          true,
        );
        socket.close();
        stopMedia();
        setPhase("idle");
      }
    }, 20000);
    socket.onmessage = (event) => {
      if (state.ws !== socket) return;
      if (event.data instanceof ArrayBuffer) {
        queueAudio(event.data);
        return;
      }
      try {
        handleMessage(JSON.parse(event.data));
      } catch {
        notice(
          "Не удалось прочитать ответ. Диалог можно скачать через кнопку истории.",
          true,
        );
      }
    };
    socket.onerror = () => {
      if (state.ws === socket) {
        state.failed = true;
        notice(
          "Голосовой сервис недоступен. Проверь, запущен ли сервер.",
          true,
        );
      }
    };
    socket.onclose = () => {
      if (state.ws !== socket) return;
      clearTimeout(state.timeout);
      stopMedia();
      if (state.phase === "active")
        notice(
          "Соединение прервано. Сервер попробует сохранить заметки. На всякий случай скачай диалог через кнопку истории.",
          true,
        );
      else if (state.phase === "saving" && !state.notes && !state.failed)
        notice(
          "Сохранение не подтверждено. Скачай диалог через кнопку истории, чтобы оставить копию.",
          true,
        );
      state.ws = null;
      setPhase("idle");
    };
  } catch (error) {
    clearTimeout(state.timeout);
    stopMedia();
    setPhase("idle");
    const message =
      error.name === "NotAllowedError"
        ? "Нет доступа к микрофону. Разреши его в браузере или выбери «Написать текстом»."
        : error.name === "NotFoundError"
          ? "Микрофон не найден. Подключи его или выбери «Написать текстом»."
          : error.message;
    notice(message, true);
  }
}
function handleMessage(msg) {
  if (msg.type === "ready") {
    if (state.phase !== "connecting") return;
    clearTimeout(state.timeout);
    resetSession();
    setPhase("active");
    state.started = Date.now();
    state.timer = setInterval(() => {
      const seconds = Math.floor((Date.now() - state.started) / 1000);
      $("timer").textContent =
        `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    }, 1000);
    $("controlHelp").textContent =
      "Не торопись. Sarah ответит только после «Отправить».";
    animateVoice();
    if (state.mode === "text") $("textInput").focus();
  } else if (msg.type === "turn_start") {
    if (msg.speaker === "model") {
      state.generating = true;
      if (!state.recording) state.suppressAudio = false;
      refreshRoom();
    }
  } else if (msg.type === "transcript_delta" || msg.type === "turn_complete") {
    updateTurn(msg.speaker, msg.full_text, msg.type === "turn_complete");
    if (msg.speaker === "model" && msg.full_text) {
      const reply = $("liveReply");
      const nearBottom = reply.scrollHeight - reply.scrollTop - reply.clientHeight < 48;
      reply.textContent = msg.full_text;
      $("sarahPreview").hidden = false;
      if (nearBottom) reply.scrollTop = reply.scrollHeight;
    }
    if (msg.speaker === "user" && !state.recording) {
      $("liveSpeech").textContent = msg.full_text;
      $("speechLabel").textContent = "Ты";
      if (msg.type === "turn_complete") state.captionLocked = true;
    }
    if (msg.speaker === "user" && msg.type === "turn_complete") {
      state.textPending = false;
      $("sendBtn").disabled = state.phase !== "active";
    }
  } else if (msg.type === "response_complete") {
    state.generating = false;
    if (!state.recording) state.suppressAudio = false;
    refreshRoom();
  } else if (msg.type === "interrupted") {
    stopPlayback();
    state.generating = false;
    if (!state.recording) state.suppressAudio = false;
    refreshRoom();
  } else if (msg.type === "speech_preview") {
    if (msg.turn_id === state.utteranceId && !state.captionLocked) {
      state.captionText = msg.text;
      $("liveSpeech").textContent = msg.text || "Слушаю…";
    }
  } else if (msg.type === "caption_status") {
    if (msg.turn_id === state.utteranceId)
      $("speechLabel").textContent = msg.text;
  } else if (msg.type === "live_coaching") renderCoaching(msg.data);
  else if (msg.type === "coaching_status")
    $("feedbackStatus").textContent = msg.text;
  else if (msg.type === "error") {
    state.failed = true;
    notice(msg.text, true);
    stopMedia();
    setPhase("saving");
  } else if (msg.type === "saving") {
    stopMedia();
    setPhase("saving");
  } else if (msg.type === "summary") {
    state.notes = msg.markdown;
    state.filename = msg.filename;
    renderNote($("summaryContent"), msg.markdown);
    $("saveStatus").textContent = "Сохранено в Obsidian. Можно вернуться к этой заметке в «Моих занятиях».";
    if (!$("summaryDialog").open) $("summaryDialog").showModal();
    notice(
      "Занятие сохранено. Заметка и диалог доступны в «Моих занятиях».",
    );
  } else if (msg.type === "save_error") {
    state.failed = true;
    notice(msg.text, true);
  } else if (msg.type === "session_ended")
    notice("Разговор завершён. Реплик для сохранения пока нет.");
}
function endSession() {
  if (state.phase !== "active") return;
  send({ type: "audio_stream_end" });
  send({ type: "end_session" });
  stopMedia();
  setPhase("saving");
}
function download(text, filename) {
  const url = URL.createObjectURL(
    new Blob([text], { type: "text/markdown;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("startBtn").addEventListener("click", startSession);
$("endBtn").addEventListener("click", endSession);
$("interruptBtn").addEventListener("click", interrupt);
$("modeBtn").addEventListener("click", () => {
  if (state.phase === "idle") {
    state.mode = state.mode === "voice" ? "text" : "voice";
    $("textForm").hidden = state.mode !== "text";
    $("modeBtn").querySelector("span").textContent =
      state.mode === "text" ? "Говорить голосом" : "Написать текстом";
    $("modeBtn").setAttribute("aria-pressed", String(state.mode === "text"));
  } else {
    $("textForm").hidden = !$("textForm").hidden;
    if (!$("textForm").hidden) $("textInput").focus();
  }
});
$("commitBtn").addEventListener("click", commitVoiceTurn);
$("settingsBtn").addEventListener("click", () =>
  $("settingsDialog").showModal(),
);
$("transcriptBtn").addEventListener("click", () =>
  $("transcriptDialog").showModal(),
);
const levelDescriptions = {
  A2: "Простые предложения и знакомые слова. Можно попросить начало фразы.",
  B1: "Повседневные темы, истории и планы. Без необъяснённых идиом.",
  "B1+": "Понятные слова, короткие ответы, без сложного делового жаргона.",
  B2: "Более подробные объяснения, аргументы и естественные сочетания слов.",
  C1: "Нюансы смысла и точные формулировки. Без лишнего жаргона.",
};
function setLevel(value) {
  state.level = Object.hasOwn(levelDescriptions, value) ? value : "B1+";
  $("levelSelect").value = state.level;
  $("levelBadge").textContent = state.level;
  $("levelDescription").textContent = levelDescriptions[state.level];
  try {
    localStorage.setItem("voice-tutor-target-level", state.level);
  } catch {}
}
try {
  setLevel(localStorage.getItem("voice-tutor-target-level") || "B1+");
} catch {
  setLevel("B1+");
}
$("levelSelect").addEventListener("change", (event) =>
  setLevel(event.target.value),
);
$("muteBtn").addEventListener("click", () => {
  state.muted = !state.muted;
  state.stream
    ?.getAudioTracks()
    .forEach((track) => (track.enabled = !state.muted));
  $("muteBtn").setAttribute("aria-pressed", String(state.muted));
  $("muteBtn").querySelector("span").textContent = state.muted
    ? "Unmute"
    : "Mute";
  refreshRoom();
});
$("echoShield").addEventListener("change", refreshRoom);
document.querySelectorAll("[data-focus]").forEach((button) =>
  button.addEventListener("click", () => {
    state.focus = button.dataset.focus;
    document.querySelectorAll("[data-focus]").forEach((item) => {
      item.classList.toggle("selected", item === button);
      item.setAttribute("aria-pressed", String(item === button));
    });
  }),
);
document.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-tab]").forEach((item) => {
      const selected = item === button;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
      $(item.dataset.tab + "Panel").hidden = !selected;
    });
  });
  button.addEventListener("keydown", (event) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const target =
        event.key === "Home"
          ? $("correctionsTab")
          : event.key === "End"
            ? $("upgradesTab")
            : button === $("correctionsTab")
              ? $("upgradesTab")
              : $("correctionsTab");
      target.click();
      target.focus();
    }
  });
});
$("textForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("textInput").value.trim();
  if (!text || state.phase !== "active" || state.textPending || state.recording)
    return;
  if (state.generating || state.sources.size) {
    state.suppressAudio = true;
    stopPlayback();
    state.generating = false;
  }
  if (send({ type: "user_turn", text })) {
    state.utteranceId++;
    state.captionLocked = true;
    $("liveSpeech").textContent = text;
    $("speechLabel").textContent = "Отправляю…";
    state.textPending = true;
    $("sendBtn").disabled = true;
    $("textInput").value = "";
  }
});
document.addEventListener("keydown", (event) => {
  if (
    event.code !== "Space" ||
    event.repeat ||
    event.target.closest(
      "input,textarea,button,a,select,[contenteditable=true]",
    ) ||
    document.querySelector("dialog[open]")
  )
    return;
  if (state.phase === "active" && (state.generating || state.sources.size)) {
    event.preventDefault();
    interrupt();
  }
});
document
  .querySelectorAll("[data-close]")
  .forEach((button) =>
    button.addEventListener("click", () => $(button.dataset.close).close()),
  );
$("downloadTranscript").addEventListener("click", () =>
  download(
    "# Conversation with Sarah\n\n" +
      state.turns
        .map(
          (turn) =>
            `**${turn.role === "user" ? "Ты" : "Sarah"}:** ${turn.text}`,
        )
        .join("\n\n"),
    `voice-tutor-${new Date().toISOString().slice(0, 10)}.md`,
  ),
);
$("downloadNotes").addEventListener("click", () =>
  download(state.notes, state.filename || "session-notes.md"),
);
window.addEventListener("beforeunload", (event) => {
  if (state.phase !== "idle") {
    event.preventDefault();
    event.returnValue = "";
  }
});
fetch("/api/status")
  .then((response) => {
    if (!response.ok) throw new Error();
    return response.json();
  })
  .then((data) => {
    if (!data.configured)
      notice(
        "Добавь ключ Gemini в .env и перезапусти сервер.",
        true,
      );
  })
  .catch(() =>
    notice(
      "Сервер недоступен. Запусти Voice Tutor и обнови страницу.",
      true,
    ),
  );
