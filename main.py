from datetime import date, datetime
from typing import List, Optional, Literal

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator

# =============================================================================
# CENDOJ Action Mock - v1.3 (mejoras 1..5 unificadas)
# =============================================================================

app = FastAPI(
    title="CENDOJ Action Mock",
    version="1.3.0",
    description=(
        "Mock de búsqueda de jurisprudencia CENDOJ con filtros habituales.\n\n"
        "Parámetros soportados: query, organo, desde, hasta, orden, limite.\n"
        "Orden: fecha_desc (defecto), fecha_asc, relevancia_desc, relevancia_asc.\n"
        "Órganos: TS (Tribunal Supremo), TSJC (TS de Cataluña), AN (Audiencia Nacional), "
        "TSJ (genérico) o cadenas tipo 'TSJ de Andalucía' (se normalizan a TSJ).\n"
    ),
)

# CORS amplio para facilitar llamadas desde el editor del GPT
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------- Modelos de respuesta -----------------------------

class Resultado(BaseModel):
    id_cendoj: Optional[str] = Field(None, description="Identificador CENDOJ si está disponible")
    titulo: str
    organo: str
    sala: Optional[str] = None
    ponente: Optional[str] = None
    fecha: date
    relevancia: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Score de 0..1 (para ordenar por relevancia)"
    )
    url: str
    url_detalle: Optional[str] = None

class RespuestaBusqueda(BaseModel):
    query: str
    total: int
    resultados: List[Resultado]
    nota: Optional[str] = None

# ------------------------------ Datos simulados -------------------------------

# “Base” mínima para pruebas; puedes ampliar/ajustar libremente.
DATASET: List[Resultado] = [
    Resultado(
        id_cendoj="0801932001202400077",
        titulo="Licencia urbanística en suelo no urbanizable: criterios recientes",
        organo="Tribunal Superior de Justicia de Cataluña (TSJC)",
        sala="Sala de lo Contencioso-Administrativo",
        ponente="Ponente C",
        fecha=date.fromisoformat("2024-02-12"),
        relevancia=0.76,
        url="https://www.poderjudicial.es/search/indexAN.jsp",
        url_detalle="https://www.poderjudicial.es/search/cedula.jsp?id=0801932001202400077",
    ),
    Resultado(
        id_cendoj="08019320012023000123",
        titulo="Sentencia ejemplo sobre 'volumen disconforme'",
        organo="Tribunal Superior de Justicia de Cataluña (TSJC)",
        sala="Sala de lo Contencioso-Administrativo",
        ponente="Ponente A",
        fecha=date.fromisoformat("2023-05-10"),
        relevancia=0.53,
        url="https://www.poderjudicial.es/search/indexAN.jsp",
        url_detalle="https://www.poderjudicial.es/search/cedula.jsp?id=0801932001203000123",
    ),
    Resultado(
        id_cendoj="28079130012022000456",
        titulo="Sentencia ejemplo sobre 'fuera de ordenación' en suelo urbano",
        organo="Tribunal Supremo (TS)",
        sala="Sala Tercera (Cont.-Adm.)",
        ponente="Ponente B",
        fecha=date.fromisoformat("2022-11-03"),
        relevancia=0.82,
        url="https://www.poderjudicial.es/search/indexAN.jsp",
        url_detalle="https://www.poderjudicial.es/search/cedula.jsp?id=28079130012022000456",
    ),
]

# ---------------------------- Utilidades/Filtros ------------------------------

# Normalización sencilla de órganos (puedes extenderla fácilmente)
ORG_MAP = {
    "TS": "Tribunal Supremo (TS)",
    "TRIBUNAL SUPREMO": "Tribunal Supremo (TS)",
    "AN": "Audiencia Nacional (AN)",
    "AUDIENCIA NACIONAL": "Audiencia Nacional (AN)",
    "TSJC": "Tribunal Superior de Justicia de Cataluña (TSJC)",
    "TRIBUNAL SUPERIOR DE JUSTICIA DE CATALUÑA": "Tribunal Superior de Justicia de Cataluña (TSJC)",
}
# Cualquier “TSJ de …” lo normalizamos a “TSJ (varios)”
def normaliza_organo(user_value: Optional[str]) -> Optional[str]:
    if not user_value:
        return None
    v = user_value.strip().upper()
    if v in ORG_MAP:
        return ORG_MAP[v]
    if v.startswith("TSJ " ) or v.startswith("TSJ DE") or v.startswith("TRIBUNAL SUPERIOR DE JUSTICIA"):
        return "TSJ (varios)"
    return user_value  # lo dejamos tal cual si no reconocemos, para no perder información

def parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Formato de fecha inválido: {s} (usa YYYY-MM-DD)")

def tokens(text: str) -> List[str]:
    return [t for t in text.lower().replace("’", "'").replace("“","\"").replace("”","\"").split() if t]

def match_approx(titulo: str, query: str) -> float:
    """Coincidencia muy simple por tokens: proporción de tokens del query presentes en el título."""
    q = tokens(query)
    if not q:
        return 1.0
    ttl = tokens(titulo)
    hits = sum(1 for t in q if t in ttl)
    return hits / len(q)

# ------------------------------ Endpoints -------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get(
    "/buscar-cendoj",
    response_model=RespuestaBusqueda,
    summary="Buscar resoluciones (mock)",
    description=(
        "Devuelve resultados ficticios con estructura similar a CENDOJ.\n\n"
        "Ejemplos:\n"
        "• /buscar-cendoj?query=fuera%20de%20ordenaci%C3%B3n&organo=TSJC&orden=fecha_desc\n"
        "• /buscar-cendoj?query=urbanizable&desde=2024-01-01&orden=relevancia_desc&limite=5\n"
        "• /buscar-cendoj?query=garaje%20ilegal&orden=relevancia_desc\n"
    ),
)
def buscar_cendoj(
    query: str = Query(..., min_length=2, description="Términos de búsqueda"),
    organo: Optional[str] = Query(None, description="Órgano (TS, TSJC, AN, TSJ de …)"),
    desde: Optional[str] = Query(None, description="Fecha inicial (YYYY-MM-DD)"),
    hasta: Optional[str] = Query(None, description="Fecha final (YYYY-MM-DD)"),
    orden: Literal["fecha_desc", "fecha_asc", "relevancia_desc", "relevancia_asc"] = Query(
        "fecha_desc", description="Criterio de ordenación"
    ),
    limite: int = Query(5, ge=1, le=20, description="Número máximo de resultados (1..20)"),
):
    # Normalizar/validar filtros
    f_desde = parse_date(desde)
    f_hasta = parse_date(hasta)
    if f_desde and f_hasta and f_desde > f_hasta:
        raise HTTPException(status_code=400, detail="El rango de fechas es inválido: 'desde' > 'hasta'.")

    org_norm = normaliza_organo(organo)

    # Filtrado por órgano y fechas
    candidatos = []
    for r in DATASET:
        if org_norm:
            if org_norm == "TSJ (varios)":
                if "TSJ" not in r.organo and "Tribunal Superior de Justicia" not in r.organo:
                    continue
            else:
                if r.organo != org_norm:
                    continue
        if f_desde and r.fecha < f_desde:
            continue
        if f_hasta and r.fecha > f_hasta:
            continue
        candidatos.append(r)

    # Coincidencia por query (muy simple, suficiente para mock con 'nota')
    nota: Optional[str] = None
    filtrados: List[Resultado] = []
    for r in candidatos:
        score = match_approx(r.titulo, query)
        # Aceptamos si hay al menos 1 token coincidente; marcamos nota si baja
        if score > 0:
            if score < 0.51:
                nota = "Resultados aproximados (coincidencia baja con el texto de búsqueda)."
            filtrados.append(r)

    # Ordenación
    if orden == "fecha_desc":
        filtrados.sort(key=lambda x: x.fecha, reverse=True)
    elif orden == "fecha_asc":
        filtrados.sort(key=lambda x: x.fecha)
    elif orden == "relevancia_desc":
        filtrados.sort(key=lambda x: (x.relevancia or 0.0), reverse=True)
    elif orden == "relevancia_asc":
        filtrados.sort(key=lambda x: (x.relevancia or 0.0))

    # Limitar y construir respuesta (copiamos objetos para no mutar DATASET)
    out: List[Resultado] = []
    for r in filtrados[:limite]:
        out.append(Resultado(**r.dict()))

    return RespuestaBusqueda(
        query=query,
        total=len(out),
        resultados=out,
        nota=nota,
    )