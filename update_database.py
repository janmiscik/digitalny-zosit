import sqlite3


DATABASE_FILE = "digitalny-zosit.db"


connection = sqlite3.connect(
    DATABASE_FILE
)


cursor = connection.cursor()


# =========================================
# KONTROLA TABUĽKY JOBS
# =========================================

cursor.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
)

jobs_table = cursor.fetchone()


if jobs_table is None:

    print("CHYBA: Tabuľka jobs neexistuje.")

    print(
        "Skontroluj, či sa skript spúšťa v hlavnom priečinku projektu."
    )

    connection.close()

    raise SystemExit


# =========================================
# KONTROLA STĹPCOV
# =========================================

cursor.execute(
    "PRAGMA table_info(jobs)"
)


columns = cursor.fetchall()


column_names = [
    column[1]
    for column in columns
]


# =========================================
# PRIDANIE TERMÍNU
# =========================================

if "due_date" not in column_names:

    cursor.execute(
        """
        ALTER TABLE jobs
        ADD COLUMN due_date DATE
        """
    )

    print(
        "Stĺpec due_date bol úspešne pridaný."
    )

else:

    print(
        "Stĺpec due_date už existuje."
    )


connection.commit()

connection.close()


print(
    "Databáza je aktualizovaná."
)