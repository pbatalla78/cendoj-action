from __future__ import annotations

import os
import unicodedata
from datetime import date, datetime
from typing import List, Optional, Tuple

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator

# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------
app = FastAPI(title="CENDOJ Action Mock", version="1.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Render + editor del GPT
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# Datos mock (coinciden con los usados en tus pruebas)
# -----------------------------------------------------------------------------
MOCK_DATA = [
    {
        "id_cendoj": "08019320012024000077",
        "titulo": "Licencia urbanística en suelo no urbanizable: criterios recientes",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "ponente": "Ponente C",
        "fecha": "2024-02-12",
        "relevancia": 0.76,
        "url": "https://www.poderjudicial.es/search/indexAN.jsp",
        "url_detalle": "https://www.poderjudicial.es/search/cedula.jsp?id=08019320012024000077",
    },
    {
        "id_cendoj": "08019320012023000123",
        "titulo": "Sentencia ejemplo sobre 'volumen disconforme'",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "ponente": "Ponente A",
        "fecha": "2023-05-10",
        "relevancia": 0.18,
        "url": "https://www.poderjudicial.es/search/indexAN.jsp",
        "url_detalle": "https://www.poderjudicial.es/search/cedula.jsp?id=08019320012023000123",
    },
    {
        "id_cendoj": "28079130012022000456",
        "titulo": "Sentencia ejemplo sobre 'fuera de ordenación' en suelo urbano",
        "organo": "Tribunal Supremo (TS)",
        "sala": "Sala Tercera (Cont.-Adm.)",
        "ponente": "Ponente B",
        "fecha": "2022-11-03",
        "relevancia": 0.82,
        "url": "https://www.poderjudicial.es/search/indexAN.jsp",
        "url_detalle": "https://www.poderjudicial.es/search/cedula.jsp?id=28079130012022000456",
    },
]

# -----------------------------------------------------------------------------
# Utilidades de normalización / scoring
# -----------------------------------------------------------------------------
def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm(s: str) -> str:
    return _strip_accents(s or "").lower().strip()


def _tokenize(s: str) -> List[str]:
    s = _norm(s)
    # separadores simples
    for ch in [",", ";", ":", ".", "(", ")", "[", "]", "{", "}", "’", "'", "“", "”", '"', "/"]:
        s = s.replace(ch, " ")
    return [t for t in s.split() if t]


# Mapa de sinónimos básicos de nuestras pruebas
SYNONYMS = {
    "volumen disconforme": ["edificacion disconforme", "alteracion de volumen", "aumento de edificabilidad", "disconformidad con planeamiento"],
    "fuera de ordenacion": ["situacion de fuera de ordenacion", "edificacion disconforme", "ordenacion urbanistica"],
    "suelo no urbanizable": ["suelo rustico", "suelos protegidos"],
    "garaje ilegal": ["construccion ilegal", "obra sin licencia"],
}

# Órganos soportados (formas normalizadas para filtrar)
ORGANO_MAP = {
    "ts": "tribunal supremo (ts)",
    "tribunal supremo": "tribunal supremo (ts)",
    "tsjc": "tribunal superior de justicia de cataluna (tsjc)",
    "tribunal superior de justicia de cataluña": "tribunal superior de justicia de cataluna (tsjc)",
    "tribunal superior de justicia de cataluna": "tribunal superior de justicia de cataluna (tsjc)",
    "audiencia nacional": "audiencia nacional",
    "an": "audiencia nacional",
}


def _expand_with_synonyms(query: str) -> Tuple[str, bool]:
    """
    Si la query contiene una clave con sinónimos, añadimos los sinónimos para ampliar coincidencia.
    Devuelve (query_expandida, se_uso_sinonimo)
    """
    qn = _norm(query)
    used = False
    for key, syns in SYNONYMS.items():
        if key in qn:
            # añadimos términos sinónimos para mejorar recall
            query = f"{query} " + " ".join(syns)
            used = True
    return query, used


def _score_match(query_tokens: List[str], text: str) -> float:
    """
    Score muy sencillo por solapamiento de tokens en título y metadatos.
    """
    if not query_tokens:
        return 0.0
    text_tokens = set(_tokenize(text))
    if not text_tokens:
        return 0.0
    hits = sum(1 for t in query_tokens if t in text_tokens)
    return hits / max(len(query_tokens), 1)


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _validate_and_fix_range(desde: Optional[str], hasta: Optional[str]) -> Tuple[Optional[date], Optional[date], Optional[str]]:
    d1 = _parse_date(desde)
    d2 = _parse_date(hasta)
    if d1 and d2 and d1 > d2:
        # Corrige rango invertido (mejora 4)
        return d2, d1, "Se corrigió el rango de fechas (invertido)."
    return d1, d2, None


def _normalize_organo(organo: Optional[str]) -> Optional[str]:
    if not organo:
        return None
    o = _norm(organo)
    return ORGANO_MAP.get(o, o)


# -----------------------------------------------------------------------------
# Schemas de respuesta
# -----------------------------------------------------------------------------
class Resultado(BaseModel):
    id_cendoj: Optional[str] = None
    titulo: str
    organo: str
    sala: Optional[str] = None
    ponente: Optional[str] = None
    fecha: str
    relevancia: Optional[float] = Field(None, description="0..1")
    url: str
    url_detalle: Optional[str] = None


class BuscarResponse(BaseModel):
    query: str
    total: int
    resultados: List[Resultado]
    nota: Optional[str] = None


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "service": "cendoj-action-mock", "time": datetime.utcnow().isoformat()}


@app.get("/buscar-cendoj", response_model=BuscarResponse)
def buscar_cendoj(
    query: str = Query(..., min_length=2, description="Términos de búsqueda"),
    organo: Optional[str] = Query(None, description="Órgano: TS, TSJC, Audiencia Nacional…"),
    desde: Optional[str] = Query(None, description="Fecha inicial (YYYY-MM-DD)"),
    hasta: Optional[str] = Query(None, description="Fecha final (YYYY-MM-DD)"),
    orden: Optional[str] = Query("fecha_desc", regex="^(fecha_desc|fecha_asc|relevancia_desc|relevancia_asc)$",
                                 description="Criterio de ordenación"),
    limite: Optional[int] = Query(5, ge=1, le=50, description="Nº máximo de resultados"),
):
    """
    Buscador mock con:
    - Normalización (acentos, mayúsculas)
    - Sinónimos (amplía coincidencia)
    - Scoring por tokens (relevancia)
    - Filtros por órgano y fechas
    - Notas explicativas cuando:
        * se usan sinónimos,
        * rango invertido fue corregido,
        * coincidencia baja (aproximados),
        * no hay resultados.
    """
    notes: List[str] = []

    # 1) Normalización y sinónimos
    expanded_query, used_syns = _expand_with_synonyms(query)
    if used_syns:
        notes.append("Se usaron sinónimos comunes para ampliar la coincidencia.")
    q_tokens = _tokenize(expanded_query)

    # 2) Fechas
    d1, d2, fixed = _validate_and_fix_range(desde, hasta)
    if fixed:
        notes.append(fixed)

    # 3) Órgano normalizado
    organo_norm = _normalize_organo(organo)

    # 4) Filtrado + scoring
    results: List[dict] = []
    for r in MOCK_DATA:
        # filtro órgano (si se indica)
        if organo_norm:
            if _norm(r["organo"]) != organo_norm:
                continue

        # filtro fechas
        rdate = _parse_date(r["fecha"])
        if d1 and rdate and rdate < d1:
            continue
        if d2 and rdate and rdate > d2:
            continue

        # scoring simple sobre título + metadatos clave
        text = f"{r['titulo']} {r['organo']} {r.get('sala') or ''} {r.get('ponente') or ''}"
        score = _score_match(q_tokens, text)

        # guardamos una 'relevancia_calc' para ordenar si procede
        r_copy = dict(r)
        r_copy["_relevancia_calc"] = score
        results.append(r_copy)

    # 5) Aproximación: si hay resultados pero el score medio es bajo
    approx = False
    if results:
        avg = sum(x["_relevancia_calc"] for x in results) / max(len(results), 1)
        if avg < 0.25:
            approx = True
            notes.append("Resultados aproximados (coincidencia baja con el texto de búsqueda).")

    # 6) Ordenación
    if orden == "fecha_desc":
        results.sort(key=lambda x: x["fecha"], reverse=True)
    elif orden == "fecha_asc":
        results.sort(key=lambda x: x["fecha"])
    elif orden == "relevancia_desc":
        # prioriza relevancia calculada y, como desempate, relevancia mock y fecha
        results.sort(key=lambda x: (x["_relevancia_calc"], x.get("relevancia", 0.0), x["fecha"]), reverse=True)
    elif orden == "relevancia_asc":
        results.sort(key=lambda x: (x["_relevancia_calc"], x.get("relevancia", 0.0), x["fecha"]))
    else:
        # fallback seguro
        results.sort(key=lambda x: x["fecha"], reverse=True)

    # 7) Límite
    results = results[:limite or 5]

    # 8) Construcción de respuesta
    payload = [
        Resultado(
            id_cendoj=r.get("id_cendoj"),
            titulo=r["titulo"],
            organo=r["organo"],
            sala=r.get("sala"),
            ponente=r.get("ponente"),
            fecha=r["fecha"],
            relevancia=r.get("relevancia"),
            url=r["url"],
            url_detalle=r.get("url_detalle"),
        ).dict()
        for r in results
    ]

    # 9) Nota final
    note_text = None
    if notes:
        # unimos notas de manera compacta
        note_text = " ".join(sorted(set(notes)))
    elif not results:
        note_text = None  # respuesta vacía pero sin incidencias

    return BuscarResponse(
        query=query,
        total=len(payload),
        resultados=payload,
        nota=note_text,
    )