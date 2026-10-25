"""
Ochrany na úrovni databázy (SQLite triggery), ktoré platia aj pri priamom SQL,
hromadných operáciách a omyloch v budúcom kóde - nielen cez ORM.

  * audit_log: len pridávanie (žiadny UPDATE ani DELETE),
  * invoices: vystavenú faktúru (stav iný než Návrh) nemožno zmazať a nemožno ju
    vrátiť do stavu Návrh,
  * invoice_items: položky vystavenej faktúry sa nedajú upraviť ani zmazať.

Rovnaké príkazy používa migrácia (existujúce databázy) aj vytvorenie schémy
(`Base.metadata.create_all` - nové databázy a testy), takže testy bežia s
rovnakými ochranami ako produkcia.

Ochrana nie je nepreniknuteľná: kto má priamy prístup k súboru databázy, vie
triggery zrušiť. Chráni pred chybami a bežnými zásahmi, nie pred správcom.
"""

DRAFT = "Návrh"

TRIGGERS: dict[str, tuple[str, str]] = {
    # názov: (tabuľka, príkaz CREATE)
    "audit_log_no_update": ("audit_log", (
        "CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'audit_log je len na pridávanie'); END"
    )),
    "audit_log_no_delete": ("audit_log", (
        "CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'audit_log je len na pridávanie'); END"
    )),
    "invoices_no_delete_issued": ("invoices", (
        "CREATE TRIGGER invoices_no_delete_issued BEFORE DELETE ON invoices "
        f"WHEN OLD.status <> '{DRAFT}' "
        "BEGIN SELECT RAISE(ABORT, 'vystavenú faktúru nemožno zmazať'); END"
    )),
    "invoices_no_revert_to_draft": ("invoices", (
        "CREATE TRIGGER invoices_no_revert_to_draft BEFORE UPDATE OF status ON invoices "
        f"WHEN OLD.status <> '{DRAFT}' AND NEW.status = '{DRAFT}' "
        "BEGIN SELECT RAISE(ABORT, 'vystavenú faktúru nemožno vrátiť do návrhu'); END"
    )),
    "invoice_items_no_update_issued": ("invoice_items", (
        "CREATE TRIGGER invoice_items_no_update_issued BEFORE UPDATE ON invoice_items "
        f"WHEN (SELECT status FROM invoices WHERE id = OLD.invoice_id) <> '{DRAFT}' "
        "BEGIN SELECT RAISE(ABORT, 'položky vystavenej faktúry sa nedajú meniť'); END"
    )),
    "invoice_items_no_delete_issued": ("invoice_items", (
        "CREATE TRIGGER invoice_items_no_delete_issued BEFORE DELETE ON invoice_items "
        f"WHEN (SELECT status FROM invoices WHERE id = OLD.invoice_id) <> '{DRAFT}' "
        "BEGIN SELECT RAISE(ABORT, 'položky vystavenej faktúry sa nedajú meniť'); END"
    )),
}


def install_statements(only_tables: set[str] | None = None) -> list[str]:
    """Príkazy DROP + CREATE pre všetky (alebo vybrané) triggery."""

    statements = []

    for name, (table, create) in TRIGGERS.items():

        if only_tables is not None and table not in only_tables:
            continue

        statements.append(f"DROP TRIGGER IF EXISTS {name}")
        statements.append(create)

    return statements


def remove_statements() -> list[str]:

    return [f"DROP TRIGGER IF EXISTS {name}" for name in TRIGGERS]


def attach_to_metadata(metadata) -> None:
    """Po vytvorení tabuliek (create_all) nainštaluje triggery na SQLite."""

    from sqlalchemy import DDL, event

    for table_name in ("audit_log", "invoices", "invoice_items"):

        table = metadata.tables[table_name]

        for statement in install_statements({table_name}):

            event.listen(table, "after_create", DDL(statement).execute_if(dialect="sqlite"))


def _normalize(sql: str) -> str:

    return " ".join((sql or "").split()).rstrip(";").lower()


def missing_triggers(database_path) -> set[str]:
    """
    Názvy ochranných triggerov, ktoré v databáze chýbajú alebo majú pozmenenú
    definíciu (oslabený trigger je nebezpečnejší než žiadny, lebo to nikto nezbadá).
    Databázu len číta.
    """

    import sqlite3
    from pathlib import Path

    uri = f"file:{Path(database_path).as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)

    try:
        present = {name: sql for name, sql in
                   connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")}

    finally:
        connection.close()

    return {
        name for name, (_, create) in TRIGGERS.items()
        if name not in present or _normalize(present[name]) != _normalize(create)
    }
