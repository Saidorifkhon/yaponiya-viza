"""
Mock test: soxta lokal "elchixona sayti" va navbat.py ni unga qarshi to'liq ishlatish.

Soxta sayt haqiqiy rsvsys tuzilishini takrorlaydi (2026-10-18 da tekshirilgan qismlari):
  - /auth/login: input[name=login_id], input[name=password], button[type=submit]
  - /reservations/calendar: yashirin category/event/plan inputlar, .js_calendar_container,
    jQuery(...).trigger('change') kalendarni AJAX bilan yangilaydi, yuklanish paytida
    .js_loading_container chiqadi
  - band kun: <img src=icon_disabled.svg alt="Available"> (yolg'on alt)
  - bo'sh kun: <a class="js_move_reserve">
Ariza formasi haqiqiy saytda hali ko'rilmagan, shuning uchun bu yerda TAXMINIY forma.

Ishga tushirish (loyiha papkasidan):   python tests/mock_test.py
Har bir ssenariy vaqtinchalik papkada ishlaydi: sizning .env, navbat.log, shots/ ga tegmaydi.
"""

from __future__ import annotations

import html
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------ soxta sayt ---
class Site:
    def __init__(self, users: dict[str, str], open_after: float, slots: int = 20,
                 expire_first_calendar: bool = False, taken_dialog_first: bool = False,
                 down_seconds: float = 0, error_after_login_once: bool = False):
        self.users = users
        self.open_at = time.time() + open_after   # shu vaqtdan keyin bo'sh joylar chiqadi
        self.slots = slots
        self.sessions: dict[str, str] = {}
        self.bookings: list[dict] = []
        self.ajax_calls = 0
        self.logins: dict[str, int] = {}
        self.expire_first_calendar = expire_first_calendar
        self.expired_once = False
        self.taken_dialog_first = taken_dialog_first
        self.taken_shown = False
        self.lock = threading.Lock()
        # Shu vaqtgacha sayt hamma sahifaga 500 xato beradi (2026-10-18 17:30 dagi holat).
        self.down_until = time.time() + down_seconds
        self.down_hits = 0
        # 1-login: cookie beriladi, lekin javob 500 (2026-10-20 18:30 dagi holat).
        self.error_after_login_once = error_after_login_once


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>mock</title></head>
<body><article>{body}</article></body></html>"""

CAL_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>calendar</title>
<script>
// Kichik jQuery shim: navbat.py faqat jQuery(x).trigger('change') ni chaqiradi.
window.jQuery = function (x) {{ return {{ trigger: function () {{ loadCal(); }} }}; }};
function loadCal() {{
  var l = document.createElement('div'); l.className = 'js_loading_container'; document.body.appendChild(l);
  fetch('/ajax/reservations/calendar', {{method: 'POST'}}).then(function (r) {{ return r.text(); }}).then(function (t) {{
    setTimeout(function () {{ document.querySelector('.js_calendar_container').innerHTML = t; l.remove(); }}, 300);
  }});
}}
</script></head><body><article>
<form action="/reservations/calendar" method="post">
<input type="hidden" name="category" value="12"><input type="hidden" name="event" value="20">
<input type="hidden" name="plan" class="js-plan js_change_conditions" value="19">
</form>
<div class="js_calendar_container">{cal}</div>
<div class="js_error_dialog_message" style="display:none"></div>
</article></body></html>"""

FORM = """<form method="post" action="/reserve/confirm"><input type="hidden" name="d" value="{d}">
<table>
<tr><th>Name / Ism</th><td><input name="data[name]" class="c_form_input c_form_input_req"></td></tr>
<tr><th>Phone</th><td><input name="data[tel]" class="c_form_input c_form_input_req"></td></tr>
<tr><th>E-mail</th><td><input name="data[mail]" class="c_form_input c_form_input_req"></td></tr>
<tr><th>Passport No</th><td><input name="data[passport_no]" class="c_form_input c_form_input_req"></td></tr>
<tr><th>Remarks</th><td><textarea name="data[remarks]" class="c_form_input"></textarea></td></tr>
<tr><th>Agree</th><td><input type="checkbox" name="agree" value="1" class="c_form_input_req" required></td></tr>
</table>
<a href="/reservations/calendar" class="c_btn">Back</a>
<button type="submit">Confirm / Tasdiqlash</button></form>"""


def make_handler(site: Site):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _user(self) -> str | None:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "sid" and v in site.sessions:
                    return site.sessions[v]
            return None

        def _send(self, body: str, code: int = 200, headers: dict | None = None):
            b = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)

        def _redirect(self, to: str, headers: dict | None = None):
            h = {"Location": to}
            h.update(headers or {})
            self._send("", 302, h)

        def _form(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}

        def _cal_html(self, user: str) -> str:
            is_open = time.time() >= site.open_at
            mine = any(b["user"] == user for b in site.bookings)
            cells = []
            for day in range(19, 31):
                free = is_open and site.slots > 0 and not mine
                if free:
                    cells.append(f'<td><div class="sc_cal_date">{day}</div>'
                                 f'<a class="js_move_reserve" href="/reserve/form?d={day}">○</a></td>')
                else:
                    cells.append(f'<td><div class="sc_cal_date">{day}</div><p class="c_cal_time_cell">'
                                 f'<img src="/assets/images/user/icon_disabled.svg" alt=" Available"></p></td>')
            return '<table class="sc_cal_month"><tr>' + "".join(cells) + "</tr></table>"

        def _down(self) -> bool:
            if time.time() < site.down_until:
                site.down_hits += 1
                self._send('<html><head><title>エラーが発生しました。管理者にお問い合わせください</title></head>'
                           '<body>エラーが発生しました。管理者にお問い合わせください<a href="/">戻る</a></body></html>', 500)
                return True
            return False

        def do_GET(self):
            u = urlparse(self.path)
            if self._down():
                return
            user = self._user()
            if u.path == "/auth/login" and user:
                # Haqiqiy sayt: sessiya bor bo'lsa login sahifasi bosh sahifaga yo'naltiradi.
                return self._redirect("/")
            if u.path == "/auth/login":
                return self._send(PAGE.format(body='<form method="post" action="/auth/login">'
                    '<input name="login_id"><input name="password" type="password">'
                    '<button type="submit">Login</button></form>'))
            if u.path == "/":
                return self._send(PAGE.format(body="Home") if user else "", 200 if user else 302,
                                  None if user else {"Location": "/auth/login"})
            if not user:
                return self._redirect("/auth/login")
            if u.path == "/reservations/calendar":
                with site.lock:
                    if site.expire_first_calendar and not site.expired_once:
                        site.expired_once = True
                        for k in [k for k, v in site.sessions.items() if v == user]:
                            del site.sessions[k]
                        return self._redirect("/auth/login")
                return self._send(CAL_PAGE.format(cal=self._cal_html(user)))
            if u.path == "/reserve/form":
                d = parse_qs(u.query).get("d", ["?"])[0]
                with site.lock:
                    if site.taken_dialog_first and not site.taken_shown:
                        site.taken_shown = True
                        return self._send(PAGE.format(body="<p>Bu joy band bo'lib qoldi</p>"
                            '<div class="js_error_dialog_message">Sorry, this slot is no longer available.</div>'
                            '<a href="/reservations/calendar">Back</a>'))
                return self._send(PAGE.format(body=FORM.format(d=html.escape(d))))
            return self._send("404", 404)

        def do_POST(self):
            u = urlparse(self.path)
            if self._down():
                return
            f = self._form()
            if u.path == "/auth/login":
                lid, pw = f.get("login_id", ""), f.get("password", "")
                if site.users.get(lid) == pw:
                    sid = f"s{len(site.sessions)}{int(time.time()*1000)}"
                    site.sessions[sid] = lid
                    site.logins[lid] = site.logins.get(lid, 0) + 1
                    if site.error_after_login_once:
                        site.error_after_login_once = False
                        return self._send('<html><head><title>エラーが発生しました。管理者にお問い合わせください</title>'
                                          '</head><body>エラー</body></html>', 500,
                                          {"Set-Cookie": f"sid={sid}; Path=/"})
                    return self._redirect("/", {"Set-Cookie": f"sid={sid}; Path=/"})
                return self._send(PAGE.format(body="Login error"))
            user = self._user()
            if not user:
                return self._redirect("/auth/login")
            if u.path == "/ajax/reservations/calendar":
                site.ajax_calls += 1
                return self._send(self._cal_html(user))
            if u.path == "/reserve/confirm":
                need = ["data[name]", "data[tel]", "data[mail]", "data[passport_no]", "agree"]
                miss = [k for k in need if not f.get(k)]
                if miss:
                    return self._send(PAGE.format(body="Missing: " + ", ".join(miss)))
                hidden = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in f.items())
                return self._send(PAGE.format(body=f'<p>Please check</p><form method="post" action="/reserve/complete">{hidden}'
                    '<button type="button" onclick="history.back()">戻る</button>'
                    '<button type="submit">予約する</button></form>'))
            if u.path == "/reserve/complete":
                with site.lock:
                    site.slots -= 1
                    site.bookings.append({"user": user, **f})
                    no = len(site.bookings)
                return self._send(PAGE.format(body=f"<p>予約が完了しました。予約番号: {no}</p>"))
            return self._send("404", 404)
    return H


def start_site(site: Site) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(site))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ------------------------------------------------------------- runner ---
def run_bot(base: str, env_lines: dict[str, str], timeout: int = 120) -> tuple[int, str]:
    """navbat.py ni vaqtinchalik papkada (o'z .env, log, shots bilan) ishga tushiradi."""
    tmp = Path(tempfile.mkdtemp(prefix="viza-mock-"))
    shutil.copy(ROOT / "navbat.py", tmp / "navbat.py")
    env_lines = {"START_TIME": "00:00", "STOP_AFTER_MINUTES": "1", "HEADLESS": "1",
                 "POLL_SECONDS": "1", **env_lines}
    (tmp / ".env").write_text("\n".join(f"{k}={v}" for k, v in env_lines.items()) + "\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in env_lines}
    env["SITE_BASE"] = base
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "navbat.py", "--now"], cwd=tmp, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    log = (tmp / "navbat.log").read_text(encoding="utf-8", errors="replace") if (tmp / "navbat.log").exists() else ""
    # Yozuvlar (runs/) tekshiruvi: fayl nomlari + parol hech qayerga tushmaganmi.
    files, leak = [], []
    pw = [x for x in env_lines.get("PASSWORD", "").split(",") if x]
    for f in (tmp / "runs").rglob("*") if (tmp / "runs").exists() else []:
        if f.is_file():
            files.append(f.name)
            data = f.read_bytes()
            import zipfile, io
            blobs = [data]
            if f.suffix == ".zip":
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    blobs = [z.read(n) for n in z.namelist()]
            if any(p.encode() in b for p in pw for b in blobs):
                leak.append(f.name)
    log += "\nRUNFILES: " + " ".join(sorted(files)) + "\nPWLEAK: " + " ".join(leak) + "\n"
    shutil.rmtree(tmp, ignore_errors=True)
    return p.returncode, log + p.stdout + p.stderr


PERSON1 = {"APPLICANT_NAME": "Ali Valiyev", "PHONE": "+998901112233",
           "EMAIL": "ali@mail.uz", "PASSPORT_NO": "AA1234567"}


def scenario_single_slot_opens_later() -> list[str]:
    """Joy 6 s dan keyin ochiladi: bot kutadi, bosadi, formani to'ldiradi, yakunlaydi."""
    site = Site({"ali": "p1"}, open_after=6)
    srv, base = start_site(site)
    try:
        code, out = run_bot(base, {"LOGIN_ID": "ali", "PASSWORD": "p1", **PERSON1})
    finally:
        srv.shutdown()
    errs = []
    if len(site.bookings) != 1:
        errs.append(f"1 ta bron kutilgan, {len(site.bookings)} ta bo'ldi")
    else:
        b = site.bookings[0]
        want = {"data[name]": "Ali Valiyev", "data[tel]": "+998901112233",
                "data[mail]": "ali@mail.uz", "data[passport_no]": "AA1234567", "agree": "1"}
        for k, v in want.items():
            if b.get(k) != v:
                errs.append(f"{k}: '{b.get(k)}' != '{v}'")
    if "YAKUN" not in out:
        errs.append("logda YAKUN yo'q")
    if site.ajax_calls < 2:
        errs.append(f"AJAX yangilash ishlamadi ({site.ajax_calls} marta)")
    runfiles = out.split("RUNFILES: ")[-1].split("\n")[0].split()
    for need in ("navbat.log", "network.jsonl", "console.log"):
        if need not in runfiles:
            errs.append(f"runs/ da {need} yo'q")
    if sum(f.startswith("calendar-") for f in runfiles) < 2:
        errs.append("kalendar o'zgarishi (oldin/keyin) saqlanmadi")
    if not any(f.startswith("trace-") for f in runfiles):
        errs.append("trace zip saqlanmadi")
    if not any(f.endswith("-final.html") for f in runfiles):
        errs.append("yakuniy sahifa HTML saqlanmadi")
    if "Kalendar O'ZGARDI" not in out:
        errs.append("logda 'Kalendar O'ZGARDI' yo'q")
    leak = out.split("PWLEAK: ")[-1].split("\n")[0].strip()
    if leak:
        errs.append(f"PAROL yozuvlarga tushdi: {leak}")
    return errs + ([] if not errs else ["--- log ---\n" + out[-2500:]])


def scenario_two_accounts_both_book() -> list[str]:
    """2 akkaunt (2 odam): ikkalasi ham o'z ma'lumoti bilan alohida bron oladi."""
    site = Site({"ali": "p1", "vali": "p2"}, open_after=8)
    srv, base = start_site(site)
    try:
        code, out = run_bot(base, {
            "LOGIN_ID": "ali,vali", "PASSWORD": "p1,p2",
            "APPLICANT_NAME": "Ali Valiyev,Vali Aliyev", "PHONE": "+998901112233,+998904445566",
            "EMAIL": "ali@mail.uz,vali@mail.uz", "PASSPORT_NO": "AA1234567,AB7654321",
        }, timeout=180)
    finally:
        srv.shutdown()
    errs = []
    by_user = {b["user"]: b for b in site.bookings}
    if set(by_user) != {"ali", "vali"}:
        errs.append(f"ikkala akkaunt bron olishi kerak edi, bo'ldi: {sorted(by_user)}")
    if by_user.get("ali", {}).get("data[passport_no]") not in (None, "AA1234567"):
        errs.append("ali ga boshqa odamning pasporti yozildi")
    if by_user.get("vali", {}).get("data[passport_no]") not in (None, "AB7654321"):
        errs.append("vali ga boshqa odamning pasporti yozildi")
    if by_user.get("vali", {}).get("data[name]") not in (None, "Vali Aliyev"):
        errs.append("vali ismi noto'g'ri")
    return errs + ([] if not errs else ["--- log ---\n" + out[-3000:]])


def scenario_session_expired_relogin() -> list[str]:
    """Kalendarga birinchi kirishda sessiya tushadi: bot qayta login qilib davom etadi."""
    site = Site({"ali": "p1"}, open_after=3, expire_first_calendar=True)
    srv, base = start_site(site)
    try:
        code, out = run_bot(base, {"LOGIN_ID": "ali", "PASSWORD": "p1", **PERSON1})
    finally:
        srv.shutdown()
    errs = []
    if site.logins.get("ali", 0) < 2:
        errs.append(f"qayta login bo'lmadi (login soni {site.logins.get('ali', 0)})")
    if len(site.bookings) != 1:
        errs.append(f"qayta logindan keyin bron bo'lmadi ({len(site.bookings)})")
    return errs + ([] if not errs else ["--- log ---\n" + out[-2500:]])


def scenario_missing_field_stops() -> list[str]:
    """PASSPORT_NO bo'sh: bot formada to'xtashi kerak, noto'liq ariza YUBORMASLIGI kerak."""
    site = Site({"ali": "p1"}, open_after=2)
    srv, base = start_site(site)
    try:
        p = dict(PERSON1, PASSPORT_NO="")
        code, out = run_bot(base, {"LOGIN_ID": "ali", "PASSWORD": "p1", **p})
    finally:
        srv.shutdown()
    errs = []
    if site.bookings:
        errs.append("bo'sh maydon bilan bron yuborildi")
    if "majburiy maydon" not in out:
        errs.append("logda 'majburiy maydon' ogohlantirishi yo'q")
    if code == 0:
        errs.append("chiqish kodi 0 (to'xtaganini bildirmadi)")
    return errs + ([] if not errs else ["--- log ---\n" + out[-2500:]])


def scenario_no_slot_deadline() -> list[str]:
    """Joy umuman ochilmaydi: bot STOP_AFTER_MINUTES dan keyin o'zi to'xtaydi."""
    site = Site({"ali": "p1"}, open_after=10_000)
    srv, base = start_site(site)
    t0 = time.time()
    try:
        code, out = run_bot(base, {"LOGIN_ID": "ali", "PASSWORD": "p1", **PERSON1}, timeout=150)
    finally:
        srv.shutdown()
    took = time.time() - t0
    errs = []
    if "MUDDAT TUGADI" not in out:
        errs.append("logda 'MUDDAT TUGADI' yo'q")
    if took > 120:
        errs.append(f"to'xtash juda kech: {took:.0f} s")
    rate = site.ajax_calls / max(took, 1)
    if rate > 1.05:
        errs.append(f"so'rov tezligi juda yuqori: {rate:.2f}/s")
    return errs + ([] if not errs else ["--- log ---\n" + out[-2000:]])


def scenario_slot_taken_retry() -> list[str]:
    """Birinchi bosilgan joy boshqaga ketadi (xato oynasi): bot to'xtamay keyingi joyni oladi."""
    site = Site({"ali": "p1"}, open_after=3, taken_dialog_first=True)
    srv, base = start_site(site)
    try:
        code, out = run_bot(base, {"LOGIN_ID": "ali", "PASSWORD": "p1", **PERSON1})
    finally:
        srv.shutdown()
    errs = []
    if not site.taken_shown:
        errs.append("xato oynasi ko'rsatilmadi (test o'zi noto'g'ri)")
    if len(site.bookings) != 1:
        errs.append(f"joy ketgandan keyin qayta urinib bron olmadi ({len(site.bookings)})")
    return errs + ([] if not errs else ["--- log ---\n" + out[-2500:]])


def scenario_site_down_then_recovers() -> list[str]:
    """Sayt 25 s davomida 500 xato beradi (texnik ishlar): bot qulamay kutadi, keyin bron oladi."""
    site = Site({"ali": "p1"}, open_after=30, down_seconds=25)
    srv, base = start_site(site)
    try:
        code, out = run_bot(base, {"LOGIN_ID": "ali", "PASSWORD": "p1", "RETRY_SECONDS": "5", **PERSON1})
    finally:
        srv.shutdown()
    errs = []
    if site.down_hits < 2:
        errs.append(f"sayt xatosi sinalmadi ({site.down_hits})")
    if "SAYT JAVOB BERMAYAPTI" not in out:
        errs.append("logda 'SAYT JAVOB BERMAYAPTI' yo'q")
    if "Traceback" in out:
        errs.append("bot qulab tushdi (Traceback)")
    if len(site.bookings) != 1:
        errs.append(f"sayt tiklangach bron bo'lmadi ({len(site.bookings)})")
    return errs + ([] if not errs else ["--- log ---\n" + out[-2500:]])


def scenario_error_after_login_session_kept() -> list[str]:
    """Login bosilgach 500, lekin cookie o'rnatilgan: bot sessiyani tanib, bron oladi."""
    site = Site({"ali": "p1"}, open_after=20, error_after_login_once=True)
    srv, base = start_site(site)
    try:
        code, out = run_bot(base, {"LOGIN_ID": "ali", "PASSWORD": "p1", "RETRY_SECONDS": "2", **PERSON1})
    finally:
        srv.shutdown()
    errs = []
    if "Sessiya allaqachon ochiq" not in out:
        errs.append("bot ochiq sessiyani tanimadi")
    if "Traceback" in out:
        errs.append("bot qulab tushdi (Traceback)")
    if len(site.bookings) != 1:
        errs.append(f"bron bo'lmadi ({len(site.bookings)})")
    return errs + ([] if not errs else ["--- log ---\n" + out[-2500:]])


SCENARIOS = [
    ("Joy keyinroq ochiladi -> bron", scenario_single_slot_opens_later),
    ("2 akkaunt -> ikkalasi bron", scenario_two_accounts_both_book),
    ("Sessiya tushdi -> qayta login", scenario_session_expired_relogin),
    ("Joy boshqaga ketdi -> keyingisini oladi", scenario_slot_taken_retry),
    ("Sayt 500 xato beradi -> kutib, tiklangach bron", scenario_site_down_then_recovers),
    ("Logindan keyin 500, sessiya bor -> bron", scenario_error_after_login_session_kept),
    ("Bo'sh majburiy maydon -> to'xtaydi", scenario_missing_field_stops),
    ("Joy ochilmadi -> muddatda to'xtaydi", scenario_no_slot_deadline),
]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    only = sys.argv[1:]
    failed = 0
    for name, fn in SCENARIOS:
        if only and not any(o.lower() in fn.__name__ for o in only):
            continue
        t0 = time.time()
        try:
            errs = fn()
        except Exception as e:  # ssenariy o'zi qulasa ham hisobotga tushsin
            errs = [f"istisno: {type(e).__name__}: {e}"]
        status = "OK  " if not errs else "FAIL"
        print(f"[{status}] {name} ({time.time()-t0:.0f} s)")
        for e in errs:
            print("      " + e.replace("\n", "\n      "))
        failed += bool(errs)
    print(f"\n{'HAMMASI OK' if not failed else f'{failed} ta ssenariy FAIL'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
