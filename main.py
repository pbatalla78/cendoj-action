# main.py — v1.8 (final)
# CENDOJ Search API
# - Ranking híbrido (relevancia + actualidad)
# - Sinónimos y experto temático (urbanismo catalán)
# - Validación de enlaces SIN dependencias externas (httpx opcional; stdlib si falta)
# - Enlace preferido robusto (directo si valida; si no, estable al buscador)
# - Resúmenes consistentes + notas uniformes
# - Filtros temporales explícitos y corrección de rango invertido
# - Campo "Ponente" eliminado del modelo y de la salida

from fastapi import FastAPI, Query, HTTPException, Response
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, date, timezone
import asyncio
import re

# Para validación de enlaces con stdlib si httpx no está instalado
from urllib.parse import quote_plus
try:
    import httpx  # opcional
    HAVE_HTTPX = True
except Exception:
    import urllib.request
    HAVE_HTTPX = False

app = FastAPI(title="CENDOJ Search API", version="1.8")

# ------------------------------
# Configuración
# ------------------------------
CENDOJ_DIRECTO = "https://www.poderjudicial.es/search/cedula.jsp?id={id_cendoj}"
PORTAL_BUSCADOR = "https://www.poderjudicial.es/search/indexAN.jsp"
GOOGLE_SITE = "https://www.google.com/search?q=site%3Apoderjudicial.es+%22{ecli}%22"

HTTP_TIMEOUT = 7.5
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CENDOJ-Checker/1.8; +https://example.org)"
}

# Sinónimos “típicos”
SYNONYMS = {
    "suelo no urbanizable": ["suelo rústico", "suelos protegidos"],
    "fuera de ordenación": ["situación de fuera de ordenación"],
    "volumen disconforme": ["edificación disconforme"],
    "garaje ilegal": ["aparcamiento ilegal", "cochera sin licencia"],
}

# Experto temático (urbanismo catalán)
URBANISMO_CATALAN_TRIGGERS = [
    "urbanizable", "suelo no urbanizable", "fuera de ordenación",
    "edificación disconforme", "licencia urbanística", "planeamiento",
    "disciplina urbanística", "volumen disconforme", "ordenación urbanística",
]

# ------------------------------
# Utilidades
# ------------------------------
def normalize_text(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    s = s.strip('"').strip("'")
    return s

def expand_query(q: str) -> Tuple[str, List[str]]:
    qn = normalize_text(q)
    added: List[str] = []
    for k, vs in SYNONYMS.items():
        if k in qn:
            for v in vs:
                if v not in qn:
                    added.append(v)
    return qn, added

def detect_urbanismo_catalan(qn: str) -> bool:
    return any(t in qn for t in URBANISMO_CATALAN_TRIGGERS)

def parse_date(d: Optional[str]) -> Optional[date]:
    if not d:
        return None
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return None

def make_nota(motivo: Optional[str] = None,
              accion: Optional[str] = None,
              info: Optional[str] = None) -> Optional[str]:
    parts = []
    if motivo:
        parts.append(f"Motivo: {motivo}")
    if accion:
        parts.append(f"Acción: {accion}")
    if info:
        parts.append(f"Info: {info}")
    return f"⚠️ Nota: " + " ".join(parts) if parts else None

# Validación de enlace: httpx si está; si no, stdlib
async def _validate_with_httpx(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=HTTP_HEADERS, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return False
            text = r.text.lower()
            if "404 page error" in text or "la página que buscas no existe" in text:
                return False
            if len(text) < 120:
                return False
            return True
    except Exception:
        return False

def _validate_with_urllib(url: str) -> bool:
    try:
        req = urllib.request.Request(url, headers=HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                return False
            body = resp.read(4096).decode("utf-8", errors="ignore").lower()
            if "404 page error" in body or "la página que buscas no existe" in body:
                return False
            if len(body) < 60:
                return False
            return True
    except Exception:
        return False

async def validar_enlace(url: str) -> bool:
    if HAVE_HTTPX:
        return await _validate_with_httpx(url)
    return await asyncio.to_thread(_validate_with_urllib, url)

def hybrid_score(relevancia_0a1: float, fecha_doc: Optional[str]) -> float:
    rel = max(0.0, min(1.0, relevancia_0a1))
    if not fecha_doc:
        return rel
    try:
        d = datetime.strptime(fecha_doc, "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        delta_days = max(0, (today - d).days)
        recency = max(0.0, 1.0 - (delta_days / 1095.0))  # ~3 años
    except Exception:
        recency = 0.5
    return (0.7 * rel) + (0.3 * recency)

def build_links(record: Dict[str, Any]) -> Dict[str, Any]:
    idc = record.get("id_cendoj")
    ecli = record.get("ecli")
    # Enlace directo a la cédula
    url_directo = CENDOJ_DIRECTO.format(id_cendoj=idc) if idc else None
    # Estable (robusto): landing del buscador; alternativo Google por ECLI
    url_estable = PORTAL_BUSCADOR
    url_estable_sec = GOOGLE_SITE.format(ecli=quote_plus(ecli)) if ecli else None

    record["url_directo"] = url_directo
    record["url_estable"] = url_estable
    record["url_estable_secundaria"] = url_estable_sec
    record["enlace_preferido"] = url_estable  # por defecto robusto
    record["enlace_directo_ok"] = None
    record["estrategia_enlace"] = "estable"
    return record

def build_summary(rec: Dict[str, Any]) -> str:
    t = rec.get("titulo", "—")
    o = rec.get("organo", "—")
    s = rec.get("sala", "—")
    f = rec.get("fecha", "—")
    return f"{t} ({o} - {s}). Fecha: {f}."

# ------------------------------
# Dataset de ejemplo (mock)
# Sustituye por tu fuente real si aplica
# ------------------------------
EXAMPLES = [
    {
        "id_cendoj": "0801932001202400077",
        "titulo": "Licencia urbanística en suelo no urbanizable: criterios recientes",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "fecha": "2024-02-12",
        "relevancia": 0.76,
        "ecli": "ECLI:ES:TS:2024:1234",
        "roj": "STS 1234/2024",
        "tags": ["urbanizable", "suelo no urbanizable"],
    },
    {
        "id_cendoj": "28079130012022000456",
        "titulo": "Sentencia ejemplo sobre 'fuera de ordenación' en suelo urbano",
        "organo": "Tribunal Supremo (TS)",
        "sala": "Sala Tercera (Cont.-Adm.)",
        "fecha": "2022-11-03",
        "relevancia": 0.82,
        "ecli": "ECLI:ES:TS:2022:456",
        "roj": "STS 456/2022",
        "tags": ["ordenación", "fuera de ordenación"],
    },
    {
        "id_cendoj": "08019320012023000123",
        "titulo": "Sentencia ejemplo sobre 'volumen disconforme'",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "fecha": "2023-05-10",
        "relevancia": 0.18,
        "ecli": "ECLI:ES:TSJC:2023:789",
        "roj": "STSJC 789/2023",
        "tags": ["ordenación", "volumen disconforme"],
    },
]

def search_examples(qn: str,
                    desde: Optional[date],
                    hasta: Optional[date]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in EXAMPLES:
        hay = (
            (qn in normalize_text(r["titulo"])) or
            any(t in qn for t in r.get("tags", [])) or
            (qn in normalize_text(" ".join(r.get("tags", []))))
        )
        if not hay:
            continue
        f = parse_date(r["fecha"])
        if desde and f and f < desde:
            continue
        if hasta and f and f > hasta:
            continue
        out.append(r.copy())
    out.sort(key=lambda x: hybrid_score(x.get("relevancia", 0.0), x.get("fecha")), reverse=True)
    return out

# ------------------------------
# Modelos Pydantic (sin Ponente)
# ------------------------------
class Resultado(BaseModel):
    titulo: str
    organo: str
    sala: str
    fecha: str
    relevancia: float
    resumen: Optional[str]
    id_cendoj: Optional[str]
    roj: Optional[str]
    ecli: Optional[str]
    url_directo: Optional[str]
    url_estable: Optional[str]
    url_estable_secundaria: Optional[str]
    enlace_preferido: Optional[str]
    enlace_directo_ok: Optional[bool]
    estrategia_enlace: Optional[str]

class Respuesta(BaseModel):
    query: str
    total: int
    resultados: List[Resultado]
    nota: Optional[str]

# ------------------------------
# Endpoints
# ------------------------------
@app.get("/buscar-cendoj", response_model=Respuesta)
async def buscar_cendoj(
    query: str = Query(..., description="términos de búsqueda"),
    desde: Optional[str] = Query(None, description="YYYY-MM-DD"),
    hasta: Optional[str] = Query(None, description="YYYY-MM-DD"),
    orden: str = Query("relevancia_desc", description="relevancia_desc|fecha_desc|fecha_asc"),
    limite: int = Query(10, ge=1, le=50),
    validar_enlaces: bool = Query(False, description="valida enlaces directos y usa estable si fallan"),
    organo: Optional[str] = Query(None, description="filtro textual simple por órgano"),
):
    notas: List[str] = []

    # Normalización + sinónimos
    qn, syn_added = expand_query(query)
    if syn_added:
        notas.append(f"Se añadieron sinónimos: {', '.join(syn_added)}.")

    # Experto temático
    experto_activo = False
    if detect_urbanismo_catalan(qn):
        experto_activo = True
        notas.append("Experto temático: derecho urbanístico catalán (priorización de criterios/terminología).")

    # Fechas (y corrección si invertidas)
    d_desde = parse_date(desde)
    d_hasta = parse_date(hasta)
    if d_desde and d_hasta and d_desde > d_hasta:
        d_desde, d_hasta = d_hasta, d_desde
        notas.append("Se corrigió el rango de fechas (invertido).")

    # Búsqueda (mock sobre EXAMPLES)
    resultados_raw = search_examples(qn, d_desde, d_hasta)

    # Filtro por órgano
    if organo:
        org_n = normalize_text(organo)
        resultados_raw = [r for r in resultados_raw if org_n in normalize_text(r.get("organo", ""))]

    # Orden alternativo
    if orden == "fecha_desc":
        resultados_raw.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    elif orden == "fecha_asc":
        resultados_raw.sort(key=lambda x: x.get("fecha", ""))

    # Preparación de salida
    final: List[Dict[str, Any]] = []
    for r in resultados_raw[:limite]:
        rec: Dict[str, Any] = {
            "titulo": r["titulo"],
            "organo": r["organo"],
            "sala": r.get("sala", "—"),
            "fecha": r["fecha"],
            "relevancia": r.get("relevancia", 0.0),
            "id_cendoj": r.get("id_cendoj"),
            "roj": r.get("roj"),
            "ecli": r.get("ecli"),
        }
        rec["resumen"] = build_summary(rec)
        rec = build_links(rec)

        # Validación de enlace directo y selección de preferido
        if validar_enlaces and rec.get("url_directo"):
            ok = await validar_enlace(rec["url_directo"])
            rec["enlace_directo_ok"] = ok
            if ok:
                rec["enlace_preferido"] = rec["url_directo"]
                rec["estrategia_enlace"] = "directo"
            else:
                rec["enlace_preferido"] = PORTAL_BUSCADOR
                rec["estrategia_enlace"] = "estable"
                notas.append(f"El enlace directo de {rec.get('id_cendoj')} no respondió como esperado; se usa enlace estable.")
        else:
            rec["enlace_preferido"] = PORTAL_BUSCADOR
            rec["estrategia_enlace"] = "estable"

        final.append(rec)

    # Sin resultados: propuestas y nota uniforme
    if not final:
        sugerencias = []
        for k, vs in SYNONYMS.items():
            if k in qn:
                sugerencias.extend(vs)
        info = "Ranking híbrido (relevancia + actualidad) aplicado."
        if sugerencias:
            info += f" Sugerencias de sinónimos: {', '.join(sorted(set(sugerencias)))}."
        return {
            "query": query,
            "total": 0,
            "resultados": [],
            "nota": make_nota(
                motivo="No se han encontrado resultados exactos.",
                accion="Prueba sinónimos o ajusta el rango temporal.",
                info=info
            ),
        }

    info_bits = []
    if orden.startswith("relevancia"):
        info_bits.append("orden: ranking híbrido (relevancia + actualidad)")
    if experto_activo:
        info_bits.append("experto temático (urbanismo catalán) activo")

    nota_final = make_nota(info="; ".join(notas + info_bits)) if (notas or info_bits) else None

    return {
        "query": query,
        "total": len(final),
        "resultados": final,
        "nota": nota_final,
    }

@app.get("/redir")
async def redirigir(id: Optional[str] = None,
                    ecli: Optional[str] = None,
                    roj: Optional[str] = None):
    """
    Redirige al mejor enlace disponible:
      1) Si el directo valida -> cedula.jsp?id=...
      2) Si no -> buscador del portal (estable).
      3) Si hay ECLI -> como pista adicional, el cliente puede usar Google (no se redirige aquí).
    """
    if not (id or ecli or roj):
        raise HTTPException(status_code=400, detail="Falta id/ecli/roj")

    prefer = PORTAL_BUSCADOR
    if id:
        url_directo = CENDOJ_DIRECTO.format(id_cendoj=id)
        if await validar_enlace(url_directo):
            prefer = url_directo

    return Response(status_code=302, headers={"Location": prefer})

# Salud mínima
@app.get("/")
def root():
    return {"status": "ok", "service": "cendoj-action", "version": "1.8"}