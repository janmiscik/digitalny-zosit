FROM python:3.12-slim

WORKDIR /app

# PDF (reportlab) aj obrázky (Pillow) majú pre bežné architektúry
# hotové (manylinux) wheely, takže appka nepotrebuje žiadne špeciálne
# systémové knižnice na zostavenie - len samotný pip install.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Dáta (databáza kont + databázy a súbory jednotlivých kont) musia
# prežiť reštart kontajnera - pripoj sem trvalý disk/volume
# (na Railway: Volume s mount pathom /app/data).
VOLUME ["/app/data"]

EXPOSE 8000

# Pri KAŽDOM štarte kontajnera: over/inicializuj databázu kont, potom
# spusti appku. Databázy jednotlivých kont appka migruje sama pri ich
# registrácii (tenancy.provision_account) - tento krok sa týka len tej
# jednej spoločnej databázy kont.
CMD alembic -c alembic_accounts.ini upgrade head && \
    uvicorn main:app --host 0.0.0.0 --port 8000
