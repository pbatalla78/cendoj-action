import os
import re
import math
import datetime
from typing import List, Optional, Literal, Dict, Any
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
import httpx

app = FastAPI(
    title="CENDOJ Action",
    version="1.6",
    description="Buscador auxiliar con normalización, sinónimos, ranking híbrido, resúmenes y validación de enlaces."
)

# ---------------------------
# Utilidades básicas
# ---------------------------

DATE_FMT = "%Y-%m-%d"

def to_date(s: Optional[str]) -> Optional[datetime.date]:
    if not s:
        return None
    return datetime.datetime.strptime(s, DATE_FMT).date()

def format_date(d: datetime.date) -> str:
    return d.strftime(DATE_FMT)

def today() -> datetime.date:
    return datetime.date.today()

def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))

def pct_int(v: float) -> int:
    return int(round(100 * clamp(v, 0, 1)))

# ---------------------------
# Normalización de órgano
# ---------------------------

ORG_NORMALIZATIONS = {
    "tribunal supremo": "Tribunal Supremo (TS)",
    "ts": "Tribunal Supremo (TS)",
    "audiencia nacional": "Audiencia Nacional (AN)",
    "an": "Audiencia Nacional (AN)",
    "tsjc": "Tribunal Superior de Justicia de Cataluña (TSJC)",
    "tribunal superior de justicia de cataluña": "Tribunal Superior de Justicia de Cataluña (TSJC)",
}

def normaliza_organo(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    k = re.sub(r"\s+", " ", s.strip().lower())
    return ORG_NORMALIZATIONS.get(k, s)

# ---------------------------
# Sinónimos (expansión simple)
# ---------------------------

SYNONYMS = {
    "fuera de ordenación": ["situación de fuera de ordenación", "edificación disconforme", "ordenación urbanística"],
    "volumen disconforme": ["alteración de volumen", "aumento de edificabilidad", "disconformidad con planeamiento"],
    "suelo no urbanizable": ["suelo rústico", "suelos protegidos", "autorización excepcional"],
}

def expand_query(q: str) -> (str, List[str]):
    usados = []
    exp = [q]
    ql = q.lower()
    for base, exps in SYNONYMS.items():
        if base in ql:
            usados.extend(exps)
            exp.extend(exps)
    return q, usados

# ---------------------------
# Dataset DEMO (mismos IDs que venías probando)
# Nota: En producción, aquí llamarías al buscador real del CENDOJ.
# ---------------------------

DEMO_DOCS = [
    {
        "id_cendoj": "0801932001202400077",
        "titulo": "Licencia urbanística en suelo no urbanizable: criterios recientes",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "ponente": "Ponente C",
        "fecha": "2024-02-12",
        "relevancia": 0.76,
        "roj": "STS 1234/2024",
        "ecli": "ECLI:ES:TS:2024:1234",
    },
    {
        "id_cendoj": "08019320012023000123",
        "titulo": "Sentencia ejemplo sobre 'volumen disconforme'",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "ponente": "Ponente A",
        "fecha": "2023-05-10",
        "relevancia": 0.18,
        "roj": "STS 111/2023",
        "ecli": "ECLI:ES:TS:2023:111",
    },
    {
        "id_cendoj": "28079130012022000456",
        "titulo": "Sentencia ejemplo sobre 'fuera de ordenación' en suelo urbano",
        "organo": "Tribunal Supremo (TS)",
        "sala": "Sala Tercera (Cont.-Adm.)",
        "ponente": "Ponente B",
        "fecha": "2022-11-03",
        "relevancia": 0.82,
        "roj": "STS 456/2022",
        "ecli": "ECLI:ES:TS:2022:456",
    },
]

# ---------------------------
# Modelos de respuesta
# ---------------------------

class Resultado(BaseModel):
    titulo: str
    organo: str
    sala: Optional[str] = None
    ponente: Optional[str] = None
    fecha: str
    relevancia: int  # en %
    resumen: Optional[str] = None
    id_cendoj: Optional[str] = None
    roj: Optional[str] = None
    ecli: Optional[str] = None
    url_directo: Optional[str] = None
    url_estable: Optional[str] = None
    enlace_preferido: Optional[str] = None
    enlace_directo_ok: Optional[bool] = None
    estrategia_enlace: Literal["directo", "estable"] = "directo"

class Respuesta(BaseModel):
    query: str
    total: int
    resultados: List[Resultado]
    nota: Optional[str] = None

# ---------------------------
# Resúmenes consistentes
# ---------------------------

def make_resumen(titulo: str, organo: str, sala: Optional[str], fecha: str) -> str:
    # Plantilla breve y neutra
    base = re.sub(r"\s+", " ", titulo).strip().rstrip(".")
    partes = [base]
    if organo:
        partes.append(f"({organo}{' - ' + sala if sala else ''})")
    partes.append(f"Fecha: {fecha}.")
    return " ".join(partes)

# ---------------------------
# Construcción de enlaces
# ---------------------------

def enlace_directo(id_cendoj: str) -> str:
    return f"https://www.poderjudicial.es/search/cedula.jsp?id={id_cendoj}"

def enlace_estable(roj: Optional[str], ecli: Optional[str]) -> str:
    # Estrategia: búsqueda site: por ECLI preferente; si no, por ROJ
    if ecli:
        q = f"site%3Apoderjudicial.es+%22{ecli}%22"
    elif roj:
        q = f"site%3Apoderjudicial.es+%22{roj}%22"
    else:
        q = "site%3Apoderjudicial.es+CENDOJ"
    return f"https://www.google.com/search?q={q}"

async def valida_directo(url: str, timeout_s: float = 2.0) -> bool:
    # Validación opcional y best-effort (CENDOJ puede cambiar comportamiento)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; CendojAction/1.6)"
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s, headers=headers, follow_redirects=True) as client:
            r = await client.get(url)
            # Consideramos válido si 200 y no contiene un 404 clásico
            if r.status_code == 200 and ("404 Page Error" not in r.text):
                return True
    except Exception:
        pass
    return False

# ---------------------------
# Ranking híbrido
# ---------------------------

def score_hibrido(doc: Dict[str, Any], hoy: datetime.date) -> float:
    # relevancia: 0..1
    rel = float(doc.get("relevancia", 0.0))
    # recency: 0..1 (1=reciente). Decae con media ~ 540 días
    d = to_date(doc["fecha"])
    if not d:
        recency = 0.5
    else:
        days = (hoy - d).days
        tau = 540.0
        recency = math.exp(-max(0, days) / tau)  # 1 hoy, ~0.37 a 1.5 años
    # Ponderación
    return 0.7 * rel + 0.3 * recency

def sort_docs(docs: List[Dict[str, Any]], orden: str, hoy: datetime.date) -> List[Dict[str, Any]]:
    if orden == "relevancia_desc":
        return sorted(docs, key=lambda d: d.get("relevancia", 0.0), reverse=True)
    if orden == "fecha_desc":
        return sorted(docs, key=lambda d: d.get("fecha", ""), reverse=True)
    # default híbrido_desc
    return sorted(docs, key=lambda d: score_hibrido(d, hoy), reverse=True)

# ---------------------------
# Filtro de fechas y órgano
# ---------------------------

def filtra_docs(docs: List[Dict[str, Any]], desde: Optional[str], hasta: Optional[str], organo: Optional[str]) -> (List[Dict[str, Any]], List[str], Optional[str]):
    notas = []
    d1 = to_date(desde) if desde else None
    d2 = to_date(hasta) if hasta else None
    if d1 and d2 and d1 > d2:
        # corregimos rango invertido
        d1, d2 = d2, d1
        notas.append("Se corrigió el rango de fechas (invertido).")
    org = normaliza_organo(organo)
    if organo and org != organo:
        notas.append(f"Órgano normalizado a “{org}”.")
    out = []
    for doc in docs:
        ok = True
        if org and doc["organo"] != org:
            ok = False
        if d1 and to_date(doc["fecha"]) < d1:
            ok = False
        if d2 and to_date(doc["fecha"]) > d2:
            ok = False
        if ok:
            out.append(doc)
    return out, notas, org

# ---------------------------
# Matching muy simple por texto
# ---------------------------

def matches(q: str, doc: Dict[str, Any]) -> bool:
    hay = (doc["titulo"] + " " + doc["organo"] + " " + (doc.get("sala") or "")).lower()
    tokens = re.findall(r"\w+", q.lower())
    return all(t in hay for t in tokens) if tokens else True

def buscar_base(q: str, sinonimos: List[str]) -> (List[Dict[str, Any]], List[str]):
    notas = []
    candidatos = []
    # coincidencia básica
    for d in DEMO_DOCS:
        if matches(q, d):
            candidatos.append(d)
    # si nada, probar con sinónimos
    if not candidatos and sinonimos:
        notas.append("Se usaron sinónimos comunes para ampliar la coincidencia.")
        for d in DEMO_DOCS:
            if any(matches(s, d) for s in sinonimos):
                candidatos.append(d)
    return candidatos, notas

# ---------------------------
# Endpoint health
# ---------------------------

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.6"}

# ---------------------------
# Endpoint principal
# ---------------------------

@app.get("/buscar-cendoj", response_model=Respuesta)
async def buscar_cendoj(
    query: str = Query(..., description="Consulta de texto libre"),
    organo: Optional[str] = Query(None, description="Órgano, ej. TS, TSJC, AN..."),
    desde: Optional[str] = Query(None, description="Fecha inicial (YYYY-MM-DD)"),
    hasta: Optional[str] = Query(None, description="Fecha final (YYYY-MM-DD)"),
    orden: Optional[str] = Query("hibrido_desc", description="hibrido_desc | relevancia_desc | fecha_desc"),
    limite: Optional[int] = Query(5, ge=1, le=50, description="Límite de resultados"),
    validar_enlaces: Optional[bool] = Query(False, description="Validar enlace directo contra CENDOJ"),
):
    # Normalización de query + expansión de sinónimos
    q_limpia = re.sub(r"[“”\"']", "", query).strip()
    q_base, sinonimos_usados = expand_query(q_limpia)

    # Búsqueda base (demo)
    docs, notas_busqueda = buscar_base(q_base, sinonimos_usados)

    # Filtro por fechas/órgano
    try:
        docs, notas_filtros, organo_norm = filtra_docs(docs, desde, hasta, organo)
    except ValueError:
        raise HTTPException(status_code=400, detail="El rango de fechas es inválido: 'desde' y/o 'hasta' no cumplen YYYY-MM-DD.")

    # Ordenación
    docs = sort_docs(docs, orden or "hibrido_desc", today())

    # Límite
    docs = docs[: (limite or 5)]

    resultados: List[Resultado] = []
    notas: List[str] = []
    notas.extend(notas_busqueda)
    notas.extend(notas_filtros)

    for d in docs:
        url_dir = enlace_directo(d["id_cendoj"])
        url_est = enlace_estable(d.get("roj"), d.get("ecli"))
        ok_dir: Optional[bool] = None
        estrategia = "directo"
        preferido = url_dir

        # Validación de enlace directo si se solicita
        if validar_enlaces:
            ok_dir = await valida_directo(url_dir)
            if not ok_dir:
                estrategia = "estable"
                preferido = url_est
                notas.append(f"El enlace directo de {d['id_cendoj']} no respondió como esperado; se ofrece enlace estable por ECLI/ROJ.")

        resumen = make_resumen(
            titulo=d["titulo"],
            organo=d["organo"],
            sala=d.get("sala"),
            fecha=d["fecha"]
        )

        resultados.append(Resultado(
            titulo=d["titulo"],
            organo=d["organo"],
            sala=d.get("sala"),
            ponente=d.get("ponente"),
            fecha=d["fecha"],
            relevancia=pct_int(d.get("relevancia", 0.0)),
            resumen=resumen,
            id_cendoj=d.get("id_cendoj"),
            roj=d.get("roj"),
            ecli=d.get("ecli"),
            url_directo=url_dir,
            url_estable=url_est,
            enlace_preferido=preferido,
            enlace_directo_ok=ok_dir,
            estrategia_enlace=estrategia,  # directo|estable
        ))

    # Notas explicativas más precisas
    if not resultados:
        nota_final = "No se han encontrado resultados exactos. Prueba con sinónimos o ajusta el rango de fechas."
    else:
        extras = []
        if sinonimos_usados:
            extras.append("sinónimos: " + ", ".join(sinonimos_usados))
        if orden == "hibrido_desc":
            extras.append("orden: ranking híbrido (relevancia y actualidad)")
        if extras:
            notas.append("Info: " + "; ".join(extras))
        nota_final = "; ".join(notas) if notas else None

    return Respuesta(
        query=q_limpia,
        total=len(resultados),
        resultados=resultados,
        nota=nota_final
    )