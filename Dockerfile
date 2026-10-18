# Playwright + Chromium tayyor obraz. Teg aniq versiya (moving tag emas);
# requirements-docker.txt dagi playwright versiyasi shu teg bilan bir xil bo'lsin.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

# Xvfb (virtual ekran) + x11vnc + noVNC: brauzerni http://127.0.0.1:8107 da
# ko'rib, kerak bo'lsa qo'lda davom etish uchun (CAPTCHA, forma).
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      xvfb x11vnc novnc websockify tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV TZ=Asia/Tashkent \
    DISPLAY=:99 \
    DASHBOARD_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1

USER pwuser
EXPOSE 8106 8107
ENTRYPOINT ["/entrypoint.sh"]
