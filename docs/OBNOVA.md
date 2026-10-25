# Obnova dát po úplnej strate úložiska

Všetky dáta (databázy, fotky, logo, zálohy) ležia na jednom Railway Volume (`/app/data`).
Ak Volume zmizne, zmiznú aj zálohy na ňom. **Preto si zálohu priebežne sťahuj mimo Railway.**

## Prevencia
**Najlepšie: zapni automatické nahrávanie mimo Railway** (premenné `OFFSITE_BACKUP_*`, viď README → Záloha mimo servera). Potom každá záloha skončí aj v tvojom bucketi a pri strate Volume ju len stiahneš odtiaľ.

Bez toho (5 minút týždenne):
1. Nastavenia → Záloha → **Stiahnuť zálohu** (ZIP s databázou aj súbormi).
2. Ulož ju mimo Railway (osobný disk, cloud). Pred väčšou zmenou (nová verzia, import) stiahni novú.
3. Zálohu z bucketu stiahneš v konzole poskytovateľa (cesta `<predpona>/<konto>/<názov>.zip`) a nahráš v Nastaveniach.
4. Raz za čas zálohu otestuj: Nastavenia → Overiť zálohu (appka zálohu rozbalí a skontroluje).

## Postup po strate úložiska
1. V Railway pripoj nový Volume s Mount path `/app/data` a nasaď službu. V logu musí byť
   `[ULOZISKE] OK: /app/data je samostatný disk`.
2. Otvor appku a **zaregistruj sa znova s rovnakým menom konta** (kontá sú vo Volume, takže zmizli).
3. Prihlás sa → Nastavenia → **Obnoviť zo zálohy** → nahraj stiahnutý ZIP.
4. Appka záloha overí, uloží poistnú zálohu, obnoví databázu aj súbory a doplní schému na aktuálnu verziu.
   Ak záloha pochádza z novšej verzie appky alebo je poškodená, pôvodný stav ostane nedotknutý.
5. Skontroluj faktúry, zákazníkov a logo na PDF. Dáta sú v stave z času zálohy; doklady vystavené
   po zálohe treba vystaviť/zapísať znova.

## Ak sa proces počas obnovy zabije
Obnova zapisuje žurnál a po páde (OOM, reštart) sa pri štarte sama vráti z poistnej zálohy, takže konto
nezostane napoly obnovené. Podrobnosti a ručný postup: `docs/PREVADZKA.md`.

## Čo test pokrýva
`tests/test_disaster_recovery.py` simuluje presne tento postup: vymaže celé úložisko aj zoznam kont,
zaregistruje konto znova, nahrá stiahnutú zálohu a porovná dáta, súbory a schému. Overuje aj poškodené
zálohy, zálohu zo staršej verzie, rollback pri zlyhaní migrácie, poškodenú fotku v ZIP a simulovaný pád procesu v troch bodoch výmeny.

## Limity
- Maximálna veľkosť nahrávanej zálohy je 200 MB.
- Záloha je po konte: obnova jedného konta sa nedotkne ostatných.


## Obnova zo šifrovanej zálohy (súbor `.zip.enc`)

Ak je nastavené `BACKUP_ENCRYPTION_KEY`, zálohy v bucketi sú zašifrované.

1. Stiahni súbor `...zip.enc` z bucketu (R2 → bucket → konto → najnovší súbor).
2. Na počítači s projektom: `python decrypt_backup.py zaloha.zip.enc zaloha.zip` (heslo sa opýta alebo ho vezme z `BACKUP_ENCRYPTION_KEY`).
3. Výsledné `zaloha.zip` nahraj cez Nastavenia → Obnova zo zálohy.

**Heslo musíš mať uložené mimo Railway.** Bez neho sa záloha nedá rozšifrovať. Po zmene hesla sa staré zálohy dajú otvoriť len starým heslom.


### Najjednoduchšie: obnova šifrovanej zálohy priamo v appke
Nastavenia → Obnoviť zo zálohy → vyber súbor `.zip.enc` stiahnutý z bucketu → do poľa „Heslo k šifrovanej zálohe"
zadaj heslo (ak ostane prázdne, použije sa `BACKUP_ENCRYPTION_KEY` zo servera) → Obnoviť. Šifrovanie sa
rozpozná podľa obsahu súboru, nie podľa názvu. Pri zlom hesle alebo poškodenom súbore sa nič nezmení.
Heslo sa nikam neukladá ani nezobrazuje. Príkaz `decrypt_backup.py` je potrebný len ak appka nebeží.

## Skúška obnovy z bucketu (odporúčané raz mesačne)

Overí, že zálohy v R2 sa dajú naozaj stiahnuť, rozšifrovať a obnoviť. Berie najnovšiu zálohu každého konta, nič nenahráva ani nemení a dočasné súbory zmaže:

```
python offsite_restore.py --verify
python offsite_restore.py --verify --account admin
```

Potrebuje `OFFSITE_BACKUP_*` a pri šifrovaných zálohách `BACKUP_ENCRYPTION_KEY` (rovnaké ako na serveri). Výstup: `OK` alebo `CHYBA` s dôvodom pri každom konte; návratový kód je 1, ak niektoré konto zlyhalo.
