# Výrobny elektřiny v Česku

Interaktivní mapa licencovaných výroben elektřiny z otevřených dat Energetického regulačního úřadu (ERÚ), umístěných do katastru přes RÚIAN (ČÚZK). Aktualizuje se jednou měsíčně přes GitHub Actions a publikuje na GitHub Pages.

## Co skript dělá

`update.py` při každém běhu:

1. **Zjistí nová data.** Na [eru.gov.cz/zpristupnena-data](https://eru.gov.cz/zpristupnena-data) najde nejnovější *Datové sady technologických energetických zařízení* (TEZ, vydávané k 1. dni měsíce) a k nim časově nejbližší *Seznam držitelů licencí* (vydávaný týdně). Pokud se od minulého běhu nic nezměnilo, skončí.
2. **Stáhne a načte** soubory `tez-sk-11-*.xml` (výrobny, zdroje, výkon, katastry, parcely) a `lic11*.xml` (držitelé licencí, kódování windows-1250).
3. **Spojí je** přes číslo licence. Výroba elektřiny z licencí, které jsou v licenčním souboru zrušené nebo zaniklé, se v mapě nezobrazuje.
4. **Umístí výrobny:**
   - **k.ú.:** podle kódu katastrálního území do definičního bodu k.ú. (celostátní soubor RÚIAN `ST_UKSG`);
   - **parcely:** výrobny firem navíc podle parcelních čísel do definičního bodu parcely (obecní soubory RÚIAN `OB_*_UKSH`, stahují se jen obce s novými parcelami);
   - **fyzické osoby:** jejich výrobny zůstávají na úrovni k.ú. a v mapě jsou sečtené.
5. **Odhadne rok registrace výrobny.** Datum zahájení v TEZ se při převodu nebo obnově licence přepisuje. Proto skript drží historii nejstaršího známého data a opravuje ho podle pořadového čísla výrobny (`NNNNN_T11`), které se nemění.
6. **Vytvoří výstupy** v `docs/` a přehled změn proti minulému měsíci.

## Výstupy

| Soubor | Obsah |
|---|---|
| `docs/index.html` | interaktivní mapa, graf kumulativního výkonu, animace FVE po letech |
| `docs/data/vyrobny.csv` | jeden řádek na výrobnu: výkon, typ, držitel, katastr, kódy RÚIAN, souřadnice, rok registrace, kontroly |
| `docs/data/vyrobny.geojson` | body pro mapové nástroje (firmy jednotlivě, fyzické osoby sečtené po k.ú.) |
| `docs/data/zmeny.csv` | nové, ukončené a změněné výrobny proti minulému běhu |
| `docs/data/stav.json` | data zdrojů, odkazy, souhrny a počty kontrol |
| `docs/embed/vyrobny.js` | mapa pro vložení do WordPressu (modul Kód v Divi) |
| `state/` | trvalý stav mezi běhy: nalezené parcely a historie dat zahájení (verzováno v gitu) |

## Vložení do webu (WordPress / Divi)

Na [opendatastudio.cz/energetika](https://opendatastudio.cz/energetika/) je mapa vložená modulem **Kód** v Divi. Modul obsahuje jen `<div id="vyrobny-app">`, knihovnu d3 a krátký načítací skript. Ten stáhne soubor **`https://dvoriik.github.io/vyrobny-elektriny/embed/vyrobny.js`** se styly, obsahem, skriptem a daty a spustí ho.

Soubor `docs/embed/vyrobny.js` generuje `update.py` při každém běhu ze **stejné šablony** (`src/template.html`) jako samostatnou stránku. Na webu je proto vždy stejný vzhled i data jako na GitHub Pages, bez zásahu ve WordPressu. Cache WordPressu (24 h) se ho netýká, protože se soubor načítá až v prohlížeči. GitHub Pages ho obnovuje do 10 minut.

Ve vložené verzi platí jen světlý vzhled, tmavý režim má jen samostatná stránka (`class="vyr-page"`). Styly jsou omezené na `#vyrobny-app` a ID prvků mají předponu `vyr-`, aby se nebily se šablonou webu.

## Úpravy vzhledu a nové grafiky

Měňte jen **`src/template.html`**: styly v `<style id="vyr-css">`, obsah mezi `<!--vyr:markup-->` a `<!--/vyr:markup-->`, skript v `<script id="vyr-js">`. Pravidla:

- **ID:** nová ID pište s předponou `vyr-` (`id="vyr-novy-graf"`, `getElementById('vyr-novy-graf')`).
- **Styly:** nové styly začínejte `#vyrobny-app ` a barvy berte z proměnných (`var(--ink)`, `var(--g0)`…).
- **Data:** jsou v objektu `DATA` (viz `build.map_data`). Když nový graf potřebuje další údaj, přidejte ho tam.

Po pushi do `main` se workflow spustí samo, přepočítá výstupy a nasadí je na GitHub Pages. Do pár minut se změna projeví na samostatné stránce i na webu.

## Spuštění

```bash
pip install -r requirements.txt
python update.py            # jen při nových datech
python update.py --force    # vždy přepočítat
python update.py --tez-date 2026-10-01   # konkrétní měsíc
```

První běh bez `state/` stáhne parcely z přibližně 3 100 obcí (asi 25 minut), další běhy jen to, co přibylo.

## Automatická aktualizace

Workflow `.github/workflows/update.yml` běží každý den 3.–12. v měsíci, protože ERÚ datum zveřejnění pevně nedrží. Spouští se také po každém pushi, který mění kód nebo šablonu. Když najde nová data, commitne `docs/` a `state/` a nasadí GitHub Pages. Ručně jde spustit v záložce Actions (volba *Přepočítat i bez nových dat*).

V nastavení repozitáře je potřeba zapnout **Settings → Pages → Source: GitHub Actions**.

## Omezení dat

- **Malé výrobny bez licence chybí.** Výrobny do 50 kW licenci nepotřebují, takže v datech nejsou hlavně střešní FVE domácností.
- **Nejsou tu ukončené elektrárny.** Graf v čase ukazuje dnešní výrobny podle roku registrace, ne skutečný historický výkon.
- **Přesnost polohy:** definiční bod parcely nebo k.ú., ne přesná poloha zařízení. Část parcelních čísel v TEZ v RÚIAN neexistuje (přečíslování) a výrobna pak zůstane ve středu k.ú.
- **Osobní údaje:** názvy výroben fyzických osob se ve výstupech nahrazují textem „výrobna fyzické osoby“.

Zdroje: [ERÚ – zpřístupněná data](https://eru.gov.cz/zpristupnena-data), [ČÚZK – výměnný formát RÚIAN](https://vdp.cuzk.gov.cz/).
