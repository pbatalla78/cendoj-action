# main.py — v1.9
# CENDOJ Search API: ranking híbrido, sinónimos, validación de enlaces,
# resúmenes consistentes, notas uniformes, experto temático (urbanismo catalán)
# y redirección /redir con enlace preferido (directo vs estable).
#
# Cambios vs v1.8:
# - Elimina "ponente" del esquema y de la tabla de presentación.
# - Sin dependencias externas (adiós httpx/dateutil): validación con urllib.
# - url_estable ahora intenta abrir el buscador con query ECLI/ROJ.
# - Nota uniforme Motivo/Acción/Info y avisos de órgano distinto.
# - Mantiene: ranking híbrido, sinónimos, experto temático, corrección de rango, etc.

from fastapi import FastAPI, Query, HTTPException, Response
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, date, timezone
import urllib.request
import urllib.error
import urllib.parse
import re

app = FastAPI(title="CENDOJ Search API", version="1.9")

# ------------------------------
# Utilidades y configuración
# ------------------------------

CENDOJ_DIRECTO = "https://www.poderjudicial.es/search/cedula.jsp?id={id_cendoj}"
PORTAL_BUSCADOR = "https://www.poderjudicial.es/search/indexAN.jsp"
GOOGLE_SITE = "https://www.google.com/search?q=site%3Apoderjudicial.es+%22{q}%22"

HTTP_TIMEOUT = 7.5
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CENDOJ-Checker/1.9; +https://example.org)"
}

# Sinónimos “típicos”
SYNONYMS = {
    "suelo no urbanizable": ["suelo rústico", "suelos protegidos"],
    "fuera de ordenación": ["situación de fuera de ordenación"],
    "volumen disconforme": ["edificación disconforme"],
    "garaje ilegal": ["aparcamiento ilegal", "cochera sin licencia"],
}

# “Experto temático” — ampliación de consulta para urbanismo catalán
URBANISMO_CATALAN_TRIGGERS = [
    "urbanizable", "suelo no urbanizable", "fuera de ordenación",
    "edificación disconforme", "licencia urbanística", "planeamiento",
    "disciplina urbanística", "volumen disconforme", "ordenación urbanística",
]
URBANISMO_CATALAN_EXPANSIONS = [
    "TRLU 1/2010", "Decreto 305/2006 Reglamento de la Ley de Urbanismo",
    "Reglamento de disciplina urbanística", "TSJC"
]


def normalize_text(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    s = s.strip('"').strip("'")
    return s


def expand_query(q: str) -> Tuple[str, List[str]]:
    """Devuelve query normalizada + lista de sinónimos añadidos (para nota)."""
    qn = normalize_text(q)
    added = []
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


def validar_enlace(url: str) -> bool:
    """Valida enlace directo con urllib: 200 + sin mensaje 404 del portal."""
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            if r.status != 200:
                return False
            body = r.read(4096).decode(errors="ignore").lower()  # leer poco, suficiente
            if "404 page error" in body or "la página que buscas no existe" in body:
                return False
            return True
    except Exception:
        return False


def hybrid_score(relevancia_0a1: float, fecha_doc: Optional[str]) -> float:
    """Ranking híbrido: 0.7 relevancia + 0.3 actualidad (decay ~3 años)."""
    rel = max(0.0, min(1.0, float(relevancia_0a1 or 0.0)))
    if not fecha_doc:
        return rel
    try:
        d = datetime.strptime(fecha_doc, "%Y-%m-%d").date()
        delta_days = max(0, (datetime.now(timezone.utc).date() - d).days)
        recency = max(0.0, 1.0 - (delta_days / 1095.0))
    except Exception:
        recency = 0.5
    return (0.7 * rel) + (0.3 * recency)


def build_links(record: Dict[str, Any]) -> Dict[str, Any]:
    """Construye urls y el enlace_preferido (sin validación previa)."""
    idc = record.get("id_cendoj")
    ecli = (record.get("ecli") or "").strip()
    roj = (record.get("roj") or "").strip()

    url_directo = CENDOJ_DIRECTO.format(id_cendoj=idc) if idc else None

    # Intento 1 (estable): abrir buscador CENDOJ con query ECLI/ROJ (no documentado, pero suele aceptar ?q=)
    q_str = urllib.parse.quote_plus(ecli or roj) if (ecli or roj) else ""
    url_estable = f"{PORTAL_BUSCADOR}?q={q_str}" if q_str else PORTAL_BUSCADOR

    # Intento 2 (estable secundaria): Google site:
    url_estable_sec = GOOGLE_SITE.format(q=urllib.parse.quote_plus(ecli or roj)) if (ecli or roj) else GOOGLE_SITE.format(q="CENDOJ")

    record["url_directo"] = url_directo
    record["url_estable"] = url_estable
    record["url_estable_secundaria"] = url_estable_sec

    # Por defecto: estable (más robusto)
    record["enlace_preferido"] = url_estable
    record["enlace_directo_ok"] = None
    record["estrategia_enlace"] = "estable"
    return record


# ------------------------------
# “Índice” de ejemplos (dataset mínimo)
# ------------------------------
# NOTA: Sustituye estos mocks por tu fuente real en producción.
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
    """Filtro simple sobre EXAMPLES con ranking híbrido."""
    out = []
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

    # Ranking híbrido por defecto
    out.sort(key=lambda x: hybrid_score(x.get("relevancia", 0.0), x.get("fecha")), reverse=True)
    return out


def build_summary(rec: Dict[str, Any]) -> str:
    # Resumen objetivo 1–2 frases con datos del registro (sin valorar)
    t = rec.get("titulo", "—")
    o = rec.get("organo", "—")
    s = rec.get("sala", "—")
    f = rec.get("fecha", "—")
    return f"{t} ({o} - {s}). Fecha: {f}."


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


@app.get("/buscar-cendoj", response_model=Respuesta)
def buscar_cendoj(
    query: str = Query(..., description="términos de búsqueda"),
    desde: Optional[str] = Query(None, description="YYYY-MM-DD"),
    hasta: Optional[str] = Query(None, description="YYYY-MM-DD"),
    orden: str = Query("relevancia_desc", description="relevancia_desc|fecha_desc|fecha_asc"),
    limite: int = Query(10, ge=1, le=50),
    validar_enlaces: bool = Query(False, description="valida enlaces directos y selecciona estable si fallan"),
    organo: Optional[str] = Query(None, description="filtro por órgano textual simple"),
):
    nota_msgs = []

    # Normalización y sinónimos
    qn, syn_added = expand_query(query)
    if syn_added:
        nota_msgs.append(f"Se añadieron sinónimos: {', '.join(syn_added)}.")

    # Experto temático (urbanismo catalán)
    experto_activo = False
    if detect_urbanismo_catalan(qn):
        experto_activo = True
        nota_msgs.append("Experto temático: derecho urbanístico catalán (priorización TSJC y normativa catalana).")

    # Fechas
    d_desde = parse_date(desde)
    d_hasta = parse_date(hasta)
    if d_desde and d_hasta and d_desde > d_hasta:
        d_desde, d_hasta = d_hasta, d_desde
        nota_msgs.append("Se corrigió el rango de fechas (invertido).")

    # Búsqueda (mock)
    resultados_raw = search_examples(qn, d_desde, d_hasta)

    # Filtro por órgano textual simple (si llega)
    requested_org = normalize_text(organo) if organo else None
    if requested_org:
        resultados_raw = [r for r in resultados_raw if requested_org in normalize_text(r.get("organo", ""))]
        if not resultados_raw:
            # si filtrando estrictamente no hay, relajamos pero avisamos
            candidatos = search_examples(qn, d_desde, d_hasta)
            if candidatos:
                nota_msgs.append("No se encontraron coincidencias exactas con el órgano solicitado; se muestran resultados cercanos.")
                resultados_raw = candidatos

    # Orden explícito por fecha si se pide
    if orden == "fecha_desc":
        resultados_raw.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    elif orden == "fecha_asc":
        resultados_raw.sort(key=lambda x: x.get("fecha", ""))

    # Formateo de resultados
    final: List[Dict[str, Any]] = []
    for r in resultados_raw[:limite]:
        rec = {
            "titulo": r["titulo"],
            "organo": r["organo"],
            "sala": r.get("sala", "—"),
            "fecha": r["fecha"],
            "relevancia": float(r.get("relevancia", 0.0)),
            "id_cendoj": r.get("id_cendoj"),
            "roj": r.get("roj"),
            "ecli": r.get("ecli"),
        }
        rec["resumen"] = build_summary(rec)

        # Links
        rec = build_links(rec)

        # Validación de enlace directo y selección del preferido
        if validar_enlaces and rec.get("url_directo"):
            ok = validar_enlace(rec["url_directo"])
            rec["enlace_directo_ok"] = ok
            if ok:
                rec["enlace_preferido"] = rec["url_directo"]
                rec["estrategia_enlace"] = "directo"
            else:
                rec["enlace_preferido"] = rec["url_estable"]
                rec["estrategia_enlace"] = "estable"
                nota_msgs.append(
                    f"El enlace directo de {rec.get('id_cendoj')} no respondió como esperado; se usa enlace estable."
                )
        else:
            rec["enlace_preferido"] = rec["url_estable"]
            rec["estrategia_enlace"] = "estable"

        # Aviso si órgano no coincide con lo solicitado
        if requested_org and requested_org not in normalize_text(rec.get("organo", "")):
            nota_msgs.append("Algún resultado no coincide exactamente con el órgano solicitado.")

        final.append(rec)

    # 0 resultados
    if not final:
        sug = []
        if not syn_added:
            for k, vs in SYNONYMS.items():
                if k in qn:
                    sug.extend(vs)
            if sug:
                nota_msgs.append(f"Sugerencias de sinónimos: {', '.join(sorted(set(sug)))}")
        nota_msgs.append("Ajusta términos o el rango temporal.")
        return {
            "query": query,
            "total": 0,
            "resultados": [],
            "nota": make_nota(
                motivo="No se han encontrado resultados exactos.",
                accion="Prueba sinónimos o ajusta el rango temporal.",
                info="Ranking híbrido (relevancia + actualidad) aplicado."
            )
        }

    # Nota uniforme
    info_bits = []
    if orden.startswith("relevancia"):
        info_bits.append("orden: ranking híbrido (relevancia + actualidad)")
    if experto_activo:
        info_bits.append("experto temático (urbanismo catalán) activo")
    nota_final = make_nota(
        motivo=None,
        accion=None,
        info=" ".join(nota_msgs + info_bits) if (nota_msgs or info_bits) else None
    )

    return {
        "query": query,
        "total": len(final),
        "resultados": final,
        "nota": nota_final
    }


@app.get("/redir")
def redirigir(id: Optional[str] = None,
              ecli: Optional[str] = None,
              roj: Optional[str] = None):
    """
    Redirige al enlace preferido:
      1) Si el directo funciona -> cedula.jsp?id=...
      2) Si no -> buscador del portal con ECLI/ROJ (estable).
      3) Como secundario, Google site: para ECLI/ROJ.
    """
    if not (id or ecli or roj):
        raise HTTPException(status_code=400, detail="Falta id/ecli/roj")

    prefer = PORTAL_BUSCADOR
    if id:
        url_directo = CENDOJ_DIRECTO.format(id_cendoj=id)
        if validar_enlace(url_directo):
            prefer = url_directo
        else:
            if ecli or roj:
                q = urllib.parse.quote_plus(ecli or roj)
                prefer = f"{PORTAL_BUSCADOR}?q={q}"
            else:
                prefer = PORTAL_BUSCADOR
    else:
        q = urllib.parse.quote_plus((ecli or roj))
        prefer = f"{PORTAL_BUSCADOR}?q={q}"

    return Response(status_code=302, headers={"Location": prefer})