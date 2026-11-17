const $ = id => document.getElementById(id);
const MAX_UPLOAD = 20 * 1024 * 1024;
let room = "general", last = 0, timer;

async function api(url, data, raw) {
  const opt = data === undefined ? {} : {
    method: "POST",
    headers: raw ? {} : { "Content-Type": "application/json" },
    body: raw ? data : JSON.stringify(data)
  };
  const r = await fetch(url, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}
const showErr = e => { $("err").textContent = e.message; };

function show(name) {
  $("auth").hidden = true; $("app").hidden = false;
  $("who").textContent = name;
  enter("general");
}

async function auth(path) {
  $("err").textContent = ""; $("note").textContent = "";
  try {
    const j = await api(path, { name: $("name").value, key: $("key").value });
    if (j.key) $("note").textContent = "Your key (shown once, save it): " + j.key;
    show(j.name);
  } catch (e) { showErr(e); }
}
$("in").onclick = () => auth("/api/login");
$("up").onclick = () => auth("/api/signup");
$("out").onclick = async () => { await api("/api/logout", {}); location.reload(); };

async function loadRooms() {
  const list = await api("/api/rooms");
  $("rooms").replaceChildren(...list.map(c => {
    const b = document.createElement("button");
    b.className = "room" + (c === room ? " on" : "");
    b.textContent = c === "general" ? "# general" : c;
    b.onclick = () => enter(c);
    return b;
  }));
}

function enter(c) {
  room = c; last = 0;
  $("log").replaceChildren();
  $("title").textContent = c === "general" ? "Chat Room" : "Private room. Share this code: " + c;
  loadRooms();
  clearInterval(timer); poll(); timer = setInterval(poll, 2000);
}

$("mk").onclick = async () => {
  try { enter((await api("/api/create", {})).room); } catch (e) { showErr(e); }
};
$("jn").onclick = async () => {
  try {
    const j = await api("/api/join", { code: $("code").value.trim() });
    $("code").value = ""; $("err").textContent = "";
    enter(j.room);
  } catch (e) { showErr(e); }
};
function line(m) {
  const d = document.createElement("div");
  d.textContent = "[" + new Date(m.ts * 1000).toLocaleTimeString() + "] " + m.user + ": " + (m.text || "") + " ";
  if (m.file) {
    const a = document.createElement("a");
    a.href = "/f/" + m.file; a.textContent = "[file] " + m.fname;
    d.append(a);
  }
  return d;  
}

async function poll() {
  const r = room;
  try {
    const ms = await api("/api/msgs?room=" + encodeURIComponent(r) + "&after=" + last);
    if (r !== room) return;
    const log = $("log");
    const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
    for (const m of ms) { last = m.id; log.append(line(m)); }
    if (ms.length && stick) log.scrollTop = log.scrollHeight;
  } catch (e) {}
}

$("send").onclick = async () => {
  const text = $("text").value.trim(), f = $("file").files[0];
  if (!text && !f) return;
  $("err").textContent = "";
  try {
    if (f && f.size > MAX_UPLOAD) throw new Error("File had to be less than 20MB");
    if (text) await api("/api/send", { room, text });
    if (f) await api("/api/upload?room=" + encodeURIComponent(room) + "&name=" + encodeURIComponent(f.name), f, true);
    $("text").value = ""; $("file").value = "";
    poll();
  } catch (e) { showErr(e); }
};
$("text").onkeydown = e => { if (e.key === "Enter") $("send").click(); };
api("/api/me").then(j => show(j.name)).catch(() => {});
