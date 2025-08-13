from fastapi import FastAPI, Query
from typing import List, Optional, Dict, Any
from datetime import datetime
import uvicorn
import re

# --- (Opcional) validación de enlaces directos ---
# Si httpx no está instalado, la validación se desactiva automáticamente.
try:
    import httpx  # pip install httpx
    _HTTPX_AVAILABLE = True
except Exception:
    _HTTPX_AVAILABLE = False

app = FastAPI(title="CENDOJ Search API", version="1.5")

# -------------------------------------------------------------------
# Mock de datos (sustituir por conector real a CENDOJ)
# -------------------------------------------------------------------
MOCK_DB: List[Dict[str, Any]] = [
    {
        "id_cendoj": "0801932001202400077",
        "roj": "STS 1234/2024",
        "ecli": "ECLI:ES:TS:2024:1234",
        "titulo": "Licencia urbanística en suelo no urbanizable: criterios recientes",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "ponente": "Ponente C",
        "fecha": "2024-02-12",
        "resumen": "Criterios sobre licencias en suelos no urbanizables (TSJC).",
        "relevancia": 0.76
    },
    {
        "id_cendoj": "28079130012022000456",
        "roj": "STS 456/2022",
        "ecli": "ECLI:ES:TS:2022:456",
        "titulo": "Sentencia ejemplo sobre 'fuera de ordenación' en suelo urbano",
        "organo": "Tribunal Supremo (TS)",
        "sala": "Sala Tercera (Cont.-Adm.)",
        "ponente": "Ponente B",
        "fecha": "2022-11-03",
        "resumen": "Criterios sobre situación de fuera de ordenación en suelo urbano (TS).",
        "relevancia": 0.82
    },
    {
        "id_cendoj": "08019320012023000123",
        "roj": "STS 789/2023",
        "ecli": "ECLI:ES:TS:2023:789",
        "titulo": "Sentencia ejemplo sobre volumen disconforme",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "ponente": "Ponente A",
        "fecha": "2023-05-10",
        "resumen": "Caso sobre edificación/volumen disconforme con el planeamiento (TSJC).",
        "relevancia": 0.18
    }
]

# -------------------------------------------------------------------
# Sinónimos + normalización
# -------------------------------------------------------------------
SYNONYMS: Dict[str, List[str]] = {
    "suelo no urbanizable": ["suelo rústico", "suelos protegidos", "suelo rustico"],
    "fuera de ordenación": ["fuera ordenación", "situación de fuera de ordenación", "edificación disconforme"],
    "volumen disconforme": ["alteración de volumen", "aumento de edificabilidad", "edificación disconforme"],
}

def normalize_text(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[“”\"'´`]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s

def expand_query(q: str) -> List[str]:
    qn = normalize_text(q)
    expanded = {qn}
    for key, syns in SYNONYMS.items():
        if key in qn:
            expanded.update([normalize_text(s) for s in syns])
    return list(expanded)

# -------------------------------------------------------------------
# Ranking híbrido (relevancia + match textual)
# -------------------------------------------------------------------
def hybrid_rank(results: List[Dict[str, Any]], terms: List[str]) -> List[Dict[str, Any]]:
    for r in results:
        title_norm = normalize_text(r.get("titulo", ""))
        match_score = sum(1 for t in terms if t in title_norm)
        base_rel = float(r.get("relevancia", 0.0))
        # 70% relevancia declarada + 30% coincidencia textual (normalizada)
        r["_score"] = (base_rel * 0.70) + (min(match_score, 3) / 3.0) * 0.30
    return sorted(results, key=lambda x: x["_score"], reverse=True)

# -------------------------------------------------------------------
# Filtros temporales explícitos
# -------------------------------------------------------------------
def apply_date_filter(results: List[Dict[str, Any]], desde: Optional[str], hasta: Optional[str]) -> List[Dict[str, Any]]:
    if not desde and not hasta:
        return results

    def parse_date(d: str) -> datetime:
        return datetime.strptime(d, "%Y-%m-%d")

    out = []
    for r in results:
        try:
            f = parse_date(r["fecha"])
        except Exception:
            # si el dato está malformado, lo excluimos del filtro (o lo incluimos por defecto)
            continue
        if desde and f < parse_date(desde):
            continue
        if hasta and f > parse_date(hasta):
            continue
        out.append(r)
    return out

# -------------------------------------------------------------------
# Estrategia de enlaces (híbrida)
# - url_directo: cedula.jsp?id=...
# - url_estable: búsqueda por ECLI (o por Id CENDOJ si no hay ECLI)
# Validación opcional: HEAD al url_directo para marcar si está caído (404, etc.)
# -------------------------------------------------------------------
def build_links(r: Dict[str, Any]) -> Dict[str, Optional[str]]:
    base_direct = f"https://www.poderjudicial.es/search/cedula.jsp?id={r['id_cendoj']}"
    ecli = r.get("ecli")
    if ecli:
        stable = f"https://www.google.com/search?q=site%3Apoderjudicial.es+%22{ecli}%22"
    else:
        # fallback: búsqueda por id_cendoj si no hay ECLI
        stable = f"https://www.google.com/search?q=site%3Apoderjudicial.es+%22{r['id_cendoj']}%22"
    return {"url_directo": base_direct, "url_estable": stable}

def check_direct_link(url: str, timeout_s: float = 2.5) -> Optional[int]:
    if not _HTTPX_AVAILABLE:
        return None
    try:
        # HEAD suele ser suficiente y rápido; si el servidor no lo soporta, httpx hará GET (follow redirects False)
        with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
            resp = client.head(url)
            return resp.status_code
    except Exception:
        return 0  # error de red u otros

# -------------------------------------------------------------------
# Endpoint principal
# -------------------------------------------------------------------
@app.get("/buscar-cendoj")
def buscar_cendoj(
    query: str = Query(..., description="Texto a buscar"),
    desde: Optional[str] = Query(None, description="Fecha inicial (YYYY-MM-DD)"),
    hasta: Optional[str] = Query(None, description="Fecha final (YYYY-MM-DD)"),
    orden: Optional[str] = Query("relevancia_desc", description="Orden: relevancia_desc | fecha_desc | fecha_asc"),
    limite: Optional[int] = Query(10, ge=1, le=50, description="Número máximo de resultados"),
    validar_enlaces: Optional[bool] = Query(False, description="Si True, verifica si el enlace directo responde (HEAD)")
):
    notas: List[str] = []
    terms = expand_query(query)
    # Si se expandieron sinónimos, avisamos (sin ruido si es el mismo término)
    if len(terms) > 1:
        notas.append("Se usaron sinónimos comunes para ampliar la coincidencia.")

    # Corrección de rango de fechas invertido
    if desde and hasta:
        try:
            if datetime.strptime(desde, "%Y-%m-%d") > datetime.strptime(hasta, "%Y-%m-%d"):
                desde, hasta = hasta, desde
                notas.append("Se corrigió el rango de fechas (invertido).")
        except Exception:
            pass  # si no son fechas válidas, ya fallará el filtro de manera inocua

    # Matching muy simple sobre el mock (en producción reemplazar por consulta real)
    results = []
    for r in MOCK_DB:
        title_norm = normalize_text(r.get("titulo", ""))
        if any(t in title_norm for t in terms):
            results.append(r.copy())

    # Filtros temporales
    results = apply_date_filter(results, desde, hasta)

    # Ranking híbrido + orden
    results = hybrid_rank(results, terms)
    if orden == "fecha_desc":
        results.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    elif orden == "fecha_asc":
        results.sort(key=lambda x: x.get("fecha", ""))
    # por defecto: relevancia_desc ya lo aplica hybrid_rank

    if not results:
        return {
            "query": query,
            "total": 0,
            "resultados": [],
            "nota": " ".join(notas) if notas else "No se han encontrado resultados exactos. Prueba con sinónimos o ajusta el rango de fechas."
        }

    formatted: List[Dict[str, Any]] = []
    broken_count = 0

    for r in results[:limite]:
        # Resúmenes consistentes (si faltara en la fuente)
        resumen = r.get("resumen") or "Resumen no disponible con precisión."

        links = build_links(r)
        enlace_valido: Optional[bool] = None
        estrategia = "directo"

        if validar_enlaces:
            status = check_direct_link(links["url_directo"])
            # status None => no se pudo validar (httpx no disponible). 0/error o >=400 => roto.
            if status is None:
                enlace_valido = None
            elif status == 0 or (status is not None and status >= 400):
                enlace_valido = False
                estrategia = "estable"
                broken_count += 1
            else:
                enlace_valido = True

        resultado = {
            "titulo": r.get("titulo"),
            "organo": r.get("organo"),
            "sala": r.get("sala") or "",
            "ponente": r.get("ponente") or "",
            "fecha": r.get("fecha"),
            "relevancia": round(float(r.get("relevancia", 0.0)) * 100),  # en %
            "resumen": resumen,
            "id_cendoj": r.get("id_cendoj"),
            "roj": r.get("roj"),
            "ecli": r.get("ecli"),
            # Estrategia híbrida de enlaces
            "url_directo": links["url_directo"],
            "url_estable": links["url_estable"],
            "enlace_preferido": links["url_directo"] if estrategia == "directo" else links["url_estable"],
            "estrategia_enlace": estrategia,
        }
        if validar_enlaces:
            resultado["enlace_directo_ok"] = enlace_valido

        formatted.append(resultado)

    if validar_enlaces:
        if broken_count > 0:
            notas.append(f"{broken_count} enlace(s) directo(s) devolvieron error; se sugiere usar el enlace estable.")

    return {
        "query": query,
        "total": len(results),
        "resultados": formatted,
        "nota": " ".join(notas) if notas else None
    }

# -------------------------------------------------------------------
# Salud
# -------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "version": app.version, "httpx": _HTTPX_AVAILABLE}

# -------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)