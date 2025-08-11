import unicodedata
from typing import List, Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel

def norm(s: str) -> str:
    return ''.join(
        c for c in unicodedata.normalize('NFD', s.lower())
        if unicodedata.category(c) != 'Mn'
    )

# ====== MODELOS ======
class Resultado(BaseModel):
    id_cendoj: str
    titulo: str
    organo: str
    sala: Optional[str] = None
    ponente: Optional[str] = None
    fecha: str  # YYYY-MM-DD
    url: str
    url_detalle: str
    relevancia: float  # 0.0 - 1.0

class BusquedaResponse(BaseModel):
    query: str
    total: int
    resultados: List[Resultado]
    nota: Optional[str] = None  # explica si se relajó la búsqueda

# ====== APP ======
app = FastAPI(title="CENDOJ Action Mock", version="1.3.0")

# Datos simulados enriquecidos (IDs y detalles ficticios)
BASE = [
    {
        "id_cendoj": "08019320012023000123",
        "titulo": "Sentencia ejemplo sobre 'volumen disconforme'",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "ponente": "Ponente A",
        "fecha": "2023-05-10",
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
        "url": "https://www.poderjudicial.es/search/indexAN.jsp",
        "url_detalle": "https://www.poderjudicial.es/search/cedula.jsp?id=28079130012022000456",
    },
    {
        "id_cendoj": "08019320012024000077",
        "titulo": "Licencia urbanística en suelo no urbanizable: criterios recientes",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "ponente": "Ponente C",
        "fecha": "2024-02-12",
        "url": "https://www.poderjudicial.es/search/indexAN.jsp",
        "url_detalle": "https://www.poderjudicial.es/search/cedula.jsp?id=08019320012024000077",
    },
]

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get(
    "/buscar-cendoj",
    response_model=BusquedaResponse,
    summary="Buscar resoluciones (mock con tolerancia + metadatos CENDOJ)"
)
def buscar_cendoj(
    query: str = Query(..., min_length=2),
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    organo: Optional[str] = None,
    materia: Optional[str] = None,
    limite: int = Query(5, ge=1, le=50),
):
    # Normaliza y tokeniza la query
    q_norm = norm(query).replace("’", "'").replace('"', " ")
    q_tokens = [t for t in q_norm.split() if len(t) > 1]
    denom = max(len(q_tokens), 1)

    def pasa_filtros(r):
        if fecha_desde and r["fecha"] < fecha_desde: return False
        if fecha_hasta and r["fecha"] > fecha_hasta: return False
        if organo and norm(organo) not in norm(r["organo"]): return False
        if materia and norm(materia) not in norm(r["titulo"]): return False
        return True

    # Scoring por tokens: relevancia en [0,1]
    scored = []
    for r in BASE:
        if not pasa_filtros(r):
            continue
        text = norm(r["titulo"] + " " + r["organo"] + " " + (r.get("sala") or ""))
        hits = sum(1 for t in q_tokens if t in text)
        if hits > 0:
            rel = round(hits / denom, 3)
            scored.append((rel, r))

    # Orden: relevancia desc, fecha desc
    scored.sort(key=lambda x: (x[0], x[1]["fecha"]), reverse=True)
    resultados = []
    for rel, r in scored[:limite]:
        resultados.append(Resultado(
            id_cendoj=r["id_cendoj"],
            titulo=r["titulo"],
            organo=r["organo"],
            sala=r.get("sala"),
            ponente=r.get("ponente"),
            fecha=r["fecha"],
            url=r["url"],
            url_detalle=r["url_detalle"],
            relevancia=rel
        ))

    nota = None
    # Fallback: si 0 resultados, relajamos filtros (ignoramos organo/materia) y aplicamos OR simple
    if not resultados:
        nota = "Sin coincidencias exactas; se muestran resultados aproximados."
        approx = []
        for r in BASE:
            if fecha_desde and r["fecha"] < fecha_desde: continue
            if fecha_hasta and r["fecha"] > fecha_hasta: continue
            text = norm(r["titulo"] + " " + r["organo"] + " " + (r.get("sala") or ""))
            hits = sum(1 for t in q_tokens if t in text)
            if hits > 0:
                rel = round(hits / denom, 3)
                approx.append((rel, r))
        approx.sort(key=lambda x: (x[0], x[1]["fecha"]), reverse=True)
        for rel, r in approx[:limite]:
            resultados.append(Resultado(
                id_cendoj=r["id_cendoj"],
                titulo=r["titulo"],
                organo=r["organo"],
                sala=r.get("sala"),
                ponente=r.get("ponente"),
                fecha=r["fecha"],
                url=r["url"],
                url_detalle=r["url_detalle"],
                relevancia=rel
            ))

    return {"query": query, "total": len(resultados), "resultados": resultados, "nota": nota}