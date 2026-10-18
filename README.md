# Yaponiya vizasi navbat (yaponiya-viza)

Sayt: https://uzembassyryouji.rsvsys.jp/ . Login'dan keyin "Book an Appointment"
havolasi `/reservations/calendar` ga olib boradi. Kalendar AJAX bilan keladi:
band kun = `icon_disabled.svg` rasmi (alt matni "Available" deb yolg'on
aytadi), bo'sh kun/vaqt = `a.js_move_reserve` havolasi. Skript shu havola
chiqishi bilan bosadi.

## O'rnatish (bir marta)

```
cd D:\Loyihalar\yaponiya-viza
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
```

`.env` ni o'zingiz to'ldirasiz (login, parol, ariza maydonlari). Git ga tushmaydi.

## Docker (tavsiya)

```
cd D:\Loyihalar\yaponiya-viza
docker compose up -d --build
```

- Dashboard: http://127.0.0.1:8106
- Brauzerni ko'rish va qo'lda davom etish: http://127.0.0.1:8107/vnc.html -> Connect.
  Skript to'xtab ovoz bersa (Docker'da ovoz yo'q, logda ko'rinadi), shu oynada tugatasiz.
- Kod, `.env`, log, screenshotlar host papkada qoladi. Vaqt: Asia/Tashkent.
- Dockersiz `python dashboard.py` 8108 portda ochiladi (http://127.0.0.1:8108). Ikkalasida bir vaqtda "Boshlash" bosmang: bir xil .env va log.
- To'xtatish: `docker compose down`.

## Dashboard (Dockersiz)

```
venv\Scripts\activate
python dashboard.py
```

Brauzerda http://127.0.0.1:8106 ni oching (faqat shu kompyuterdan ochiladi).

- Sozlamalar: Login ID, parol, START_TIME, ariza turi, forma maydonlari -> Saqlash.
- "Discover (faqat tekshirish)": login qilib kalendar HTML ini
  `calendar-dump.html` ga saqlaydi, hech narsa bosmaydi.
- "Boshlash": START_TIME dan LEAD_MINUTES (5) daqiqa oldin kuzatuvni boshlaydi.
- "Darhol boshlash": kutmasdan hozir boshlaydi.
- "To'xtatish": skriptni o'ldiradi.
- Pastda jonli log va oxirgi screenshotlar.

Konsoldan ham ishlaydi: `python navbat.py [--now|--discover]`.

## Navbat kuni (19:00 ochiladi) - aniq qadamlar

1. 18:45: `venv\Scripts\activate`, `python dashboard.py`, brauzerda
   http://127.0.0.1:8106 .
2. Sozlamalarda START_TIME=19:00, LEAD_MINUTES=5, POLL_SECONDS=1,
   STOP_AFTER_MINUTES=10, ariza maydonlari to'ldirilgan -> Saqlash.
3. "Boshlash" ni bosing. Skript 18:55 da login qilib kalendar sahifasida
   qoladi, 18:59 gacha har 20 s (PRE_POLL_SECONDS) AJAX bilan yangilab
   sessiyani tirik saqlaydi, 18:59 dan 19:10 gacha har soniya yangilaydi
   (bitta yangilanish ~2.5 s, shuning uchun real tezlik ~3 s da bir marta).
4. Bo'sh joy chiqishi bilan 2 marta ovoz beradi, slotni bosadi, formani
   to'ldiradi, tasdiqlaydi. Yakunda 3 marta ovoz; brauzer 10 daqiqa ochiq qoladi.
5. 8 marta ovoz = skript to'xtadi (CAPTCHA, bo'sh majburiy maydon yoki
   noma'lum tugma). Brauzer ochiq, qolganini qo'lda tugating.

## Bir nechta akkaunt

`.env` da vergul bilan, bir xil tartibda (parolda vergul bo'lmasin):

```
LOGIN_ID=login1,login2
PASSWORD=parol1,parol2
```

Har bir akkaunt (har xil odam) uchun alohida brauzer ochiladi, hammasi bir
vaqtda kuzatadi. Har biri o'z navbatini olguncha mustaqil ishlaydi: biri
olsa ham qolganlari davom etadi. Ariza maydonlari ham vergul bilan, LOGIN_ID
tartibida: `APPLICANT_NAME=Ali Valiyev,Vali Aliyev`, `PHONE=+998901112233,+998904445566`.

## Ariza turi

Standart 12/20/19 = "VISA Application for short stay (Applicant)". COE yoki
Government Documents kerak bo'lsa saytda shu turni tanlab, sahifa HTML dagi
yashirin `category/event/plan` qiymatlarini `.env` ga yozing (CATEGORY_ID,
EVENT_ID, PLAN_ID). Skript kalendarni shu tur bilan qayta yuklaydi.

## Ariza formasi

Slot bosilgandan keyingi forma hali ko'rilmagan (2026-10-18 da bo'sh kun
yo'q edi). Skript formadagi maydonlarni nomi/yorlig'iga qarab `.env`
kalitlariga moslaydi (passport -> PASSPORT_NO, phone -> PHONE, name ->
APPLICANT_NAME va h.k., ro'yxat navbat.py dagi FIELD_MAP). Aniq nom ma'lum
bo'lsa `FORM_FIELDS=name=qiymat;name2=qiymat2`. Majburiy rozilik checkbox
AGREE_CHECKBOX=1 bo'lsa avtomatik belgilanadi. To'ldirib bo'lmagan majburiy
maydon qolsa skript to'xtab ovoz beradi.

## Skript qayerda to'xtaydi (qo'lda tugatish kerak)

- CAPTCHA chiqsa (skript uni yechmaydi).
- Formada to'ldirib bo'lmagan majburiy maydon bo'lsa.
- Tasdiqlash tugmasini topa olmasa yoki sayt xato oynasi chiqarsa.
- START_TIME + STOP_AFTER_MINUTES o'tsa (bloklanmaslik uchun).

Sayt xato bersa (500 "エラーが発生しました", texnik ishlar, timeout) bot TO'XTAMAYDI:
login va kalendarni har RETRY_SECONDS (10) da qayta urinadi, 19:10 gacha.
Logda "SAYT JAVOB BERMAYAPTI" deb yoziladi. 2026-10-18 17:30 da sayt shunday xato bergan.

Slot bosilganda "band bo'lib qoldi" xatosi chiqsa, kuzatuv davom etadi.

## Mock test (navbat kunidan oldin)

```
python tests/mock_test.py
```

Soxta lokal sayt ustida 6 ssenariy: joy keyin ochiladi, 2 akkaunt, sessiya
tushadi, joy boshqaga ketadi, bo'sh majburiy maydon, joy umuman ochilmaydi.
Oxirida "HAMMASI OK" chiqishi kerak. Sizning .env, log va shots ga tegmaydi.
Ariza formasi soxta saytda TAXMINIY: haqiqiy forma hali ko'rilmagan.

## Yozuvlar (har bir urinish to'liq saqlanadi)

Har safar "Boshlash" bosilganda `runs/<sana-vaqt>-<login>/` papkasi ochiladi:

| Fayl | Nima |
|---|---|
| `navbat.log` | shu urinishning logi |
| `NNN-<qadam>.html` + `.png` | har bir qadamdagi sahifa HTML va screenshot (login, slot, forma, tasdiq, yakun, xato) |
| `calendar-HHMMSS.html` | kalendar har safar O'ZGARGANDA (bo'sh joy qachon, qanday chiqqani) |
| `network.jsonl` | saytga ketgan so'rovlar: vaqt, url, status |
| `console.log` | brauzer konsoli va JS xatolari |
| `trace-N.zip` | to'liq qayta ko'rish: `playwright show-trace runs\...	race-1.zip` |

Parol hech qayerga yozilmaydi (login paytida trace o'chiriladi; mock test buni tekshiradi).
Papkalar o'chirilmaydi: keyingi kun uchun botni shu yozuvlar asosida yaxshilaymiz.
Bitta urinish taxminan 20-200 MB.
