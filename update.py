#!/usr/bin/env python3
"""Měsíční aktualizace mapy výroben elektřiny z otevřených dat ERÚ a RÚIAN.

Použití:
    python update.py                  # zkontroluje eru.gov.cz/zpristupnena-data, při nových datech vše přepočítá
    python update.py --force          # přepočítá i bez nových dat
    python update.py --tez-date 2026-10-01

Výstupy v docs/ (GitHub Pages): index.html, data/vyrobny.csv, data/vyrobny.geojson, data/zmeny.csv, data/stav.json
"""
import argparse, json, sys, datetime as dt
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / 'src'))
import eru, ruian, build  # noqa: E402

ROOT = Path(__file__).parent


def log(*a):
    print(*a, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=ROOT / 'docs', type=Path)
    ap.add_argument('--cache', default=ROOT / 'cache', type=Path)
    ap.add_argument('--state', default=ROOT / 'state', type=Path, help='trvalý stav (verzovaný v gitu)')
    ap.add_argument('--tez-date', type=dt.date.fromisoformat)
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args()
    out, cache, state = a.out, a.cache, a.state
    state.mkdir(parents=True, exist_ok=True)
    (out / 'data').mkdir(parents=True, exist_ok=True); cache.mkdir(parents=True, exist_ok=True)

    src = eru.find_latest(a.tez_date)
    log(f"ERÚ: TEZ {src['tez_date']}  licence {src['lic_date']}")
    stav_path = out / 'data' / 'stav.json'
    prev_stav = json.load(open(stav_path)) if stav_path.exists() else {}
    if not a.force and prev_stav.get('tez') == str(src['tez_date']) and prev_stav.get('licence') == str(src['lic_date']):
        log('Žádná nová data, končím.'); return 0

    tez_f = eru.download(src['tez_url'], cache / 'eru' / Path(src['tez_url']).name)
    lic_f = eru.download(src['lic_url'], cache / 'eru' / Path(src['lic_url']).name)
    lic = eru.read_licences(lic_f)
    L, P, O, C = eru.read_tez(tez_f)
    log(f'TEZ: {L.LicId.nunique()} licencí, {len(P)} výroben, {len(O)} zdrojů; licenční soubor: {len(lic)} řádků')

    p = build.premises(L, P, O, C, lic)
    rdate = ruian.latest_date()
    R, hr = ruian.load_state(rdate, cache)
    log(f'RÚIAN {rdate}: {len(R["KatastralniUzemi"])} k.ú.')
    p, ku_obec = build.geocode_ku(p, R)
    c, want = build.wanted_parcels(p, C)
    pts = ruian.parcel_points(rdate, want, ku_obec, cache, workers=a.workers, log=log, state=state)
    p = build.place_on_parcels(p, c, pts)

    hist_path = state / 'prvni_datum.csv'
    hist = pd.read_csv(hist_path, dtype=str) if hist_path.exists() else pd.DataFrame(columns=['provozovna_id', 'prvni_datum'])
    p, hist = build.registration_year(p, hist)
    hist.to_csv(hist_path, index=False)

    p = p[build.COLS].copy()
    # veřejné výstupy: názvy výroben fyzických osob (často jméno a adresa) nezveřejňujeme
    p.loc[p.drzitel_typ.str.startswith('fyzická'), 'provozovna_nazev'] = 'výrobna fyzické osoby'
    csv_path = out / 'data' / 'vyrobny.csv'
    if csv_path.exists():
        prev = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
        ch = build.changes(prev, p.astype(str))
        ch.to_csv(out / 'data' / 'zmeny.csv', index=False, encoding='utf-8-sig')
        log(f'Změny proti minulému běhu: {ch.zmena.value_counts().to_dict()}')
    p.to_csv(csv_path, index=False, encoding='utf-8-sig')
    json.dump(build.geojson(p), open(out / 'data' / 'vyrobny.geojson', 'w'), ensure_ascii=False, separators=(',', ':'))

    meta = {'tez': str(src['tez_date']), 'lic': str(src['lic_date']), 'ruian': f'{rdate[:4]}-{rdate[4:6]}-{rdate[6:]}'}
    md = build.map_data(p, R, hr, meta)
    tpl = (ROOT / 'src' / 'template.html').read_text(encoding='utf-8')
    page = tpl.replace('__DATA__', json.dumps(md, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/'))
    (cache / 'artifact.html').write_text(page, encoding='utf-8')  # verze bez kostry dokumentu (pro Claude artifact)
    (out / 'embed').mkdir(exist_ok=True)
    (out / 'embed' / 'vyrobny.js').write_text(build.embed_bundle(tpl, md, meta), encoding='utf-8')  # pro modul Kód v Divi
    (out / 'index.html').write_text('<!doctype html>\n<html lang="cs"><head><meta charset="utf-8">'
                                    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                                    '<style>body{margin:0}[hidden]{display:none!important}</style></head><body>\n' + page + '\n</body></html>', encoding='utf-8')
    m = p[p.pouzit_pro_mapu == 'ano']
    stav = {'tez': meta['tez'], 'licence': meta['lic'], 'ruian': meta['ruian'], 'zdroj_tez': src['tez_url'], 'zdroj_licence': src['lic_url'],
            'aktualizovano': dt.datetime.now().isoformat(timespec='seconds'), 'vyroben_v_mape': int(len(m)),
            'vykon_MW': round(float(m.vykon_el_MW.astype(float).sum()), 1),
            'presnost': p.geokod_presnost.value_counts().to_dict(), 'kontroly': p.kontrola_umisteni[p.kontrola_umisteni != ''].value_counts().to_dict()}
    json.dump(stav, open(stav_path, 'w'), ensure_ascii=False, indent=1)
    log(json.dumps(stav, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
