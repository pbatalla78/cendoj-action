from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional, Tuple

import unicodedata
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator

# -----------------------------------------------------------------------------
# Utilidades
# -----------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Normaliza texto: minúsculas, sin acentos, espacios compactados."""
    if text is None:
        return ""
    text = (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
        .strip()
    )
    return " ".join(text.split())


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Fecha inválida: '{s}'. Usa YYYY-MM-DD.")


# Mapeo de órganos aceptados (normalización ligera)
ORG_ALIASES = {
    "ts": "Tribunal Supremo (TS)",
    "tribunal supremo": "Tribunal Supremo (TS)",
    "tsjc": "Tribunal Superior de Justicia de Cataluña (TSJC)",
    "tribunal superior de justicia de cataluna": "Tribunal Superior de Justicia de Cataluña (TSJC)",
    "tribunal superior de justicia de cataluña": "Tribunal Superior de Justicia de Cataluña (TSJC)",
    "an": "Audiencia Nacional (AN)",
    "audiencia nacional": "Audiencia Nacional (AN)",
}

def _normaliza_organo(valor: Optional[str]) -> Optional[str]:
    if not valor:
        return None
    key = _norm(valor)
    return ORG_ALIASES.get(key, valor)


# Sinónimos básicos para mejorar “recall”
SYNONYMS = {
    "fuera de ordenacion": ["situacion de fuera de ordenacion", "edificacion disconforme", "ordenacion urbanistica"],
    "volumen disconforme": ["alteracion de volumen", "aumento de edificabilidad", "disconformidad con planeamiento"],
    "suelo no urbanizable": ["suelo rustico", "suelos protegidos", "autorizacion excepcional"],
    "garaje": ["aparcamiento"],
}

def _expand_terms(query: str) -> List[str]:
    terms = set()
    base = _norm(query)
    if not base:
        return []
    # palabras “clave” de la query (dividir por espacios)
    tokens = [t for t in base.split() if len(t) > 1]
    terms.update(tokens)

    # intentamos añadir sinónimos por frase completa
    if base in SYNONYMS:
        for s in SYNONYMS[base]:
            terms.update(_norm(s).split())

    # y también por token
    for token in tokens:
        if token in SYNONYMS:
            for s in SYNONYMS[token]:
                terms.update(_norm(s).split())

    return list(terms)


# -----------------------------------------------------------------------------
# Datos MOCK (puedes sustituir por tu integrador real cuando esté listo)
# -----------------------------------------------------------------------------
MOCK_DATA = [
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
        "materia": "urbanismo",
        "texto": "volumen disconforme edificacion disconforme planeamiento cataluna",
    },
    {
        "id_cendoj": "28079130012022000456",
        "titulo": "Sentencia ejemplo sobre 'fuera de ordenación' en suelo urbano",
        "organo": "Tribunal Supremo (TS)",
        "sala": "Sala Tercera (Cont.-Adm.)",
        "ponente": "Ponente B",
        "fecha": "2022-11-03",
        "relevancia": 0.12,
        "url": "https://www.poderjudicial.es/search/indexAN.jsp",
        "url_detalle": "https://www.poderjudicial.es/search/cedula.jsp?id=28079130012022000456",
        "materia": "urbanismo",
        "texto": "fuera de ordenacion suelo urbano licencia fuera de ordenacion",
    },
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
        "materia": "urbanismo",
        "texto": "suelo no urbanizable licencia urbanistica autorizacion excepcional",
    },
]

def _result_score(item: dict, terms: List[str]) -> float:
    """Puntuación sencilla por presencia de términos en título + texto."""
    hay = 0
    texto_busqueda = _norm(item.get("titulo", "") + " " + item.get("texto", ""))
    for t in terms:
        if t in texto_busqueda:
            hay += 1
    return hay / max(1, len(terms))


# -----------------------------------------------------------------------------
# Modelos de respuesta (estables para el agente)
# -----------------------------------------------------------------------------

class CendojItem(BaseModel):
    id_cendoj: Optional[str] = Field(None, example="08019320012023000123")
    titulo: str
    organo: str
    sala: Optional[str] = None
    ponente: Optional[str] = None
    fecha: str = Field(..., regex=r"^\d{4}-\d{2}-\d{2}$")
    relevancia: Optional[float] = Field(None, ge=0, le=1, description="0.0–1.0 (se mostrará como % en el agente)")
    url: str
    url_detalle: Optional[str] = None

    @validator("fecha")
    def _val_fecha(cls, v: str) -> str:
        # asegura formato correcto
        _ = datetime.strptime(v, "%Y-%m-%d")
        return v


class CendojResponse(BaseModel):
    query: str
    total: int
    resultados: List[CendojItem]
    nota: Optional[str] = None


# -----------------------------------------------------------------------------
# FastAPI
# -----------------------------------------------------------------------------

app = FastAPI(
    title="CENDOJ Action Mock",
    version="1.3.0",
    description="API mock para búsquedas en CENDOJ con filtros, sinónimos, relevancia y campos enriquecidos.",
)

# CORS para que el editor del agente pueda llamar sin problemas
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # si quieres, restrínge a tu dominio/origen
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """Comprobación de estado."""
    return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}


@app.get("/buscar-cendoj", response_model=CendojResponse)
def buscar_cendoj(
    query: str = Query(..., min_length=2, description="Términos de búsqueda"),
    organo: Optional[str] = Query(None, description="Órgano judicial (TS, TSJC, AN, TSJ...)"),
    desde: Optional[str] = Query(None, description="Fecha inicial YYYY-MM-DD"),
    hasta: Optional[str] = Query(None, description="Fecha final YYYY-MM-DD"),
    materia: Optional[str] = Query(None, description="Materia (solo si procede)"),
    orden: str = Query("fecha_desc", regex="^(fecha_desc|fecha_asc|relevancia_desc|relevancia_asc)$"),
    limite: int = Query(10, ge=1, le=50, description="Límite de resultados"),
    expandir: bool = Query(True, description="Expandir sinónimos para mejorar el recall"),
):
    """
    Búsqueda mock con:
      - normalización de texto
      - expansión de sinónimos (opcional)
      - filtros por órgano, fechas y materia
      - orden flexible
      - relevancia + nota de aproximación si la coincidencia es baja
    """
    # Normalización y parsing de parámetros
    org = _normaliza_organo(organo)
    d1 = _parse_date(desde)
    d2 = _parse_date(hasta)
    if d1 and d2 and d2 < d1:
        raise HTTPException(status_code=400, detail="El rango de fechas es inválido: 'hasta' < 'desde'.")

    # Construcción de términos de búsqueda
    terms = _expand_terms(query) if expandir else _norm(query).split()
    if not terms:
        raise HTTPException(status_code=400, detail="La consulta no contiene términos válidos.")

    # Filtrado + scoring
    candidatos: List[Tuple[dict, float]] = []
    for item in MOCK_DATA:
        # filtro órgano
        if org and _norm(item["organo"]) != _norm(org):
            continue
        # filtro materia (muy básico)
        if materia and _norm(materia) not in _norm(item.get("materia", "")):
            continue
        # filtro fechas
        f_item = datetime.strptime(item["fecha"], "%Y-%m-%d").date()
        if d1 and f_item < d1:
            continue
        if d2 and f_item > d2:
            continue

        score = _result_score(item, terms)
        # mezclamos una pizca de “relevancia” del mock si existe
        score = (score * 0.8) + (item.get("relevancia", 0.0) * 0.2)
        if score > 0:
            # guardamos el score temporalmente (no sale en la respuesta)
            cand = dict(item)
            cand["_score"] = score
            candidatos.append((cand, score))

    # Orden
    if orden == "fecha_desc":
        candidatos.sort(key=lambda t: t[0]["fecha"], reverse=True)
    elif orden == "fecha_asc":
        candidatos.sort(key=lambda t: t[0]["fecha"])
    elif orden == "relevancia_desc":
        candidatos.sort(key=lambda t: (t[1], t[0]["fecha"]), reverse=True)
    else:  # relevancia_asc
        candidatos.sort(key=lambda t: (t[1], t[0]["fecha"]))

    # Nota de aproximación si las coincidencias medias son bajas
    nota = None
    if candidatos:
        media = sum(s for _, s in candidatos) / len(candidatos)
        if media < 0.35:
            nota = "Resultados aproximados (coincidencia baja con el texto de búsqueda)."

    # Recorte y mapeo a schema de salida
    out: List[CendojItem] = []
    for cand, _ in candidatos[:limite]:
        out.append(
            CendojItem(
                id_cendoj=cand.get("id_cendoj"),
                titulo=cand["titulo"],
                organo=cand["organo"],
                sala=cand.get("sala"),
                ponente=cand.get("ponente"),
                fecha=cand["fecha"],
                relevancia=cand.get("relevancia"),
                url=cand["url"],
                url_detalle=cand.get("url_detalle"),
            )
        )

    return CendojResponse(query=query, total=len(out), resultados=out, nota=nota)


# Opcional: página raíz “amable” (no afecta al agente)
@app.get("/")
def root():
    return {
        "name": "CENDOJ Action Mock",
        "version": "1.3.0",
        "endpoints": {
            "health": "/health",
            "buscar": "/buscar-cendoj",
            "docs": "/docs",
            "openapi": "/openapi.json",
        },
    }