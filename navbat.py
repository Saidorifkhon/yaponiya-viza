"""
Yaponiya elchixonasi (uzembassyryouji.rsvsys.jp) viza navbati uchun skript.

Ish tartibi:
  1. .env dan LOGIN_ID / PASSWORD o'qiladi, saytga kiradi.
  2. Belgilangan START_TIME dan LEAD_MINUTES daqiqa avval (yoki darhol, --now)
     kalendarni har POLL_SECONDS soniyada AJAX bilan yangilab, bo'sh joy
     chiqishini kutadi. START_TIME dan STOP_AFTER_MINUTES (10) daqiqa o'tgach
     kuzatuv to'xtaydi (sayt bloklamasin).
  3. Bo'sh joy chiqishi bilan birinchi mavjud slotni bosadi, ariza
     formasidagi majburiy maydonlarni .env dagi qiymatlardan to'ldiradi,
     tasdiqlash tugmalarini bosib arizani yuboradi.
  4. Hamma qadam navbat.log fayliga va ekranga yoziladi; har bosqichda
     screenshot saqlanadi (shots/).

Brauzer ko'rinadigan (headful) rejimda ishlaydi: agar skript bilmagan
sahifa (masalan CAPTCHA yoki to'ldirib bo'lmagan maydon) chiqsa, u to'xtab
ovoz beradi va brauzerni ochiq qoldiradi - qolganini qo'lda tugatasiz.
CAPTCHA ni skript hech qachon o'zi yechmaydi.

Ishga tushirish:  python navbat.py            (START_TIME dan LEAD_MINUTES oldin boshlaydi)
                  python navbat.py --now      (kutmasdan darhol tekshirishni boshlaydi)
                  python navbat.py --discover (login qilib kalendar HTML ini saqlaydi,
                                               hech narsa bosmaydi - selektorlarni tekshirish uchun)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import traceback
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Locator, Page, TimeoutError as PWTimeout, sync_playwright

# SITE_BASE faqat mock test uchun (tests/mock_test.py); oddiy ishda o'zgartirmang.
BASE = os.getenv("SITE_BASE", "https://uzembassyryouji.rsvsys.jp").rstrip("/")
LOGIN_URL = f"{BASE}/auth/login"
CALENDAR_URL = f"{BASE}/reservations/calendar"

HERE = Path(__file__).resolve().parent
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.FileHandler(HERE / "navbat.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("navbat")

# Ariza formasidagi maydon nomi / label / placeholder -> .env kaliti.
# Forma hali ko'rilmagan (2026-10-18 da bo'sh kun yo'q edi), shuning uchun
# moslik regex bilan: maydon nomi yoki yorlig'ida shu so'z bo'lsa, shu
# kalit yoziladi. Aniq nom ma'lum bo'lsa FORM_FIELDS orqali to'g'ridan
# to'g'ri beriladi (name=qiymat;name2=qiymat2).
FIELD_MAP = [
    (r"passport|pasport|паспорт|旅券", "PASSPORT_NO"),
    (r"tel|phone|mobile|telefon|телефон|電話", "PHONE"),
    (r"mail|e-mail|email|почта|メール", "EMAIL"),
    (r"kana|furigana|フリガナ|カナ", "NAME_KANA"),
    (r"first.?name|given|ism\b|имя|名\b", "FIRST_NAME"),
    (r"last.?name|family|surname|familiya|фамилия|姓", "LAST_NAME"),
    (r"name|ism|фио|имя|氏名", "APPLICANT_NAME"),
    (r"birth|tug|рожд|生年月日", "BIRTH_DATE"),
    (r"nation|citizen|fuqaro|гражд|国籍", "NATIONALITY"),
    (r"address|manzil|адрес|住所", "ADDRESS"),
    (r"purpose|maqsad|цель|目的", "PURPOSE"),
    (r"remark|comment|note|izoh|примеч|備考|コメント", "REMARKS"),
]
FORM_KEYS = ["APPLICANT_NAME", "FIRST_NAME", "LAST_NAME", "NAME_KANA", "PHONE", "EMAIL",
             "PASSPORT_NO", "BIRTH_DATE", "NATIONALITY", "ADDRESS", "PURPOSE", "REMARKS"]


# ----------------------------------------------------------------- config ---
def cfg() -> dict:
    load_dotenv(HERE / ".env")
    need = ["LOGIN_ID", "PASSWORD"]
    missing = [k for k in need if not os.getenv(k)]
    if missing:
        sys.exit(f".env da yetishmayapti: {', '.join(missing)}")
    # Bir nechta akkaunt: LOGIN_ID=a,b  PASSWORD=p1,p2 (vergul bilan, bir xil tartibda).
    logins = [x.strip() for x in os.environ["LOGIN_ID"].split(",") if x.strip()]
    passwords = [x.strip() for x in os.environ["PASSWORD"].split(",") if x.strip()]
    if len(logins) != len(passwords):
        sys.exit(f"LOGIN_ID da {len(logins)} ta, PASSWORD da {len(passwords)} ta qiymat - soni teng bo'lsin")
    poll = float(os.getenv("POLL_SECONDS", "1"))
    if poll < 1:
        log.warning("POLL_SECONDS=%s juda kichik, 1 ga ko'tarildi (sayt bloklamasin)", poll)
        poll = 1.0
    # FORM_FIELDS: akkauntlar orasida "||", maydonlar orasida ";":
    #   name=Ali;tel=1||name=Vali;tel=2
    form_fields = []
    for block in os.getenv("FORM_FIELDS", "").split("||"):
        d = {}
        for part in block.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                d[k.strip()] = v.strip()
        form_fields.append(d)
    return {
        "accounts": list(zip(logins, passwords)),
        "login_id": logins[0],
        "password": passwords[0],
        # Navbat ochiladigan vaqt, mahalliy vaqt, "HH:MM" (masalan 19:00).
        "start_time": os.getenv("START_TIME", "19:00"),
        "lead_minutes": int(os.getenv("LEAD_MINUTES", "5")),
        "stop_after_minutes": int(os.getenv("STOP_AFTER_MINUTES", "10")),
        "poll_seconds": poll,
        # START_TIME dan 60 s oldingacha (kutish vaqtida) yangilash oralig'i:
        # sessiyani tirik saqlash uchun yetarli, saytga yuk kam.
        "pre_poll_seconds": max(poll, float(os.getenv("PRE_POLL_SECONDS", "20"))),
        # Sayt xato berganda (500, texnik ishlar) qayta urinish oralig'i, soniya.
        "retry_seconds": max(3.0, float(os.getenv("RETRY_SECONDS", "10"))),
        # Ariza turi (kalendar formasidagi yashirin category/event/plan).
        # Standart: 12/20/19 = "VISA Application for short stay (Applicant)".
        "category_id": os.getenv("CATEGORY_ID", "12"),
        "event_id": os.getenv("EVENT_ID", "20"),
        "plan_id": os.getenv("PLAN_ID", "19"),
        # Nechta oyni tekshirish (1 = faqat ko'rinib turgan oy, 2 = keyingi oy ham).
        "months": max(1, int(os.getenv("MONTHS", "1"))),
        # Agar aniq CSS selektor bilsangiz (--discover natijasidan), shu yerga yozing.
        "slot_selector": os.getenv("SLOT_SELECTOR", ""),
        # Faqat shu matnni o'z ichiga olgan slotni tanlash (ixtiyoriy, masalan sana).
        "prefer_text": os.getenv("PREFER_TEXT", ""),
        # Tasdiqlash tugmalari matni (regex, katta-kichik harf farqsiz).
        "confirm_regex": os.getenv(
            "CONFIRM_REGEX",
            r"確認|予約する|予約を確定|次へ|送信|はい|Confirm|Submit|Book|Reserve|Next|Yes|Tasdiq|Yubor|Band|Davom|Подтвер|Отправ|Далее",
        ),
        "form_fields": form_fields,
        # Har bir akkaunt (odam) uchun alohida: PHONE=+998901,+998902 (LOGIN_ID tartibida).
        # Bitta qiymat bo'lsa hammaga bir xil ishlatiladi.
        "form_raw": {k: os.getenv(k, "").strip() for k in FORM_KEYS},
        "form_values": {},
        # 1 = majburiy checkbox (rozilik) ni avtomatik belgilash.
        "agree_checkbox": os.getenv("AGREE_CHECKBOX", "1") == "1",
        "headless": os.getenv("HEADLESS", "0") == "1",
    }


# --------------------------------------------------------------- recorder ---
class Recorder:
    """Har bir ishga tushirish uchun runs/<vaqt>-<login>/ papkasiga hamma narsani yozadi:
      navbat.log       - shu urinishning logi
      NNN-<nom>.html   - har bir qadamdagi sahifa HTML (page.content())
      NNN-<nom>.png    - o'sha paytdagi screenshot
      calendar-*.html  - kalendar har safar O'ZGARGANDA (bo'sh joy chiqishi shu yerda ko'rinadi)
      network.jsonl    - sayt so'rovlari: vaqt, metod, url, status (body va parol yozilmaydi)
      console.log      - brauzer konsoli va JS xatolari
      trace-N.zip      - Playwright trace: `playwright show-trace trace-1.zip` bilan qadamma-qadam
    Login paytida trace to'xtatiladi - parol hech qayerga yozilmaydi."""

    def __init__(self, ctx, login_id: str):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", login_id)[:40]
        self.dir = HERE / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}-{safe}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ctx = ctx
        self.n = 0
        self.chunk = 0
        self.started = False
        self.tracing = False
        self.cal_hash = ""
        self.handler = logging.FileHandler(self.dir / "navbat.log", encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.getLogger().addHandler(self.handler)
        self.net = open(self.dir / "network.jsonl", "a", encoding="utf-8")
        self.con = open(self.dir / "console.log", "a", encoding="utf-8")
        ctx.on("response", self._on_response)
        ctx.on("console", lambda m: self._w(self.con, f"{m.type}: {m.text}"))
        ctx.on("weberror", lambda e: self._w(self.con, f"PAGEERROR: {e.error}"))
        log.info("Yozuvlar papkasi: %s", self.dir)

    @staticmethod
    def _w(f, line: str) -> None:
        try:
            f.write(f"{datetime.now():%H:%M:%S.%f}"[:-3] + " " + line + "\n")
            f.flush()
        except Exception:
            pass

    def _on_response(self, r) -> None:
        try:
            rt = r.request.resource_type
            if rt in ("image", "font", "stylesheet", "media"):
                return
            self.net.write(json.dumps({"t": f"{datetime.now():%H:%M:%S.%f}"[:-3], "m": r.request.method,
                                       "url": r.url, "st": r.status, "type": rt}, ensure_ascii=False) + "\n")
            self.net.flush()
        except Exception:
            pass

    def trace_on(self) -> None:
        try:
            if not self.started:
                self.ctx.tracing.start(screenshots=True, snapshots=True)
                self.started = True
            else:
                self.ctx.tracing.start_chunk()
            self.tracing = True
        except Exception as e:
            log.warning("trace boshlanmadi: %s", e)

    def trace_off(self) -> None:
        if not self.tracing:
            return
        self.chunk += 1
        try:
            self.ctx.tracing.stop_chunk(path=str(self.dir / f"trace-{self.chunk}.zip"))
        except Exception as e:
            log.warning("trace saqlanmadi: %s", e)
        self.tracing = False

    def snap(self, page: Page, name: str, png: Path | None = None) -> None:
        self.n += 1
        base = self.dir / f"{self.n:03d}-{name}"
        try:
            base.with_suffix(".html").write_text(f"<!-- {page.url} -->\n" + page.content(), encoding="utf-8")
        except Exception as e:
            log.warning("HTML saqlanmadi: %s", e)
        try:
            if png and png.exists():
                base.with_suffix(".png").write_bytes(png.read_bytes())
        except Exception:
            pass

    def calendar(self, page: Page) -> None:
        """Kalendar HTML i o'zgargan bo'lsa saqlaydi (bir xil holat qayta yozilmaydi)."""
        try:
            h = page.locator(".js_calendar_container").first.inner_html(timeout=2000)
        except Exception:
            return
        # Konteynerdagi forma har yangilanishda o'zgaradi (modaal_<id>, _Token, action;
        # 2026-10-18 da ko'rilgan). Taqqoslash faqat kalendar JADVALI bo'yicha;
        # saqlanadigan fayl esa to'liq konteyner.
        tables = re.findall(r"<table[^>]*sc_cal[^>]*>.*?</table>", h, re.S)
        key = "".join(tables) if tables else h
        d = hashlib.sha1(key.encode()).hexdigest()
        if d == self.cal_hash:
            return
        first = not self.cal_hash
        self.cal_hash = d
        f = self.dir / f"calendar-{datetime.now():%H%M%S}.html"
        f.write_text(h, encoding="utf-8")
        log.info("Kalendar %s: %s (js_move_reserve: %d)", "saqlandi" if first else "O'ZGARDI",
                 f.name, h.count("js_move_reserve"))

    def finish(self) -> None:
        self.trace_off()
        try:
            if self.started:
                self.ctx.tracing.stop()
        except Exception:
            pass
        for f in (self.net, self.con):
            try:
                f.close()
            except Exception:
                pass
        logging.getLogger().removeHandler(self.handler)
        self.handler.close()


REC: Recorder | None = None


# ---------------------------------------------------------------- helpers ---
def shot(page: Page, name: str) -> None:
    p = SHOTS / f"{datetime.now():%Y%m%d-%H%M%S}-{name}.png"
    try:
        page.screenshot(path=str(p), full_page=True)
    except Exception as e:  # screenshot xatosi asosiy ishni to'xtatmasin
        log.warning("screenshot olinmadi: %s", e)
    if REC:
        REC.snap(page, name, p)


def beep(times: int = 5) -> None:
    try:
        import winsound

        for _ in range(times):
            winsound.Beep(1200, 400)
            time.sleep(0.2)
    except Exception:
        print("\a" * times)


def opens_at(start_time: str) -> datetime:
    """Bugungi (yoki o'tib ketgan bo'lsa ertangi) START_TIME."""
    hh, mm = (int(x) for x in start_time.split(":"))
    now = datetime.now()
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t


def wait_until(start_time: str, lead_minutes: int) -> datetime:
    """LEAD_MINUTES oldin qaytadi; navbat ochiladigan vaqtni qaytaradi."""
    now = datetime.now()
    opens = opens_at(start_time)
    target = opens - timedelta(minutes=lead_minutes)
    if target < now:
        if opens < now:
            opens += timedelta(days=1)
            target += timedelta(days=1)
        else:
            log.info("START_TIME ga %d daqiqadan kam qoldi, darhol boshlayman", lead_minutes)
            return opens
    log.info("Kutish: %s da tekshirish boshlanadi (navbat %s da ochiladi)",
             target.strftime("%Y-%m-%d %H:%M:%S"), opens.strftime("%H:%M"))
    while True:
        left = (target - datetime.now()).total_seconds()
        if left <= 0:
            return opens
        time.sleep(min(left, 30))


def page_text(page: Page) -> str:
    try:
        if page.locator("article").count():
            return page.locator("article").first.inner_text(timeout=3000)
        return page.inner_text("body", timeout=3000)
    except Exception:
        return ""


# ------------------------------------------------------------------- steps ---
def login(page: Page, c: dict, until: datetime | None = None) -> None:
    """Login. Sayt xato bersa (500, texnik ishlar, timeout) `until` gacha har
    RETRY_SECONDS da qayta urinadi. Noto'g'ri parol (forma qaytib chiqsa) - darhol chiqadi.
    2026-10-18 17:30 da sayt hamma uchun 500 "エラーが発生しました" qaytargan."""
    if REC:
        REC.trace_off()  # parol trace ga tushmasin
    attempt = 0
    while True:
        attempt += 1
        log.info("Login sahifasi ochilmoqda%s", f" ({attempt}-urinish)" if attempt > 1 else "")
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=20000)
            if not page.locator("input[name='login_id']").count():
                raise RuntimeError(f"login formasi yo'q, sahifa: {page.title()[:80]}")
            page.fill("input[name='login_id']", c["login_id"])
            page.fill("input[name='password']", c["password"])
            page.click("button[type='submit']")
            page.wait_for_load_state("domcontentloaded")
            if "/auth/login" in page.url and page.locator("input[name='login_id']").count():
                shot(page, "login-fail")
                sys.exit(f"Login bo'lmadi (login yoki parol xato?). Sahifa: {page_text(page)[:500]}")
            if "/auth/login" in page.url or "エラー" in page.title():
                raise RuntimeError(f"logindan keyin xato sahifa: {page.title()[:80]}")
            break
        except SystemExit:
            raise
        except Exception as e:
            if attempt == 1 or attempt % 6 == 0:
                shot(page, "site-error")
            if until is None or datetime.now() >= until:
                log.error("Login bo'lmadi, urinishlar tugadi: %s", e)
                raise
            log.warning("SAYT JAVOB BERMAYAPTI (%d-urinish): %s - %d s dan keyin qayta", attempt,
                        str(e).splitlines()[0][:200], c["retry_seconds"])
            time.sleep(c["retry_seconds"])
    log.info("Login OK -> %s", page.url)
    shot(page, "login-ok")
    if REC:
        REC.trace_on()


def open_calendar(page: Page, c: dict) -> bool:
    """Kalendar sahifasini ochadi, kerak bo'lsa ariza turini almashtiradi.
    False = login sahifasiga tushib qoldi (sessiya tugagan)."""
    page.goto(CALENDAR_URL, wait_until="domcontentloaded", timeout=15000)
    if "/auth/login" in page.url:
        return False
    page.wait_for_selector(".js_calendar_container table", timeout=10000)
    cur = page.evaluate(
        "() => [['category','event','plan'].map(n => (document.querySelector('input[type=hidden][name='+n+']')||{}).value)][0]"
    )
    want = [c["category_id"], c["event_id"], c["plan_id"]]
    if cur != want:
        # Kalendar formasidagi yashirin maydonlarni almashtirib, calendar.js
        # ning 'change' hodisasini uyg'otamiz (AJAX bilan qayta yuklaydi).
        # Tekshirildi 2026-10-18: change -> loadCalendar(form.serializeArray()).
        log.info("Ariza turi almashtirilmoqda: %s -> %s", cur, want)
        page.evaluate(
            """([cat, ev, pl]) => {
                const f = document.querySelector('input[type=hidden][name=plan]').form;
                f.querySelector('input[name=category]').value = cat;
                f.querySelector('input[name=event]').value = ev;
                f.querySelector('input[name=plan]').value = pl;
                jQuery(f.querySelector('input[name=plan]')).trigger('change');
            }""",
            want,
        )
        wait_calendar_loaded(page)
    return True


def wait_calendar_loaded(page: Page, timeout: int = 8000) -> None:
    """AJAX yuklanish tugashini kutadi (loading overlay yo'qolguncha)."""
    try:
        page.wait_for_function(
            "() => !document.querySelector('.js_loading_container') && document.querySelector('.js_calendar_container table')",
            timeout=timeout,
        )
    except PWTimeout:
        log.warning("Kalendar %d ms da yangilanmadi", timeout)


def reload_calendar(page: Page) -> None:
    """Sahifani to'liq yuklamasdan kalendarni AJAX bilan yangilaydi."""
    page.evaluate("() => jQuery('input[type=hidden][name=plan]').trigger('change')")
    time.sleep(0.3)
    wait_calendar_loaded(page)


def next_month(page: Page) -> None:
    page.evaluate("() => { const a=[...document.querySelectorAll('a')].find(x=>/次月/.test(x.textContent)); if(a) a.click(); }")
    time.sleep(0.3)
    wait_calendar_loaded(page)


def find_free_slot(page: Page, c: dict) -> Locator | None:
    """Birinchi bo'sh slot elementini qaytaradi yoki None."""
    if c["slot_selector"]:
        loc = page.locator(c["slot_selector"])
        if c["prefer_text"]:
            loc = loc.filter(has_text=c["prefer_text"])
        if loc.count() > 0:
            return loc.first
        return None

    # Sayt (rsvsys, calendar.js) tekshirildi 2026-10-18:
    #  - kalendar AJAX bilan .js_calendar_container ichiga yuklanadi
    #    (table.sc_cal_month);
    #  - band kun: <p class=c_cal_time_cell><img src=".../icon_disabled.svg">
    #    (alt matni yolg'onchi "Available" deb turadi, unga ishonmaymiz);
    #  - bo'sh kun / vaqt: <a class="js_move_reserve" href=... data-url=...>
    #    havolasi, bosilganda calendar.js zaxirani tekshirib (interval-stock)
    #    ariza formasiga o'tadi (changeLocation).
    links = page.locator("a.js_move_reserve")
    n = links.count()
    candidates = []
    for i in range(n):
        a = links.nth(i)
        try:
            cell = a.locator("xpath=ancestor::td[1]")
            has_cell = cell.count() > 0
            if has_cell and cell.locator("img[src*='icon_disabled']").count():
                continue
            txt = (cell.inner_text(timeout=1000) if has_cell else a.inner_text(timeout=1000)).strip().replace("\n", " ")
        except Exception:
            continue
        if c["prefer_text"] and c["prefer_text"] not in txt:
            continue
        candidates.append((i, a, txt[:60]))
    if not candidates:
        return None
    _, a, txt = candidates[0]
    log.info("Bo'sh slot topildi (%d nomzod): %s", len(candidates), txt)
    return a


def field_keys(el: Locator) -> list[str]:
    """Maydonning name/id/placeholder/label matniga mos .env kalitlari (FIELD_MAP tartibida).
    Bir nechta mos kelishi mumkin ("Name / Ism" -> FIRST_NAME va APPLICANT_NAME):
    to'ldirishda qiymati bor birinchisi olinadi."""
    try:
        desc = el.evaluate(
            """e => {
                const lab = e.id ? document.querySelector('label[for="'+e.id+'"]') : null;
                const wrap = e.closest('tr, .c_form_item, .el_form_item, dl, li, div');
                const th = wrap ? (wrap.querySelector('th, dt, label, .c_form_label') || {}).textContent : '';
                return [e.name, e.id, e.placeholder, lab ? lab.textContent : '', th || ''].join(' ');
            }"""
        )
    except Exception:
        desc = ""
    return [key for rx, key in FIELD_MAP if re.search(rx, desc, re.I)]


def fill_form(page: Page, c: dict) -> list[str]:
    """Ko'rinib turgan bo'sh maydonlarni .env dan to'ldiradi.
    Qaytaradi: to'ldirib bo'lmagan majburiy maydonlar ro'yxati."""
    unfilled: list[str] = []
    fields = page.locator("input:visible, textarea:visible, select:visible")
    for i in range(fields.count()):
        el = fields.nth(i)
        try:
            typ = (el.get_attribute("type") or "text").lower()
            name = el.get_attribute("name") or ""
            cls = el.get_attribute("class") or ""
        except Exception:
            continue
        if typ in ("hidden", "submit", "button", "image", "password") or name in ("login_id",):
            continue
        required = "c_form_input_req" in cls or el.evaluate("e => e.required")
        if typ == "checkbox":
            if required and c["agree_checkbox"] and not el.is_checked():
                el.check()
                log.info("Checkbox belgilandi: %s", name)
            continue
        if typ == "radio":
            continue
        try:
            current = (el.input_value() or "").strip()
        except Exception:
            current = ""
        if current:
            continue
        value = c["form_fields_mine"].get(name, "")
        if not value:
            key = ""
            for k in field_keys(el):
                if c["form_values"].get(k):
                    key, value = k, c["form_values"][k]
                    break
        if value:
            try:
                if el.evaluate("e => e.tagName") == "SELECT":
                    try:
                        el.select_option(label=value)
                    except Exception:
                        el.select_option(value=value)
                else:
                    el.fill(value)
                log.info("To'ldirildi: %s", name or key)
                continue
            except Exception as e:
                log.warning("%s to'ldirilmadi: %s", name, e)
        if required:
            unfilled.append(name or "?")
    return unfilled


def click_dialog_yes(page: Page) -> bool:
    """rsvsys tasdiqlash oynasi (js_confirm_dialog) chiqsa 'yes' ni bosadi."""
    yes = page.locator(".modaal-wrapper :is(button,a,input[type=button],input[type=submit]):visible, .ui-dialog :is(button,a):visible, [role=dialog] :is(button,a):visible").filter(
        has_text=re.compile(r"はい|OK|Yes|Ha\b|Да", re.I)
    )
    if yes.count():
        log.info("Dialog: '%s' bosilmoqda", yes.first.inner_text().strip())
        yes.first.click()
        time.sleep(0.5)
        return True
    return False


def error_dialog_text(page: Page) -> str:
    err = page.locator(".js_error_dialog_message:visible, .js_error_dialog_message_including_cancel_wait:visible")
    if err.count():
        return err.first.inner_text().strip()
    return ""


# Joy boshqaga ketib qolganini bildiruvchi xato matni (taxminiy, ko'p tilda).
TAKEN_RX = r"no longer|not available|already|fully booked|band bo|qolmadi|満|定員|空きがありません|予約できません|занят|нет мест"


def click_confirms(page: Page, c: dict, max_steps: int = 8) -> bool | None:
    """Formani to'ldirib, ketma-ket tasdiqlash tugmalarini bosadi.
    True = yakuniy sahifaga yetdi."""
    rx = re.compile(c["confirm_regex"], re.I)
    rx_no = re.compile(r"cancel|bekor|back|return|no|yo'q|戻る|キャンセル|いいえ|отмен|назад|нет", re.I)
    for step in range(max_steps):
        page.wait_for_load_state("domcontentloaded")
        time.sleep(0.4)
        shot(page, f"step{step}")
        body = page_text(page)

        if re.search(r"captcha|recaptcha|hcaptcha|画像認証", body, re.I) or page.locator("iframe[src*='captcha']").count():
            log.warning("CAPTCHA ko'rindi - skript uni yechmaydi. Qo'lda davom eting.")
            return False

        if re.search(r"予約が完了|完了しました|受付番号|予約番号|Reservation (is )?complete|Thank you|qabul qilindi|Bron(lov)? (qabul|yakun)|Бронирование завершено", body, re.I):
            log.info("YAKUN: ariza qabul qilindi. Sahifa matni:\n%s", body[:1500])
            return True

        err = error_dialog_text(page)
        if err:
            log.warning("Sayt xato oynasi: %s", err[:300])
            if re.search(TAKEN_RX, err, re.I):
                return None  # joyni boshqa olib qo'ydi: chaqiruvchi kuzatuvni davom ettiradi
            return False

        if click_dialog_yes(page):
            continue

        # Kun bosilgandan keyin vaqt slotlari jadvali chiqsa, shu yerda ham
        # birinchi bo'sh slotni bosamiz.
        slot = find_free_slot(page, c)
        if slot is not None:
            log.info("Vaqt sloti bosilmoqda")
            slot.click()
            time.sleep(0.5)
            continue

        unfilled = fill_form(page, c)
        if unfilled:
            log.warning("Bo'sh majburiy maydon(lar): %s - .env ga qiymat qo'shing yoki qo'lda to'ldiring.", ", ".join(unfilled))
            return False

        btn = page.locator("button:visible, input[type=submit]:visible, a.c_btn:visible").filter(has_text=rx).filter(has_not_text=rx_no)
        if btn.count() == 0:
            btn = page.locator("input[type=submit]:visible").filter(has_not_text=rx_no)
        if btn.count() == 0:
            log.warning("Tasdiqlash tugmasi topilmadi (%s). Qo'lda davom eting.", page.url)
            return False
        b = btn.first
        txt = b.get_attribute("value") if b.evaluate("e=>e.tagName") == "INPUT" else b.inner_text().strip()
        log.info("Bosilmoqda: '%s'", txt)
        b.click()
        time.sleep(0.5)
    log.warning("%d qadamdan keyin ham yakun sahifasi chiqmadi.", max_steps)
    return False


def poll_and_book(page: Page, c: dict, deadline: datetime, opens: datetime) -> bool | None:
    """None = muddat tugadi (bo'sh joy chiqmadi); True/False = click_confirms natijasi.
    opens - 60 s gacha har PRE_POLL_SECONDS, keyin har POLL_SECONDS yangilanadi."""
    log.info("Kalendar kuzatuvi boshlandi: %s gacha har %.0f s, so'ng har %.1f s, %s gacha",
             (opens - timedelta(seconds=60)).strftime("%H:%M:%S"), c["pre_poll_seconds"],
             c["poll_seconds"], deadline.strftime("%H:%M:%S"))
    n = 0
    need_goto = True
    while True:
        if datetime.now() > deadline:
            log.warning("MUDDAT TUGADI: %s gacha bo'sh joy chiqmadi, kuzatuv to'xtatildi (%d urinish).", deadline.strftime("%H:%M"), n)
            return None
        n += 1
        if n % 60 == 0:
            need_goto = True  # daqiqada bir marta sahifani to'liq qayta ochamiz (AJAX jim xato bersa ham)
        try:
            if need_goto:
                if not open_calendar(page, c):
                    log.warning("Sessiya tushib ketdi, qayta login")
                    login(page, c, deadline)
                    continue
                need_goto = False
            else:
                reload_calendar(page)
                if "/auth/login" in page.url:
                    log.warning("Sessiya tushib ketdi, qayta login")
                    login(page, c, deadline)
                    need_goto = True
                    continue
        except Exception as e:  # timeout, 500 xato sahifa (jQuery yo'q) va h.k. - to'xtamaymiz
            log.warning("Kalendar ochilmadi (%s: %s), qayta urinaman",
                        type(e).__name__, str(e).splitlines()[0][:150] if str(e) else "")
            if n % 10 == 1:
                shot(page, "calendar-error")
            need_goto = True
            try:
                site_down = "エラー" in page.title()
            except Exception:
                site_down = True
            time.sleep(c["retry_seconds"] if site_down else c["poll_seconds"])
            continue

        if REC:
            REC.calendar(page)
        slot = None
        for m in range(c["months"]):
            if m:
                next_month(page)
            slot = find_free_slot(page, c)
            if slot is not None:
                break
        if slot is None:
            if n == 1 or n % 30 == 0:
                log.info("Hali bo'sh joy yo'q (%d-tekshiruv, kalendarda %d kun, %s)", n,
                         page.locator(".js_calendar_container td").count(), page.url.rsplit("/", 1)[-1])
            if c["months"] > 1:
                need_goto = True  # keyingi oyga o'tgan edik, boshidan ochamiz
            hot = datetime.now() >= opens - timedelta(seconds=60)
            time.sleep(c["poll_seconds"] if hot else c["pre_poll_seconds"])
            continue

        shot(page, "slot-found")
        beep(2)
        slot.click()
        try:
            page.wait_for_function(
                "() => !location.pathname.endsWith('/reservations/calendar') || document.querySelector('.js_error_dialog_message, .js_error_dialog_message_including_cancel_wait')",
                timeout=10000,
            )
        except PWTimeout:
            log.warning("Slot bosildi, lekin 10 s da sahifa o'zgarmadi")
        err = error_dialog_text(page)
        if err and "/reservations/calendar" in page.url:
            # Joy ulgurmasdan band bo'ldi: oynani yopib kuzatuvni davom ettiramiz.
            log.warning("Slot band bo'lib qoldi: %s - davom etaman", err[:200])
            page.keyboard.press("Escape")
            need_goto = True
            continue
        res = click_confirms(page, c)
        if res is None:
            log.warning("Joy boshqaga ketdi - kalendarga qaytib, keyingi joyni qidiraman")
            need_goto = True
            continue
        return res


def discover(page: Page, c: dict) -> None:
    open_calendar(page, c)
    out = HERE / "calendar-dump.html"
    out.write_text(page.content(), encoding="utf-8")
    shot(page, "discover")
    log.info("Kalendar HTML saqlandi: %s . Hech narsa bosilmadi.", out)


# -------------------------------------------------------------------- main ---
def main() -> None:
    global log
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true", help="START_TIME ni kutmasdan darhol boshlash")
    ap.add_argument("--discover", action="store_true", help="login + kalendar HTML dump, bosmaydi")
    ap.add_argument("--account", type=int, default=-1, help="(ichki) faqat shu raqamli akkaunt bilan ishlash")
    a = ap.parse_args()
    c = cfg()

    if a.account < 0 and len(c["accounts"]) > 1 and not a.discover:
        # Har bir akkaunt uchun alohida jarayon: hammasi bir vaqtda kuzatadi,
        # har biri o'z navbatini olguncha mustaqil ishlaydi (boshqa odamlar uchun).
        import subprocess
        procs = []
        for i, (lid, _) in enumerate(c["accounts"]):
            args = [sys.executable, str(HERE / "navbat.py"), "--account", str(i)] + (["--now"] if a.now else [])
            log.info("Akkaunt %d (%s) uchun jarayon ishga tushdi", i, lid)
            procs.append(subprocess.Popen(args, cwd=str(HERE)))
            time.sleep(2)  # loginlar bir vaqtda ketmasin
        try:
            for p in procs:
                p.wait()
        except KeyboardInterrupt:
            for p in procs:
                p.terminate()
        return
    idx = max(a.account, 0)
    if a.account >= 0:
        if a.account >= len(c["accounts"]):
            sys.exit(f"--account {a.account}: bunday akkaunt yo'q")
        c["login_id"], c["password"] = c["accounts"][a.account]
        log = logging.getLogger(f"navbat[{c['login_id']}]")
    for k, raw in c["form_raw"].items():
        parts = [x.strip() for x in raw.split(",")]
        c["form_values"][k] = parts[idx] if len(parts) > 1 and idx < len(parts) else (raw if len(parts) == 1 else "")
    ff = c["form_fields"]
    c["form_fields_mine"] = ff[idx] if idx < len(ff) else {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=c["headless"])
        ctx = browser.new_context(locale="en-US")
        page = ctx.new_page()
        page.set_default_timeout(10000)

        if a.discover:
            login(page, c)
            discover(page, c)
            browser.close()
            return

        if a.now:
            opens = max(opens_at(c["start_time"]), datetime.now())
        else:
            opens = wait_until(c["start_time"], c["lead_minutes"])
        deadline = opens + timedelta(minutes=c["stop_after_minutes"])
        global REC
        REC = Recorder(ctx, c["login_id"])
        login(page, c, deadline)  # 18:55 da; sayt xato bersa 19:10 gacha qayta urinadi

        try:
            ok = poll_and_book(page, c, deadline, opens)
        except Exception:
            log.error("KUTILMAGAN XATO:\n%s", traceback.format_exc())
            shot(page, "crash")
            REC.finish()
            raise
        shot(page, "final")
        if ok:
            log.info("Tayyor. Brauzer 10 daqiqa ochiq qoladi - natijani tekshiring.")
            beep(3)
            if not c["headless"]:
                time.sleep(600)
        elif ok is None:
            beep(3)
        else:
            log.warning("Skript to'xtadi, qolganini brauzerda qo'lda tugating. Brauzer ochiq qoladi (CTRL+C bilan yoping).")
            beep(8)
            if c["headless"]:  # ko'rinmas brauzerda qo'lda davom etib bo'lmaydi
                REC.finish()
                browser.close()
                sys.exit(2)
            try:
                while not page.is_closed():
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        REC.finish()
        browser.close()


if __name__ == "__main__":
    main()
