"""Spojení TEZ + licencí, umístění do RÚIAN (k.ú., parcely), odhad roku registrace, výstupy."""
import re, json, unicodedata
from math import radians, sin, cos, asin, sqrt
import numpy as np
import pandas as pd
from shapely.geometry import shape, mapping
from shapely.geometry.polygon import orient
from pyproj import Transformer
from parcels import parse as parse_parcels

TR = Transformer.from_crs('EPSG:5514', 'EPSG:4326', always_xy=True)
GROUP = {'SLE': 0, 'VE': 1, 'PVE': 1, 'VTE': 2, 'PSE': 3, 'PPE': 3, 'AOE': 3, 'PE': 4, 'JE': 5}
TYP_FIX = {'plynovýaspalovací': 'plynový a spalovací', 'přečerpávacívodní': 'přečerpávací vodní', 'jináalternativní': 'jiná alternativní'}


def premises(L, P, O, C, lic):
    """Jeden řádek na výrobnu s výkonem, palivy, katastry a držitelem."""
    cap = O.groupby(['LicId', 'PremiseElecId']).agg(
        vykon_el_MW=('OutputElec', 'sum'), vykon_tep_MW=('OutputTherm', 'sum'), pocet_zdroju=('OutputElec', 'size'),
        paliva=('FuelTypeDes', lambda s: '; '.join(sorted({x.strip() for v in s for x in str(v).split(',') if x.strip()})))).reset_index()
    cad = C[C.CadasterId != ''].groupby(['LicId', 'PremiseElecId']).agg(
        ku_kod=('CadasterId', lambda s: ';'.join(dict.fromkeys(s))), ku_nazev=('CadasterName', lambda s: ';'.join(dict.fromkeys(s)))).reset_index()
    p = P.merge(cap, on=['LicId', 'PremiseElecId'], how='left').merge(cad, on=['LicId', 'PremiseElecId'], how='left')
    p['vykon_el_MW'] = p.vykon_el_MW.fillna(0); p['vykon_tep_MW'] = p.vykon_tep_MW.fillna(0); p['pocet_zdroju'] = p.pocet_zdroju.fillna(0).astype(int)
    p[['ku_kod', 'ku_nazev', 'paliva']] = p[['ku_kod', 'ku_nazev', 'paliva']].fillna('')
    a = lic.assign(r=(lic.stav != 'Aktivní').astype(int)).sort_values('r').drop_duplicates('cislo_licence')
    a = a.rename(columns={'cislo_licence': 'LicId', 'stav': 'stav_v_licencnim_souboru', 'subjekt_IC': 'drzitel_IC', 'subjekt_nazev': 'drzitel_nazev'})
    a['drzitel_typ'] = np.where(a.fyzicka_osoba, 'fyzická osoba (anonymizováno)', 'právnická osoba / podnikatel')
    a.loc[a.fyzicka_osoba, ['drzitel_IC', 'drzitel_nazev']] = ''
    p = p.merge(a[['LicId', 'stav_v_licencnim_souboru', 'drzitel_typ', 'drzitel_IC', 'drzitel_nazev']], on='LicId', how='left')
    p['stav_v_licencnim_souboru'] = p.stav_v_licencnim_souboru.fillna('chybí v licenčním souboru')
    p['drzitel_typ'] = p.drzitel_typ.fillna('neznámý'); p[['drzitel_IC', 'drzitel_nazev']] = p[['drzitel_IC', 'drzitel_nazev']].fillna('')
    for c in ['DateBegin', 'DateChange']:
        p[c] = p[c].replace('1900-01-01', '')
    p['ZipCode'] = p.ZipCode.str.replace(r'^(\d{3})(\d{2})$', r'\1 \2', regex=True)
    p = p.rename(columns={'LicId': 'cislo_licence', 'PremiseElecId': 'provozovna_id', 'Name': 'provozovna_nazev', 'City': 'obec', 'ZipCode': 'psc',
                          'County': 'okres', 'State': 'kraj', 'TD_ElecPremiseTypeId': 'typ_kod', 'TD_ElecPremiseTypeDes': 'typ',
                          'TD_River': 'tok', 'TD_RiverKm': 'tok_km', 'DateBegin': 'datum_zahajeni', 'DateChange': 'datum_zmeny'})
    p['typ'] = p.typ.replace(TYP_FIX)
    p['tok_km'] = np.where(p.tok == '', '', p.tok_km)
    p['pouzit_pro_mapu'] = np.where(p.stav_v_licencnim_souboru.isin(['Aktivní', 'chybí v licenčním souboru']) & (p.vykon_el_MW > 0), 'ano', 'ne')
    return p


def _hav(a, b, c, d):
    a, b, c, d = map(radians, (a, b, c, d))
    return 12742 * asin(sqrt(sin((c - a) / 2) ** 2 + cos(a) * cos(c) * sin((d - b) / 2) ** 2))


def geocode_ku(p, R):
    ku, ob, ok, vu, co = R['KatastralniUzemi'], R['Obec'], R['Okres'], R['Vusc'], R['CastObce']
    ob = ob.copy()
    ob['Vusc_Kod'] = ob.Okres_Kod.map(ok.set_index('Kod').Vusc_Kod)
    praha = ob.Okres_Kod.isna() & ob.Nazev.eq('Praha'); ob.loc[praha, 'Vusc_Kod'] = '19'
    ob['okres_nazev'] = ob.Okres_Kod.map(ok.set_index('Kod').Nazev); ob.loc[praha, 'okres_nazev'] = 'Hlavní město Praha'
    ob['kraj_nazev'] = ob.Vusc_Kod.map(vu.set_index('Kod').Nazev)
    k = ku.merge(ob[['Kod', 'Nazev', 'Okres_Kod', 'Vusc_Kod', 'okres_nazev', 'kraj_nazev']].add_prefix('ob_'), left_on='Obec_Kod', right_on='ob_Kod', how='left').set_index('Kod')
    p['ku1'] = p.ku_kod.str.split(';').str[0]
    hit = p.ku1.isin(k.index)
    m = {'ruian_ku_kod': None, 'ruian_ku_nazev': 'Nazev', 'ruian_obec_kod': 'Obec_Kod', 'ruian_obec': 'ob_Nazev', 'ruian_okres_kod': 'ob_Okres_Kod',
         'ruian_okres': 'ob_okres_nazev', 'ruian_kraj_kod': 'ob_Vusc_Kod', 'ruian_kraj': 'ob_kraj_nazev', 'lat': 'lat', 'lon': 'lon'}
    for col, src in m.items():
        p[col] = np.where(hit, p.ku1, '') if src is None else p.ku1.map(k[src]).fillna('')
    p['geokod_presnost'] = np.where(hit, 'definiční bod katastrálního území', '')
    # záloha: obec podle názvu a okresu z TEZ
    norm = lambda s: str(s).strip().lower()
    key = ob.Nazev.map(norm) + '|' + ob.okres_nazev.fillna('').map(norm)
    obk = ob[~key.duplicated(keep=False)].set_index(key[~key.duplicated(keep=False)])
    for i in p.index[~hit]:
        for c in [p.at[i, 'obec'], re.split(r'\s*[-–,]\s*', p.at[i, 'obec'])[0]]:
            kk = norm(c) + '|' + norm(p.at[i, 'okres'])
            if kk in obk.index:
                r = obk.loc[kk]
                p.loc[i, ['ruian_obec_kod', 'ruian_obec', 'ruian_okres_kod', 'ruian_okres', 'ruian_kraj_kod', 'ruian_kraj', 'lat', 'lon', 'geokod_presnost']] = [
                    r.Kod, r.Nazev, r.Okres_Kod if isinstance(r.Okres_Kod, str) else '', r.okres_nazev, r.Vusc_Kod, r.kraj_nazev, r.lat, r.lon, 'definiční bod obce (podle názvu obce a okresu)']
                break
    # kontrola: vzdálenost od obce uvedené v TEZ
    pts = {}
    for df in (ob, co):
        for n, la, lo in zip(df.Nazev, df.lat.astype(float), df.lon.astype(float)):
            pts.setdefault(str(n).lower(), []).append((la, lo))
    def dist(r):
        if not r.lat: return None
        best = None
        for c in re.split(r'\s*[-–,/]\s*', r.obec) + [r.obec]:
            for q in pts.get(c.strip().lower(), []):
                d = _hav(float(r.lat), float(r.lon), *q); best = d if best is None or d < best else best
        return best
    d = p.apply(dist, axis=1)
    p['kontrola_umisteni'] = ''
    p.loc[(p.okres != '') & (p.okres != p.ruian_okres) & ~(d > 20), 'kontrola_umisteni'] = 'okres v TEZ neodpovídá RÚIAN (obec nejspíš změnila okres)'
    p.loc[d > 20, 'kontrola_umisteni'] = 'k ověření: katastr je >20 km od obce uvedené v TEZ'
    return p.drop(columns=['ku1']), dict(zip(ku.Kod, ku.Obec_Kod))


def wanted_parcels(p, C):
    firm = set(zip(p.loc[~p.drzitel_typ.str.startswith('fyzická'), 'provozovna_id'], p.loc[~p.drzitel_typ.str.startswith('fyzická'), 'cislo_licence']))
    c = C[(C.CadasterId != '').values & np.array([k in firm for k in zip(C.PremiseElecId, C.LicId)])].copy()
    c['refs'] = c.CadasterNote.fillna('').map(parse_parcels)
    want = {(r.CadasterId, None, km, None) for r in c.itertuples() for _, km, _ in r.refs}
    return c, want


def place_on_parcels(p, c, pts):
    idx = {}
    for r in pts.itertuples():
        idx.setdefault((str(r.ku), int(r.kmen), None if int(r.pod) < 0 else int(r.pod)), []).append((str(r.druh), r.x, r.y))
    def find(k, st, km, pod):
        cand = idx.get((k, km, pod), [])
        a = [q for q in cand if q[0] == ('1' if st else '2')]
        return (a or cand or [None])[0]
    found = {}
    for r in c.itertuples():
        prev = None
        for st, km, pod in r.refs:
            q = find(r.CadasterId, st, km, pod)
            if q is None and pod is None and prev and prev[2] is not None:
                q = find(r.CadasterId, prev[0], prev[1], km)
            if q is None and not st:
                q = find(r.CadasterId, True, km, pod)
            if q:
                found.setdefault((r.PremiseElecId, r.LicId), []).append((q[1], q[2]))
            prev = (st, km, pod)
    p['parcely_rozptyl_m'] = ''
    for (pid, lid), f in found.items():
        a = np.array(f); mx, my = np.median(a[:, 0]), np.median(a[:, 1])
        lon, lat = TR.transform(mx, my)
        sel = (p.provozovna_id == pid) & (p.cislo_licence == lid)
        p.loc[sel, ['lat', 'lon', 'geokod_presnost', 'parcely_rozptyl_m']] = [f'{lat:.6f}', f'{lon:.6f}', 'definiční bod parcely', str(round(float(np.max(np.hypot(a[:, 0] - mx, a[:, 1] - my)))))]
    return p


def registration_year(p, history):
    """Rok registrace: nejstarší známé datum zahájení výrobny (historie napříč běhy),
    opravené podle pořadového čísla výrobny, když převod licence datum posunul."""
    cur = p[p.datum_zahajeni != ''][['provozovna_id', 'datum_zahajeni']]
    h = pd.concat([history, cur.rename(columns={'datum_zahajeni': 'prvni_datum'})]).groupby('provozovna_id').prvni_datum.min().reset_index()
    x = pd.DataFrame({'pid': p.provozovna_id.unique()})
    x['id'] = x.pid.str.extract(r'^(\d+)')[0].astype(float)
    x['oy'] = pd.to_datetime(x.pid.map(h.set_index('provozovna_id').prvni_datum)).dt.year
    x = x.sort_values('id').reset_index(drop=True)
    med = x.oy.rolling(201, center=True, min_periods=20).median().bfill().ffill()
    mono = np.minimum(med.cummax(), med[::-1].cummin()[::-1]).round()
    x['year'] = np.where(x.oy.notna() & ((x.oy - mono).abs() <= 1), x.oy, mono).astype(int)
    x['zdroj'] = np.where(x.oy.isna(), 'odhad z pořadí registrace (datum chybí)',
                          np.where((x.oy - mono).abs() <= 1, 'datum zahájení v licenci', 'odhad z pořadí registrace (datum posunuté převodem licence)'))
    y = x.set_index('pid')
    p['rok_registrace'] = p.provozovna_id.map(y.year)
    p['rok_zdroj'] = p.provozovna_id.map(y.zdroj)
    return p, h


COLS = ['cislo_licence', 'pouzit_pro_mapu', 'stav_v_licencnim_souboru', 'drzitel_typ', 'drzitel_IC', 'drzitel_nazev', 'provozovna_id', 'provozovna_nazev',
        'typ_kod', 'typ', 'vykon_el_MW', 'vykon_tep_MW', 'pocet_zdroju', 'paliva', 'obec', 'psc', 'okres', 'kraj', 'ku_kod', 'ku_nazev', 'tok', 'tok_km',
        'datum_zahajeni', 'datum_zmeny', 'rok_registrace', 'rok_zdroj', 'ruian_ku_kod', 'ruian_ku_nazev', 'ruian_obec_kod', 'ruian_obec', 'ruian_okres_kod',
        'ruian_okres', 'ruian_kraj_kod', 'ruian_kraj', 'lat', 'lon', 'geokod_presnost', 'parcely_rozptyl_m', 'kontrola_umisteni']


def boundaries(R, hr, tol=0.0015):
    ok, vu = R['Okres'], R['Vusc']
    def feat(key):
        g = shape({'type': 'MultiPolygon', 'coordinates': hr[key]}).buffer(0).simplify(tol, preserve_topology=True)
        polys = list(g.geoms) if g.geom_type == 'MultiPolygon' else [g]
        return {'type': 'MultiPolygon', 'coordinates': [[[[round(a, 4), round(b, 4)] for a, b in ring.coords] for ring in [q.exterior, *q.interiors]] for q in (orient(x, -1.0) for x in polys)]}
    return ok, vu, feat


def map_data(p, R, hr, meta):
    m = p[p.pouzit_pro_mapu == 'ano'].copy(); m['mw'] = m.vykon_el_MW.astype(float)
    m['g'] = m.typ_kod.map(GROUP)
    m = m[m.g.notna() & (m.lat != '')]
    fo = m.drzitel_typ.str.startswith('fyzická')
    m['kukey'] = np.where(m.ruian_ku_kod != '', m.ruian_ku_kod, 'OB' + m.ruian_obec_kod)
    kuc = R['KatastralniUzemi'].set_index('Kod')
    ku = m.groupby('kukey').agg(n=('ruian_ku_nazev', 'first'), ob=('ruian_obec', 'first'), ok=('ruian_okres_kod', 'first'), kr=('ruian_kraj_kod', 'first'),
                                lon=('lon', 'first'), lat=('lat', 'first')).reset_index()
    # bod k.ú. = definiční bod k.ú. (ne poloha první výrobny, která může být na parcele)
    ku['lon'] = [kuc.lon.get(k, lo) for k, lo in zip(ku.kukey, ku.lon)]
    ku['lat'] = [kuc.lat.get(k, la) for k, la in zip(ku.kukey, ku.lat)]
    ku.loc[ku.n == '', 'n'] = ku.ob
    ku.loc[ku.ok == '', 'ok'] = 'P' + ku.kr
    kuidx = {k: i for i, k in enumerate(ku.kukey)}
    ok_t, vu_t, feat = boundaries(R, hr)
    okl = sorted(ku.ok.unique()); okidx = {k: i for i, k in enumerate(okl)}
    oknames = {**dict(zip(ok_t.Kod, ok_t.Nazev)), 'P19': 'Hlavní město Praha'}
    ok2kr = {**dict(zip(ok_t.Kod, ok_t.Vusc_Kod)), 'P19': '19'}
    krl = sorted(vu_t.Kod); kridx = {k: i for i, k in enumerate(krl)}
    names, nidx = [], {}
    def nm(s):
        if s not in nidx: nidx[s] = len(names); names.append(s)
        return nidx[s]
    pl = m.sort_values('mw', ascending=False, kind='mergesort')
    firm = ~pl.drzitel_typ.str.startswith('fyzická')
    parc = pl.geokod_presnost.eq('definiční bod parcely') & firm
    P = {'k': [kuidx[k] for k in pl.kukey], 'g': pl.g.astype(int).tolist(), 'w': [round(x * 1000, 2) for x in pl.mw],
         'n': [nm(s) if f else -1 for s, f in zip(pl.provozovna_nazev, firm)],
         'h': [nm(s) if f and s else -1 for s, f in zip(pl.drzitel_nazev, firm)],
         'i': [ic if f else '' for ic, f in zip(pl.drzitel_IC, firm)],
         'x': [round(float(v), 5) if c else 0 for v, c in zip(pl.lon, parc)], 'y': [round(float(v), 5) if c else 0 for v, c in zip(pl.lat, parc)],
         'yr': [int(v) - 2000 for v in pl.rok_registrace]}
    K = {'n': ku.n.tolist(), 'o': ku.ob.tolist(), 'd': [okidx[x] for x in ku.ok], 'x': [round(float(v), 4) for v in ku.lon], 'y': [round(float(v), 4) for v in ku.lat]}
    D = [{'type': 'Feature', 'geometry': feat('Vusc|19' if k == 'P19' else f'Okres|{k}'), 'properties': {'i': okidx[k], 'n': oknames[k], 'kr': kridx[ok2kr[k]]}} for k in okl]
    Rg = [{'type': 'Feature', 'geometry': feat(f'Vusc|{k}'), 'properties': {'i': kridx[k], 'n': vu_t.set_index('Kod').Nazev[k]}} for k in krl]
    meta = {**meta, 'n': int(len(m)), 'mw': round(float(m.mw.sum()), 1)}
    return {'P': P, 'K': K, 'N': names, 'D': {'type': 'FeatureCollection', 'features': D}, 'R': {'type': 'FeatureCollection', 'features': Rg}, 'meta': meta}


def geojson(p):
    m = p[p.pouzit_pro_mapu == 'ano'].copy(); m['mw'] = m.vykon_el_MW.astype(float)
    m = m[m.lat != '']
    fo = m.drzitel_typ.str.startswith('fyzická'); feats = []
    for r in m[~fo].itertuples():
        feats.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [float(r.lon), float(r.lat)]},
                      'properties': {'licence': r.cislo_licence, 'provozovna_id': r.provozovna_id, 'nazev': r.provozovna_nazev, 'drzitel': r.drzitel_nazev,
                                     'drzitel_IC': r.drzitel_IC, 'typ': r.typ, 'vykon_el_MW': round(r.mw, 5), 'paliva': r.paliva, 'rok_registrace': int(r.rok_registrace),
                                     'pocet_vyroben': 1, 'ku_kod': r.ruian_ku_kod, 'ku': r.ruian_ku_nazev, 'obec': r.ruian_obec, 'okres': r.ruian_okres,
                                     'kraj': r.ruian_kraj, 'presnost': 'parcela' if r.geokod_presnost == 'definiční bod parcely' else 'k.ú.'}})
    agg = m[fo].groupby(['ruian_ku_kod', 'ruian_ku_nazev', 'ruian_obec', 'ruian_okres', 'ruian_kraj', 'typ', 'lat', 'lon']).agg(mw=('mw', 'sum'), n=('mw', 'size')).reset_index()
    for r in agg.itertuples():
        feats.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [float(r.lon), float(r.lat)]},
                      'properties': {'nazev': 'výrobny fyzických osob (součet za katastr)', 'typ': r.typ, 'vykon_el_MW': round(r.mw, 5), 'pocet_vyroben': int(r.n),
                                     'ku_kod': r.ruian_ku_kod, 'ku': r.ruian_ku_nazev, 'obec': r.ruian_obec, 'okres': r.ruian_okres, 'kraj': r.ruian_kraj, 'presnost': 'k.ú.'}})
    return {'type': 'FeatureCollection', 'features': feats}


def changes(prev, cur):
    """Porovnání s minulým během: nové, zrušené a změněné výrobny (jen ty v mapě)."""
    a = prev[prev.pouzit_pro_mapu == 'ano'].drop_duplicates('provozovna_id').set_index('provozovna_id')
    b = cur[cur.pouzit_pro_mapu == 'ano'].drop_duplicates('provozovna_id').set_index('provozovna_id')
    rows = []
    for pid in b.index.difference(a.index):
        r = b.loc[pid]; rows.append(('nová', pid, r.provozovna_nazev, r.typ, r.ruian_obec, '', r.vykon_el_MW))
    for pid in a.index.difference(b.index):
        r = a.loc[pid]; rows.append(('ukončená', pid, r.provozovna_nazev, r.typ, r.ruian_obec, r.vykon_el_MW, ''))
    both = a.index.intersection(b.index)
    d = (b.loc[both].vykon_el_MW.astype(float) - a.loc[both].vykon_el_MW.astype(float)).abs() > 1e-6
    for pid in both[d.values]:
        r = b.loc[pid]; rows.append(('změna výkonu', pid, r.provozovna_nazev, r.typ, r.ruian_obec, a.loc[pid].vykon_el_MW, r.vykon_el_MW))
    return pd.DataFrame(rows, columns=['zmena', 'provozovna_id', 'nazev', 'typ', 'obec', 'vykon_puvodni_MW', 'vykon_novy_MW'])
