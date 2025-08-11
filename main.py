import unicodedata
from typing import List, Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel

def norm(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s.lower()) if unicodedata.category(c) != 'Mn')

class Resultado(BaseModel):
    titulo: str
    organo: str
    fecha: str  # YYYY-MM-DD
    url: str

class BusquedaResponse(BaseModel):
    query: str
    total: int
    resultados: List[Resultado]
    nota: Optional[str] = None  # explica si se relajó la búsqueda

app = FastAPI(title="CENDOJ Action Mock", version="1.2.0")

BASE = [
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
        "titulo": "Licencia urbanística en suelo no urbanizable: criterios recientes",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "fecha": "2024-02-12",
        "url": "https://www.poderjudicial.es/search/indexAN.jsp",
    },
]

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/buscar-cendoj", response_model=BusquedaResponse, summary="Buscar resoluciones (mock con tolerancia)")
def buscar_cendoj(
    query: str = Query(..., min_length=2),
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    organo: Optional[str] = None,
    materia: Optional[str] = None,
    limite: int = Query(5, ge=1, le=50),
):
    q_norm = norm(query)
    q_tokens = [t for t in q_norm.replace("’", "'").replace('"', ' ').split() if len(t) > 1]

    def pasa_filtros(r):
        if fecha_desde and r["fecha"] < fecha_desde: return False
        if fecha_hasta and r["fecha"] > fecha_hasta: return False
        if organo and norm(organo) not in norm(r["organo"]): return False
        if materia and norm(materia) not in norm(r["titulo"]): return False
        return True

    # scoring por tokens (AND suave)
    scored = []
    for r in BASE:
        if not pasa_filtros(r):
            continue
        text = norm(r["titulo"] + " " + r["organo"])
        hits = sum(1 for t in q_tokens if t in text)
        if hits > 0:
            scored.append((hits, r))
    scored.sort(key=lambda x: (x[0], x[1]["fecha"]), reverse=True)
    resultados = [r for _, r in scored][:limite]

    nota = None
    if not resultados:
        nota = "Sin coincidencias exactas; se muestran resultados aproximados."
        approx = []
        for r in BASE:
            if fecha_desde and r["fecha"] < fecha_desde: continue
            if fecha_hasta and r["fecha"] > fecha_hasta: continue
            text = norm(r["titulo"] + " " + r["organo"])
            if any(t in text for t in q_tokens):
                approx.append(r)
        resultados = approx[:limite]

    return {"query": query, "total": len(resultados), "resultados": resultados, "nota": nota}
