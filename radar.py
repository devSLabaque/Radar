import math
import requests
import pandas as pd
import streamlit as st
import folium
from streamlit_folium import st_folium

st.set_page_config(page_title="Radar ADS-B", page_icon="🛩️", layout="wide")
API = "https://api.adsb.lol"
PRESETS = {
    "Paris": (48.85, 2.35), "Londres": (51.5, -0.45), "Francfort": (50.03, 8.57),
    "Madrid": (40.47, -3.56), "Rome": (41.8, 12.25), "New York": (40.64, -73.78),
    "Personnalisé": None,
}
PLANE = ('<svg viewBox="0 0 24 24" width="20" height="20" style="transform:rotate({r}deg);filter:drop-shadow(0 0 2px #000)">'
         '<path fill="{c}" d="M12 1l2.3 8 8.2 4.5v2.2l-8.2-2.4-.5 6 2.5 1.9V23L12 22l-4.3 1v-1.8l2.5-1.9-.5-6-8.2 2.4v-2.2l8.2-4.5z"/></svg>')

# ---------- utilitaires ----------
def nm(a1, o1, a2, o2):
    r = math.pi / 180
    h = math.sin((a2 - a1) * r / 2) ** 2 + math.cos(a1 * r) * math.cos(a2 * r) * math.sin((o2 - o1) * r / 2) ** 2
    return 6371 / 1.852 * 2 * math.asin(math.sqrt(h))

def gc(a, b, n=48):
    """Points d'un grand cercle entre a et b (lat, lon)."""
    r = math.pi / 180
    p1, l1, p2, l2 = a[0] * r, a[1] * r, b[0] * r, b[1] * r
    d = 2 * math.asin(math.sqrt(math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2))
    if d == 0:
        return [a, b]
    out = []
    for i in range(n + 1):
        f = i / n
        A, B = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
        x = A * math.cos(p1) * math.cos(l1) + B * math.cos(p2) * math.cos(l2)
        y = A * math.cos(p1) * math.sin(l1) + B * math.cos(p2) * math.sin(l2)
        z = A * math.sin(p1) + B * math.sin(p2)
        out.append((math.atan2(z, math.hypot(x, y)) / r, math.atan2(y, x) / r))
    return out

def color(alt):
    if alt < 1000: return "#ff9f43"
    if alt < 10000: return "#f7d94c"
    if alt < 20000: return "#7ee05a"
    if alt < 30000: return "#37d6c5"
    if alt < 40000: return "#4aa8ff"
    return "#d97bff"

def fl(alt, gnd):
    return "GND" if gnd else f"FL{round(alt / 100):03d}"

# ---------- données ----------
@st.cache_data(ttl=5, show_spinner=False)
def fetch_aircraft(lat, lon, radius):
    r = requests.get(f"{API}/v2/point/{lat:.3f}/{lon:.3f}/{radius}", timeout=10)
    r.raise_for_status()
    rows = []
    for p in r.json().get("ac", []):
        if p.get("lat") is None or p.get("lon") is None:
            continue
        gnd = p.get("alt_baro") == "ground"
        alt = 0 if gnd or not isinstance(p.get("alt_baro"), (int, float)) else p["alt_baro"]
        rows.append(dict(hex=p["hex"], cs=(p.get("flight") or "").strip(), reg=p.get("r") or "", type=p.get("t") or "",
                         alt=alt, gnd=gnd, gs=p.get("gs") or 0, trk=p.get("track") or p.get("true_heading") or 0,
                         vs=p.get("baro_rate") or p.get("geom_rate") or 0, sq=p.get("squawk") or "",
                         lat=p["lat"], lon=p["lon"]))
    return pd.DataFrame(rows)

def update_routes(df):
    """Récupère les plans de vol (aéroports) des indicatifs encore inconnus."""
    cache = st.session_state.setdefault("routes", {})
    need = df[df.cs.str.match(r"^[A-Z]{3}\d") & ~df.cs.isin(cache.keys())].head(100)
    if need.empty:
        return
    for c in need.cs:
        cache[c] = None
    try:
        r = requests.post(f"{API}/api/0/routeset", timeout=10, json={
            "planes": [{"callsign": c, "lat": la, "lng": lo} for c, la, lo in zip(need.cs, need.lat, need.lon)]})
        for x in r.json():
            ap = x.get("_airports") or []
            if len(ap) >= 2:
                cache[x["callsign"]] = dict(dep=ap[0], arr=ap[-1], stops=ap[1:-1])
    except Exception:
        for c in need.cs:
            cache.pop(c, None)

# ---------- barre latérale ----------
with st.sidebar:
    st.title("🛩️ Radar ADS-B")
    zone = st.selectbox("Zone", list(PRESETS))
    lat0, lon0 = PRESETS[zone] or (46.5, 2.5)
    if zone == "Personnalisé":
        c1, c2 = st.columns(2)
        lat0 = c1.number_input("Lat", value=46.5, format="%.3f")
        lon0 = c2.number_input("Lon", value=2.5, format="%.3f")
    radius = st.slider("Rayon (NM)", 25, 250, 150, 25)
    auto = st.toggle("Rafraîchissement auto", True)
    every = st.slider("Période (s)", 5, 30, 8) if auto else None

    st.subheader("Filtres")
    q = st.text_input("Indicatif / immat / type / hex").strip().upper()
    flr = st.slider("Niveau de vol", 0, 600, (0, 600), 10)
    gs_min = st.number_input("Vitesse sol min (kt)", 0, 700, 0, 10)
    c1, c2 = st.columns(2)
    f_dep = c1.text_input("Départ (ICAO)").strip().upper()
    f_arr = c2.text_input("Arrivée (ICAO)").strip().upper()
    f_sq = st.text_input("Squawk commence par").strip()
    phase = st.radio("Phase", ["Toutes", "Montée", "Croisière", "Descente"], horizontal=True)
    hide_gnd = st.checkbox("Masquer les avions au sol")
    only_rt = st.checkbox("Seulement avec plan de vol connu")
    only_emg = st.checkbox("⚠️ Urgences (7500/7600/7700)")

    st.subheader("Affichage")
    s_lbl = st.toggle("Étiquettes", True)
    s_trail = st.toggle("Trails", True)
    s_plans = st.toggle("Tous les plans de vol", False)
    s_rings = st.toggle("Cercles de portée", False)

# ---------- cœur ----------
@st.fragment(run_every=every)
def radar():
    try:
        df = fetch_aircraft(lat0, lon0, radius)
    except Exception as e:
        st.error(f"Erreur réseau : {e}")
        return
    if df.empty:
        st.warning("Aucun appareil dans la zone.")
        return

    update_routes(df)
    routes = st.session_state.get("routes", {})
    df["rt"] = df.cs.map(lambda c: routes.get(c))
    df["dep"] = df.rt.map(lambda r: r["dep"].get("icao", "") if r else "")
    df["arr"] = df.rt.map(lambda r: r["arr"].get("icao", "") if r else "")

    hist = st.session_state.setdefault("hist", {})
    for h, la, lo in zip(df.hex, df.lat, df.lon):
        t = hist.setdefault(h, [])
        if not t or t[-1] != (la, lo):
            t.append((la, lo))
            del t[:-40]

    # filtres
    m = df.alt.between(flr[0] * 100, flr[1] * 100) | df.gnd
    if flr[0] > 0:
        m &= ~df.gnd
    if q:
        m &= df[["cs", "reg", "type", "hex"]].apply(lambda s: s.str.upper().str.contains(q, regex=False)).any(axis=1)
    m &= df.gs >= gs_min
    if f_dep: m &= df.dep.str.startswith(f_dep)
    if f_arr: m &= df.arr.str.startswith(f_arr)
    if f_sq: m &= df.sq.str.startswith(f_sq)
    if hide_gnd: m &= ~df.gnd
    if only_rt: m &= df.rt.notna()
    if only_emg: m &= df.sq.isin(["7500", "7600", "7700"])
    if phase == "Montée": m &= df.vs > 300
    elif phase == "Descente": m &= df.vs < -300
    elif phase == "Croisière": m &= df.vs.between(-300, 300)
    view = df[m].reset_index(drop=True)

    k1, k2, k3 = st.columns(3)
    k1.metric("Appareils zone", len(df))
    k2.metric("Après filtres", len(view))
    k3.metric("Plans de vol connus", int(view.rt.notna().sum()))

    left, right = st.columns([3, 2])

    # tableau + sélection
    with right:
        tab = pd.DataFrame({
            "Indicatif": view.cs.replace("", pd.NA).fillna(view.hex.str.upper()),
            "Type": view.type, "FL": [fl(a, g) for a, g in zip(view.alt, view.gnd)],
            "GS": view.gs.round().astype(int), "VS": view.vs.astype(int),
            "Dép": view.dep, "Arr": view.arr, "Squawk": view.sq})
        ev = st.dataframe(tab, on_select="rerun", selection_mode="single-row",
                          hide_index=True, use_container_width=True, height=330)
        sel = view.iloc[ev.selection.rows[0]] if ev.selection.rows else None

        if sel is not None:
            rt = sel.rt
            st.markdown(f"### {sel.cs or sel.hex.upper()}  \n`{sel.type}` {sel.reg}")
            info = {"Altitude": f"{fl(sel.alt, sel.gnd)} ({int(sel.alt)} ft)", "Vitesse sol": f"{int(sel.gs)} kt",
                    "Cap": f"{int(sel.trk)}°", "Taux vertical": f"{int(sel.vs)} ft/min", "Squawk": sel.sq or "--"}
            if rt:
                info["Départ"] = f"{rt['dep'].get('icao','')} {rt['dep'].get('name','')}"
                info["Arrivée"] = f"{rt['arr'].get('icao','')} {rt['arr'].get('name','')}"
                if rt["stops"]:
                    info["Escales"] = " ".join(s.get("icao", "") for s in rt["stops"])
                d = nm(sel.lat, sel.lon, rt["arr"]["lat"], rt["arr"]["lon"])
                info["Restant"] = f"{d:.0f} NM"
                if sel.gs > 40:
                    mn = d / sel.gs * 60
                    info["ETE"] = f"{int(mn // 60)}h{int(mn % 60):02d}"
            else:
                info["Plan de vol"] = "non disponible"
            st.table(pd.Series(info, name="Valeur"))

    # carte
    with left:
        mp = folium.Map(location=[lat0, lon0], zoom_start={25: 9, 50: 8, 100: 7}.get(radius, 6 if radius <= 150 else 5),
                        tiles="CartoDB dark_matter", control_scale=True)
        if s_rings:
            for r_ in range(50, radius + 1, 50):
                folium.Circle([lat0, lon0], radius=r_ * 1852, color="#37d6c5", weight=1, opacity=.3, fill=False).add_to(mp)
        for _, a in view.head(800).iterrows():
            emg = a.sq in ("7500", "7600", "7700")
            c = "#ff3b3b" if emg else color(a.alt)
            if s_trail and len(hist.get(a.hex, [])) > 1:
                folium.PolyLine(hist[a.hex], color=c, weight=1, opacity=.5).add_to(mp)
            if s_plans and a.rt:
                folium.PolyLine(gc((a.rt["dep"]["lat"], a.rt["dep"]["lon"]), (a.rt["arr"]["lat"], a.rt["arr"]["lon"]), 24),
                                color="#4aa8ff", weight=1, opacity=.3, dash_array="4 4").add_to(mp)
            html = f'<div style="position:relative;width:0;height:0">{PLANE.format(r=a.trk, c=c).replace("<svg", "<svg", 1)}'
            if s_lbl:
                arrow = "↑" if a.vs > 300 else "↓" if a.vs < -300 else "="
                l3 = f"<br>{a.dep or '?'}›{a.arr or '?'}" if a.rt else ""
                html += (f'<div style="position:absolute;left:12px;top:-12px;white-space:nowrap;font:10px/1.15 monospace;'
                         f'color:{c};text-shadow:0 0 3px #000,0 0 3px #000"><b>{a.cs or a.hex.upper()}</b><br>'
                         f'{fl(a.alt, a.gnd)} {arrow} {int(a.gs)}{l3}</div>')
            html += "</div>"
            folium.Marker([a.lat, a.lon], icon=folium.DivIcon(html=html, icon_size=(0, 0), icon_anchor=(0, 0)),
                          tooltip=a.cs or a.hex.upper()).add_to(mp)

        if sel is not None:
            folium.CircleMarker([sel.lat, sel.lon], radius=14, color="#fff", weight=2, fill=False).add_to(mp)
            if sel.rt:
                D = (sel.rt["dep"]["lat"], sel.rt["dep"]["lon"]); A = (sel.rt["arr"]["lat"], sel.rt["arr"]["lon"])
                folium.PolyLine(gc(D, (sel.lat, sel.lon), 24), color="#7b93a8", weight=2).add_to(mp)
                folium.PolyLine(gc((sel.lat, sel.lon), A, 24), color="#37d6c5", weight=2, dash_array="6 5").add_to(mp)
                for p, n in ((D, sel.rt["dep"].get("icao")), (A, sel.rt["arr"].get("icao"))):
                    folium.Marker(p, icon=folium.DivIcon(
                        html=f'<div style="color:#fff;font:11px monospace;text-shadow:0 0 3px #000">◆ {n}</div>')).add_to(mp)
        st_folium(mp, height=650, use_container_width=True, returned_objects=[], key="map")

radar()
st.caption("Données : adsb.lol (ADS-B communautaire). Les routes sont issues d'une base communautaire, pas de plans de vol déposés.")
