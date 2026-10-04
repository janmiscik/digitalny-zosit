FROM python:3.12-slim

WORKDIR /app

# PDF (reportlab) aj obrázky (Pillow) majú pre bežné architektúry
# hotové (manylinux) wheely, takže appka nepotrebuje žiadne špeciálne
# systémové knižnice na zostavenie - len samotný pip install.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Všetky trvalé dáta žijú pod /app/data:
#
#   /app/data/accounts.db                      master databáza kont
#   /app/data/accounts/<konto>/database.db     business dáta konta
#   /app/data/accounts/<konto>/uploads/        logo, podpis, fotky
#   /app/data/accounts/<konto>/backups/        zálohy
#
# Na Railway tu MUSÍ byť pripojený Volume (Mount path: /app/data),
# inak sa všetko stratí pri každom redeployi. Dockerfile instrukcia
# `VOLUME` sa tu zámerne nepoužíva - Railway ju nepodporuje, volume
# sa pripája v nastaveniach služby.
ENV ACCOUNTS_DATABASE_URL=sqlite:////app/data/accounts.db \
    ACCOUNTS_DATA_DIR=/app/data/accounts

# Railway nastaví premennú PORT a appka musí počúvať práve na nej
# (viď start.sh). 8000 je len predvolená hodnota pre lokálne spustenie.
ENV PORT=8000

# Na Railway je appka za ich proxy - bez tohto by uvicorn videl ako
# IP klienta vždy adresu proxy a rate limiter prihlásenia (auth.py,
# kľúčovaný podľa IP) by zdieľal jeden limit pre všetkých
# používateľov. Appka nie je dostupná mimo Railway proxy, takže
# hlavičke X-Forwarded-For sa dá veriť.
ENV FORWARDED_ALLOW_IPS=*

EXPOSE 8000

# start.sh pri KAŽDOM štarte kontajnera: zmigruje databázu kont,
# zmigruje databázy VŠETKÝCH existujúcich kont a spustí appku.
CMD ["sh", "./start.sh"]
