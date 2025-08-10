from typing import List, Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel

# --- Definición de modelos para tipado y documentación ---
class Resultado(BaseModel):
    titulo: str
    organo: str
    fecha: str  # Formato ISO YYYY-MM-DD
    url: str

class BusquedaResponse(BaseModel):
    query: str
    total: int
    resultados: List[Resultado]

# --- Crear la aplicación ---
app = FastAPI(title="CENDOJ Action Mock", version="1.1.0")

@app.get("/health")
def health():
    """Endpoint de comprobación de estado."""
    return {"status": "ok"}

@app.get(
    "/buscar-cendoj",
    response_model=BusquedaResponse,
    summary="Buscar resoluciones en CENDOJ (mock)"
)
def buscar_cendoj(
    query: str = Query(..., min_length=2, description="Texto de búsqueda"),
    fecha_desde: Optional[str] = Query(None, description="Fecha mínima (YYYY-MM-DD)"),
    fecha_hasta: Optional[str] = Query(None, description="Fecha máxima (YYYY-MM-DD)"),
    organo: Optional[str] = Query(None, description="Órgano judicial (TS, TSJ, AN…)"),
    materia: Optional[str] = Query(None, description="Materia o temática"),
    limite: int = Query(5, ge=1, le=50, description="Número máximo de resultados"),
):
    """
    Endpoint mock que devuelve resultados ficticios con filtros opcionales.
    En el futuro se conectará con el buscador real de CENDOJ.
    """
    # Datos simulados
    base = [
        {
            "titulo": "Sentencia ejemplo sobre 'volumen disconforme'",
            "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
            "fecha": "2023-05-10",
            "url": "https://www.poderjudicial.es/search/indexAN.jsp",
        },
        {
            "titulo": "Sentencia ejemplo sobre 'fuera de ordenación' en suelo urbano",
            "organo": "Tribunal Supremo (TS)",
            "fecha": "2022-11-03",
            "url": "https://www.poderjudicial.es/search/indexAN.jsp",
        },
        {
            "titulo": "Sentencia sobre urbanismo y alineaciones",
            "organo": "Audiencia Nacional (AN)",
            "fecha": "2021-06-15",
            "url": "https://www.poderjudicial.es/search/indexAN.jsp",
        },
    ]

    # Filtrado simple
    resultados = []
    for r in base:
        if query.lower() not in (r["titulo"] + " " + r["organo"]).lower():
            continue
        if fecha_desde and r["fecha"] < fecha_desde:
            continue
        if fecha_hasta and r["fecha"] > fecha_hasta:
            continue
        if organo and organo.lower() not in r["organo"].lower():
            continue
        if materia and materia.lower() not in r["titulo"].lower():
            continue
        resultados.append(r)

    resultados = resultados[:limite]
    return {"query": query, "total": len(resultados), "resultados": resultados}

