from fastapi import FastAPI, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import date, datetime

app = FastAPI(title="CENDOJ Action Mock", version="0.2.0")

# -----------------------------
# Modelos
# -----------------------------
class Resultado(BaseModel):
    id_cendoj: str = Field(..., description="Identificador CENDOJ")
    titulo: str
    organo: str
    sala: Optional[str] = None
    ponente: Optional[str] = None
    fecha: date
    relevancia: Optional[float] = Field(default=None, description="0..1 (solo mock)")
    url: str = Field(..., description="URL general (listado)")
    url_detalle: Optional[str] = Field(default=None, description="URL directa a la cédula/detalle")

class RespuestaBusqueda(BaseModel):
    query: str
    total: int
    resultados: List[Resultado]
    nota: Optional[str] = None


# -----------------------------
# Datos MOCK (puedes ampliar/ajustar)
# -----------------------------
MOCK_DATA: List[Resultado] = [
    Resultado(
        id_cendoj="08019320012023000123",
        titulo="Sentencia ejemplo sobre 'volumen disconforme'",
        organo="Tribunal Superior de Justicia de Cataluña (TSJC)",
        sala="Sala de lo Contencioso-Administrativo",
        ponente="Ponente A",
        fecha=date(2023, 5, 10),
        relevancia=0.6,
        url="https://www.poderjudicial.es/search/indexAN.jsp",
        url_detalle="https://www.poderjudicial.es/search/cedula.jsp?id=08019320012023000123",
    ),
    Resultado(
        id_cendoj="28079130012022000456",
        titulo="Sentencia ejemplo sobre 'fuera de ordenación' en suelo urbano",
        organo="Tribunal Supremo (TS)",
        sala="Sala Tercera (Cont.-Adm.)",
        ponente="Ponente B",
        fecha=date(2022, 11, 3),
        relevancia=0.4,
        url="https://www.poderjudicial.es/search/indexAN.jsp",
        url_detalle="https://www.poderjudicial.es/search/cedula.jsp?id=28079130012022000456",
    ),
    Resultado(
        id_cendoj="08019320012024000077",
        titulo="Licencia urbanística en suelo no urbanizable: criterios recientes",
        organo="Tribunal Superior de Justicia de Cataluña (TSJC)",
        sala="Sala de lo Contencioso-Administrativo",
        ponente="Ponente C",
        fecha=date(2024, 2, 12),
        relevancia=0.2,
        url="https://www.poderjudicial.es/search/indexAN.jsp",
        url_detalle="https://www.poderjudicial.es/search/cedula.jsp?id=08019320012024000077",
    ),
]


# -----------------------------
# Utilidades
# -----------------------------
def _score_query(title: str, q: str) -> float:
    """
    Score muy simple para el mock:
    - Palabras del query que aparezcan en el título suman puntos.
    """
    words = [w for w in q.lower().split() if len(w) > 2]
    title_l = title.lower()
    if not words:
        return 0.0
    hits = sum(1 for w in words if w in title_l)
    return round(hits / len(words), 3)


def _apply_filters(
    data: List[Resultado],
    organo: Optional[str],
    desde: Optional[date],
    hasta: Optional[date],
) -> List[Resultado]:
    out = data
    if organo:
        organo_l = organo.lower()
        out = [r for r in out if organo_l in r.organo.lower()]
    if desde:
        out = [r for r in out if r.fecha >= desde]
    if hasta:
        out = [r for r in out if r.fecha <= hasta]
    return out


def _apply_sort(
    data: List[Resultado],
    orden: Literal["fecha_desc", "fecha_asc", "relevancia_desc", "relevancia_asc"],
) -> List[Resultado]:
    if orden == "fecha_desc":
        return sorted(data, key=lambda r: r.fecha, reverse=True)
    if orden == "fecha_asc":
        return sorted(data, key=lambda r: r.fecha)
    if orden == "relevancia_desc":
        return sorted(data, key=lambda r: (r.relevancia or 0.0), reverse=True)
    if orden == "relevancia_asc":
        return sorted(data, key=lambda r: (r.relevancia or 0.0))
    return data


# -----------------------------
# Endpoints
# -----------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get(
    "/buscar-cendoj",
    response_model=RespuestaBusqueda,
    summary="Buscar resoluciones (mock)",
)
def buscar_cendoj(
    query: str = Query(..., min_length=2, description="Texto de búsqueda libre"),
    organo: Optional[str] = Query(
        None,
        description="Filtra por órgano (p. ej., 'TSJC', 'Tribunal Supremo')",
    ),
    desde: Optional[date] = Query(
        None, description="Fecha mínima (YYYY-MM-DD)"
    ),
    hasta: Optional[date] = Query(
        None, description="Fecha máxima (YYYY-MM-DD)"
    ),
    orden: Literal["fecha_desc", "fecha_asc", "relevancia_desc", "relevancia_asc"] = Query(
        "fecha_desc",
        description="Criterio de ordenación",
    ),
    limit: int = Query(10, ge=1, le=50, description="Límite de resultados"),
    offset: int = Query(0, ge=0, description="Desplazamiento para paginación"),
):
    """
    Mock de búsqueda con:
    - Filtro por órgano y rango de fechas
    - Ordenación por fecha o relevancia
    - Paginación con limit/offset

    Nota: Este endpoint devuelve datos simulados a modo de ejemplo.
    """

    # 1) Partimos del mock y filtramos por órgano/fechas
    filtrados = _apply_filters(MOCK_DATA, organo=organo, desde=desde, hasta=hasta)

    # 2) Scoring de relevancia en función del query (sobre-escribe relevancia mock)
    #    Solo para demostrar el orden por relevancia; si no quieres sobreescribir, comenta esta parte.
    scored: List[Resultado] = []
    for r in filtrados:
        sc = _score_query(r.titulo, query)
        # mezclamos un poco con la relevancia preexistente si la hay
        base = r.relevancia or 0.0
        combined = round(min(1.0, 0.7 * sc + 0.3 * base), 3)
        scored.append(Resultado(**{**r.model_dump(), "relevancia": combined}))
    filtrados = scored

    # 3) Orden
    ordenados = _apply_sort(filtrados, orden=orden)

    # 4) Paginación
    total = len(ordenados)
    paginados = ordenados[offset : offset + limit]

    # 5) Nota informativa si el matching es flojo (mock)
    #    Si ningún item supera 0.2 de relevancia, avisamos que son aproximados.
    nota = None
    if paginados and all((itm.relevancia or 0) <= 0.2 for itm in paginados):
        nota = "Resultados aproximados (coincidencia baja con el texto de búsqueda)."

    return RespuestaBusqueda(
        query=query,
        total=total,
        resultados=paginados,
        nota=nota,
    )