"""
Lokal web dashboard: navbat.py ni shu yerdan ishga tushirish, to'xtatish,
logni va screenshotlarni ko'rish, .env sozlamalarini tahrirlash.

Faqat 127.0.0.1 da ishlaydi (tashqaridan kirib bo'lmaydi). Qo'shimcha
kutubxona kerak emas (stdlib).

    python dashboard.py            ->  http://127.0.0.1:8106
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ENV = HERE / ".env"
ENV_EXAMPLE = HERE / ".env.example"
LOG = HERE / "navbat.log"
SHOTS = HERE / "shots"
# Docker 8106 (compose da DASHBOARD_PORT=8106), lokal ishga tushirish 8108 - bir vaqtda ikkalasi ochilsa ham to'qnashmaydi.
PORT = int(os.getenv("DASHBOARD_PORT", "8108"))

ENV_KEYS = ["LOGIN_ID", "PASSWORD", "START_TIME", "LEAD_MINUTES", "STOP_AFTER_MINUTES", "POLL_SECONDS", "PRE_POLL_SECONDS",
            "CATEGORY_ID", "EVENT_ID", "PLAN_ID", "MONTHS", "SLOT_SELECTOR", "PREFER_TEXT",
            "APPLICANT_NAME", "FIRST_NAME", "LAST_NAME", "NAME_KANA", "PHONE", "EMAIL", "PASSPORT_NO",
            "BIRTH_DATE", "NATIONALITY", "ADDRESS", "PURPOSE", "REMARKS", "FORM_FIELDS",
            "AGREE_CHECKBOX", "HEADLESS"]
DEFAULTS = {"START_TIME": "19:00", "LEAD_MINUTES": "5", "STOP_AFTER_MINUTES": "10", "POLL_SECONDS": "1", "PRE_POLL_SECONDS": "20",
            "CATEGORY_ID": "12", "EVENT_ID": "20", "PLAN_ID": "19", "MONTHS": "1",
            "AGREE_CHECKBOX": "1", "HEADLESS": "0"}

_proc: subprocess.Popen | None = None
_mode: str = ""
_started: float = 0
_lock = threading.Lock()


# ------------------------------------------------------------------ .env ---
def read_env() -> dict[str, str]:
    src = ENV if ENV.exists() else ENV_EXAMPLE
    out = {k: DEFAULTS.get(k, "") for k in ENV_KEYS}
    if src.exists():
        for line in src.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in out:
                out[k.strip()] = v.strip()
    if not ENV.exists():
        out["LOGIN_ID"] = out["PASSWORD"] = ""
    return out


def write_env(values: dict[str, str]) -> None:
    cur = read_env()
    old_pw = cur["PASSWORD"]
    for k in ENV_KEYS:
        if k in values:
            cur[k] = values[k].strip()
    # Parol bo'sh yuborilsa eskisini saqlab qolamiz (formada ko'rsatilmaydi).
    if not cur["PASSWORD"]:
        cur["PASSWORD"] = old_pw
    # Izoh (#) qatorlari saqlanib qoladi, faqat kalitlar qayta yoziladi.
    comments = [l for l in ENV.read_text(encoding="utf-8").splitlines() if l.lstrip().startswith("#")] if ENV.exists() else []
    lines = comments + [""] + [f"{k}={cur[k]}" for k in ENV_KEYS]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------- process ---
def start(mode: str) -> str:
    global _proc, _mode, _started
    with _lock:
        if _proc and _proc.poll() is None:
            return "Skript allaqachon ishlayapti"
        env = read_env()
        if not env["LOGIN_ID"] or not env["PASSWORD"]:
            return "Avval LOGIN_ID va PASSWORD ni saqlang"
        args = [sys.executable, str(HERE / "navbat.py")]
        if mode == "now":
            args.append("--now")
        elif mode == "discover":
            args.append("--discover")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        _proc = subprocess.Popen(args, cwd=str(HERE), creationflags=flags)
        _mode, _started = mode, time.time()
        return f"Ishga tushdi ({mode}), pid {_proc.pid}"


def stop() -> str:
    global _proc
    with _lock:
        if not _proc or _proc.poll() is not None:
            return "Ishlayotgan skript yo'q"
        _proc.terminate()
        try:
            _proc.wait(5)
        except subprocess.TimeoutExpired:
            _proc.kill()
        return "To'xtatildi"


def status() -> dict:
    running = bool(_proc and _proc.poll() is None)
    tail = ""
    if LOG.exists():
        data = LOG.read_bytes()[-20000:]
        tail = data.decode("utf-8", errors="replace")
    shots = sorted(SHOTS.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)[:6] if SHOTS.exists() else []
    return {
        "running": running,
        "mode": _mode if running else "",
        "uptime": int(time.time() - _started) if running else 0,
        "exit_code": None if running or not _proc else _proc.returncode,
        "log": tail,
        "shots": [p.name for p in shots],
        "env_exists": ENV.exists(),
        "dump_exists": (HERE / "calendar-dump.html").exists(),
    }


# ------------------------------------------------------------------ html ---
PAGE = """<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Viza navbat</title>
<style>
:root{--bg:#f4f6fa;--card:#fff;--fg:#1a1d23;--mut:#6b7280;--acc:#1f6feb;--ok:#1a7f37;--bad:#cf222e;--bd:#e2e6ee}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e6e8ee;--mut:#8b93a5;--bd:#2a2f3a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px;display:grid;gap:16px;grid-template-columns:1fr 1fr}
@media(max-width:800px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:0 0 12px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
.full{grid-column:1/-1}
.btn{border:0;border-radius:8px;padding:10px 14px;font:inherit;font-weight:600;cursor:pointer;color:#fff;background:var(--acc)}
.btn.ok{background:var(--ok)}.btn.bad{background:var(--bad)}.btn.sec{background:var(--mut)}.btn:disabled{opacity:.45;cursor:default}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.row input{flex:1;min-width:60px}
.dot{width:12px;height:12px;border-radius:50%;background:var(--mut);display:inline-block;margin-right:6px}
.dot.on{background:var(--ok);box-shadow:0 0 0 4px rgba(26,127,55,.2)}
label{display:block;font-size:13px;color:var(--mut);margin-top:10px}
input{width:100%;padding:8px 10px;border:1px solid var(--bd);border-radius:8px;background:transparent;color:var(--fg);font:inherit}
pre{background:#0b0e14;color:#d6deeb;border-radius:8px;padding:12px;max-height:380px;overflow:auto;font:12.5px/1.4 Consolas,monospace;white-space:pre-wrap;margin:0}
.shots{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:8px}
.shots a img{width:100%;border:1px solid var(--bd);border-radius:8px}
.shots small{display:block;color:var(--mut);font-size:11px;word-break:break-all}
.msg{color:var(--mut);font-size:13px;min-height:18px}
</style></head><body><main>
<div class="card full"><h1>Yaponiya vizasi navbat</h1>
<div class="row" style="margin-top:8px"><span id="st"><span class="dot"></span>Kutish holati</span>
<span class="msg" id="msg"></span></div>
<div class="row" style="margin-top:12px">
<button class="btn ok" id="b-sched" onclick="act('start','sched')">Boshlash (START_TIME dan 5 daq oldin)</button>
<button class="btn" id="b-now" onclick="act('start','now')">Darhol boshlash</button>
<button class="btn sec" id="b-disc" onclick="act('start','discover')">Discover (faqat tekshirish)</button>
<button class="btn bad" id="b-stop" onclick="act('stop')">To'xtatish</button>
</div></div>

<div class="card"><h2>Sozlamalar (.env)</h2><form id="f" onsubmit="return save(event)">
<label>Login ID (bir nechta bo'lsa vergul bilan: a,b)<input name="LOGIN_ID" autocomplete="off"></label>
<label>Parol (bir nechta bo'lsa vergul bilan, bo'sh qolsa eskisi saqlanadi)<input name="PASSWORD" type="password" autocomplete="new-password"></label>
<label>Navbat ochiladigan vaqt (HH:MM)<input name="START_TIME" placeholder="09:00"></label>
<label>Qancha daqiqa oldin boshlash<input name="LEAD_MINUTES" placeholder="5"></label>
<label>Tekshirish oralig'i, soniya (19:00 dan 1 daq oldin boshlab)<input name="POLL_SECONDS" placeholder="1"></label>
<label>Kutish vaqtidagi yangilash oralig'i, soniya<input name="PRE_POLL_SECONDS" placeholder="20"></label>
<label>Necha daqiqadan keyin to'xtash<input name="STOP_AFTER_MINUTES" placeholder="10"></label>
<label>Ariza turi: CATEGORY_ID / EVENT_ID / PLAN_ID (12/20/19 = short stay)<div class="row">
<input name="CATEGORY_ID" placeholder="12"><input name="EVENT_ID" placeholder="20"><input name="PLAN_ID" placeholder="19"></div></label>
<label>Nechta oyni tekshirish (1 yoki 2)<input name="MONTHS" placeholder="1"></label>
<label>Slot CSS selektor (ixtiyoriy)<input name="SLOT_SELECTOR"></label>
<label>Faqat shu matnli slot (ixtiyoriy)<input name="PREFER_TEXT"></label>
<h2 style="margin-top:18px">Ariza formasi: bir nechta akkaunt bo'lsa vergul bilan, login tartibida (bo'sh qolsa skript to'xtaydi)</h2>
<label>Ism familiya (APPLICANT_NAME)<input name="APPLICANT_NAME"></label>
<label>Ism (FIRST_NAME)<input name="FIRST_NAME"></label>
<label>Familiya (LAST_NAME)<input name="LAST_NAME"></label>
<label>Ism kana (NAME_KANA, ixtiyoriy)<input name="NAME_KANA"></label>
<label>Telefon (PHONE)<input name="PHONE"></label>
<label>E-mail (EMAIL)<input name="EMAIL"></label>
<label>Pasport raqami (PASSPORT_NO)<input name="PASSPORT_NO"></label>
<label>Tug'ilgan sana (BIRTH_DATE)<input name="BIRTH_DATE"></label>
<label>Fuqarolik (NATIONALITY)<input name="NATIONALITY"></label>
<label>Manzil (ADDRESS)<input name="ADDRESS"></label>
<label>Maqsad (PURPOSE)<input name="PURPOSE"></label>
<label>Izoh (REMARKS)<input name="REMARKS"></label>
<label>Aniq maydonlar: name=qiymat;name2=qiymat2 (FORM_FIELDS)<input name="FORM_FIELDS"></label>
<label>Rozilik checkbox ini avtomatik belgilash (1/0)<input name="AGREE_CHECKBOX" placeholder="1"></label>
<label>HEADLESS (0 = brauzer ko'rinsin)<input name="HEADLESS" placeholder="0"></label>
<div class="row" style="margin-top:12px"><button class="btn">Saqlash</button><span class="msg" id="smsg"></span></div>
</form></div>

<div class="card"><h2>Oxirgi screenshotlar</h2><div class="shots" id="shots"></div></div>
<div class="card full"><h2>Log (navbat.log)</h2><pre id="log"></pre></div>
</main>
<script>
const $=s=>document.querySelector(s);
async function api(p,o){const r=await fetch(p,o);return r.json()}
async function act(a,mode){const r=await api('/api/'+a,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});$('#msg').textContent=r.msg||'';tick()}
async function save(e){e.preventDefault();const d=Object.fromEntries(new FormData($('#f')));const r=await api('/api/env',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});$('#smsg').textContent=r.msg;$('#f').PASSWORD.value='';return false}
async function loadEnv(){const e=await api('/api/env');for(const k in e){const i=$('#f').elements[k];if(i&&k!=='PASSWORD')i.value=e[k]}if(e.PASSWORD_SET)$('#f').PASSWORD.placeholder='saqlangan'}
async function tick(){const s=await api('/api/status');
$('#st').innerHTML='<span class="dot '+(s.running?'on':'')+'"></span>'+(s.running?('Ishlayapti: '+s.mode+', '+s.uptime+' s'):(s.exit_code===null?'Ishlamayapti':'To\\'xtadi (kod '+s.exit_code+')'));
['b-sched','b-now','b-disc'].forEach(id=>$('#'+id).disabled=s.running);$('#b-stop').disabled=!s.running;
const L=$('#log');const atEnd=L.scrollHeight-L.scrollTop-L.clientHeight<40;L.textContent=s.log||'(log hali yo\\'q)';if(atEnd)L.scrollTop=L.scrollHeight;
$('#shots').innerHTML=s.shots.map(n=>'<a href="/shots/'+n+'" target="_blank"><img src="/shots/'+n+'" loading="lazy"><small>'+n+'</small></a>').join('')||'<span class="msg">hali yo\\'q</span>'}
loadEnv();tick();setInterval(tick,2000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # konsolni to'ldirmasin
        pass

    def _send(self, body: bytes, ctype: str = "application/json", code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(json.dumps(obj).encode(), code=code)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._send(PAGE.encode(), "text/html; charset=utf-8")
        if path == "/api/status":
            return self._json(status())
        if path == "/api/env":
            e = read_env()
            e["PASSWORD_SET"] = bool(e.pop("PASSWORD"))
            return self._json(e)
        if path.startswith("/shots/"):
            name = Path(path[len("/shots/"):]).name  # faqat fayl nomi, papka yo'li yo'q
            f = SHOTS / name
            if f.suffix == ".png" and f.is_file():
                return self._send(f.read_bytes(), "image/png")
        self._json({"msg": "topilmadi"}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json({"msg": "noto'g'ri JSON"}, HTTPStatus.BAD_REQUEST)
        if path == "/api/start":
            return self._json({"msg": start(body.get("mode", "sched"))})
        if path == "/api/stop":
            return self._json({"msg": stop()})
        if path == "/api/env":
            write_env({k: str(v) for k, v in body.items() if k in ENV_KEYS})
            return self._json({"msg": ".env saqlandi"})
        self._json({"msg": "topilmadi"}, HTTPStatus.NOT_FOUND)


def main():
    # Docker ichida 0.0.0.0 kerak (tashqariga compose faqat 127.0.0.1 chiqaradi).
    srv = ThreadingHTTPServer((os.getenv("DASHBOARD_HOST", "127.0.0.1"), PORT), H)
    print(f"Dashboard: http://127.0.0.1:{PORT}  (CTRL+C bilan yopish)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        stop()


if __name__ == "__main__":
    main()
