from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from typing import List, Optional, Dict, Any
import unicodedata
import re

app = FastAPI(title="CENDOJ Action Mock", version="1.3.0")

# ---------------------------------------------------------
# CORS (útil si en el futuro llamas desde frontends)
# ---------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# Helpers de normalización y utilidades
# ---------------------------------------------------------
def _strip_accents(s: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", s)
        if unicodedata.category(ch) != "Mn"
    )

def _normalize(s: str) -> str:
    if not s:
        return ""
    s = _strip_accents(s).lower()
    # quita comillas tipográficas y signos que no aportan
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[^\w\s/.-]", " ", s)  # conserva letras, números, espacios, / . -
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Fecha inválida: {s} (usa YYYY-MM-DD)")

def _fmt_date(d: str) -> str:
    # Los datos ya vienen en YYYY-MM-DD; si quisieras formatear/validar extra, hazlo aquí.
    return d

# ---------------------------------------------------------
# Catálogo de órganos y normalización
# ---------------------------------------------------------
ORG_MAP = {
    "ts": "Tribunal Supremo (TS)",
    "tribunal supremo": "Tribunal Supremo (TS)",
    "supremo": "Tribunal Supremo (TS)",
    "tsjc": "Tribunal Superior de Justicia de Cataluña (TSJC)",
    "tribunal superior de justicia de cataluna": "Tribunal Superior de Justicia de Cataluña (TSJC)",
    "tribunal superior de justicia de cataluña": "Tribunal Superior de Justicia de Cataluña (TSJC)",
    "audiencia nacional": "Audiencia Nacional (AN)",
    "an": "Audiencia Nacional (AN)",
}
def _normalize_organo(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    key = _normalize(s)
    return ORG_MAP.get(key, s)  # si no mapea, deja lo que vino

# ---------------------------------------------------------
# Diccionario de sinónimos (extensible)
# ---------------------------------------------------------
SYNONYMS = {
    "fuera de ordenacion": ["situacion de fuera de ordenacion", "edificacion disconforme", "ordenacion urbanistica"],
    "volumen disconforme": ["alteracion de volumen", "edificacion disconforme", "aumento de edificabilidad"],
    "suelo no urbanizable": ["suelo rustico", "suelos protegidos", "suelo no urbanizable comun", "autorizacion excepcional"],
    "garaje ilegal": ["garaje sin licencia", "aparcamiento ilegal", "construccion ilegal garaje"],
}

def _expand_with_synonyms(tokens: List[str]) -> List[str]:
    expanded = set(tokens)
    joined = " ".join(tokens)
    # Si el conjunto de tokens coincide con una clave compuesta, añade sus sinónimos
    for key, syns in SYNONYMS.items():
        key_tokens = key.split()
        # coinidencia aproximada: si todos los tokens de la clave están en la query
        if all(t in tokens for t in key_tokens) or key in joined:
            for s in syns:
                for tk in _normalize(s).split():
                    expanded.add(tk)
    return list(expanded)

# ---------------------------------------------------------
# Dataset "mock" (simula respuestas CENDOJ)
# ---------------------------------------------------------
DATA = [
    {
        "id_cendoj": "0801932001202400077",
        "titulo": "Licencia urbanística en suelo no urbanizable: criterios recientes",
        "organo": "Tribunal Superior de Justicia de Cataluña (TSJC)",
        "sala": "Sala de lo Contencioso-Administrativo",
        "ponente": "Ponente C",
        "fecha": "2024-02-12",
        "relevancia": 0.76,
        "url": "https://www.poderjudicial.es/search/indexAN.jsp",
        "url_detalle": "https://www.poderjudicial.es/search/cedula.jsp?id=0801932001202400077",
        "norm": _normalize("Licencia urbanística en suelo no urbanizable: criterios recientes"),
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
        "norm": _normalize("Sentencia ejemplo sobre 'volumen disconforme'"),
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
        "norm": _normalize("Sentencia ejemplo sobre 'fuera de ordenación' en suelo urbano"),
    },
]

# ---------------------------------------------------------
# Búsqueda en el "índice" mock
# ---------------------------------------------------------
def _filter_by_tokens(items: List[Dict[str, Any]], tokens: List[str], mode_all: bool = True) -> List[Dict[str, Any]]:
    if not tokens:
        return items[:]
    out = []
    for it in items:
        text = it["norm"]
        if mode_all:
            ok = all(tok in text for tok in tokens)
        else:
            ok = any(tok in text for tok in tokens)
        if ok:
            out.append(it)
    return out

def _apply_organo(items: List[Dict[str, Any]], organo: Optional[str]) -> List[Dict[str, Any]]:
    if not organo:
        return items
    return [it for it in items if _normalize(it["organo"]) == _normalize(organo)]

def _apply_dates(items: List[Dict[str, Any]], d_from: Optional[datetime], d_to: Optional[datetime]) -> List[Dict[str, Any]]:
    if not d_from and not d_to:
        return items
    def in_range(it_date: str) -> bool:
        d = datetime.strptime(it_date, "%Y-%m-%d")
        if d_from and d < d_from:
            return False
        if d_to and d > d_to:
            return False
        return True
    return [it for it in items if in_range(it["fecha"])]

def _order(items: List[Dict[str, Any]], orden: str) -> List[Dict[str, Any]]:
    if orden == "relevancia_desc":
        return sorted(items, key=lambda x: x.get("relevancia", 0.0), reverse=True)
    # por defecto, fecha_desc
    return sorted(items, key=lambda x: x["fecha"], reverse=True)

# ---------------------------------------------------------
# Health
# ---------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

# ---------------------------------------------------------
# Endpoint principal de búsqueda
# ---------------------------------------------------------
@app.get("/buscar-cendoj")
def buscar_cendoj(
    query: str = Query(..., min_length=1, description="Términos de búsqueda"),
    organo: Optional[str] = Query(None, description="Órgano (TS, TSJC, Audiencia Nacional, etc.)"),
    desde: Optional[str] = Query(None, description="Fecha inicial (YYYY-MM-DD)"),
    hasta: Optional[str] = Query(None, description="Fecha final (YYYY-MM-DD)"),
    orden: Optional[str] = Query("fecha_desc", description="fecha_desc | relevancia_desc"),
    limite: Optional[int] = Query(5, ge=1, le=25, description="Máximo de resultados (1..25)"),
):
    """
    Motor de búsqueda mock:
    - Normaliza términos y prueba en varias rondas:
      1) AND estricto
      2) OR relajado
      3) OR con sinónimos
      Además, si hay filtros de órgano/fechas que impiden resultados, relaja progresivamente
      y lo deja reflejado en 'nota'.
    - Ordena y limita resultados.
    """

    nota_parts: List[str] = []

    # Normalización de query y tokenización
    q_norm = _normalize(query)
    if not q_norm:
        raise HTTPException(status_code=400, detail="La consulta no puede quedar vacía tras normalizar.")

    tokens = q_norm.split()

    # Fechas
    d_from = _parse_date(desde) if desde else None
    d_to = _parse_date(hasta) if hasta else None
    if d_from and d_to and d_from > d_to:
        # swap automático + nota
        d_from, d_to = d_to, d_from
        nota_parts.append("Se corrigió el rango de fechas (invertido).")

    # Órgano
    organo_norm = _normalize_organo(organo)

    # Validación de orden
    orden_norm = orden if orden in ("fecha_desc", "relevancia_desc") else "fecha_desc"

    # Round 0: universo de búsqueda (aplica órgano/fechas si vienen)
    base = DATA[:]
    base = _apply_organo(base, organo_norm)
    base = _apply_dates(base, d_from, d_to)

    # Ronda 1: AND estricto
    results = _filter_by_tokens(base, tokens, mode_all=True)

    # Ronda 2: si vacío, OR relajado
    if not results:
        results = _filter_by_tokens(base, tokens, mode_all=False)
        if results:
            nota_parts.append("Resultados aproximados (coincidencia parcial con el texto de búsqueda).")

    # Ronda 3: si sigue vacío, OR con sinónimos
    if not results:
        expanded = _expand_with_synonyms(tokens)
        if set(expanded) != set(tokens):
            results = _filter_by_tokens(base, expanded, mode_all=False)
            if results:
                nota_parts.append("Se usaron sinónimos comunes para ampliar la coincidencia.")

    # Ronda 4: relajar filtros si siguen vacíos (órgano y/o fechas)
    relaxed = False
    if not results and (organo_norm or d_from or d_to):
        relaxed_base = DATA[:]
        # probar quitando órgano (si había)
        if organo_norm:
            relaxed_base = _apply_dates(relaxed_base, d_from, d_to)
            tmp = _filter_by_tokens(relaxed_base, tokens, mode_all=False)
            if not tmp:
                # probar también quitando fechas
                tmp = _filter_by_tokens(DATA, tokens, mode_all=False)
                if tmp:
                    nota_parts.append("Se relajaron órgano y fechas para encontrar coincidencias.")
                    results = tmp
                    relaxed = True
            else:
                nota_parts.append("Se relajó el filtro de órgano para encontrar coincidencias.")
                results = tmp
                relaxed = True
        else:
            # no había órgano; probar solo quitando fechas
            relaxed_base = DATA[:]
            tmp = _filter_by_tokens(relaxed_base, tokens, mode_all=False)
            if tmp:
                nota_parts.append("Se relajó el filtro de fechas para encontrar coincidencias.")
                results = tmp
                relaxed = True

        # Si tras relajar sigue vacío, intentar sinónimos sobre todo el set
        if not results:
            expanded = _expand_with_synonyms(tokens)
            tmp = _filter_by_tokens(DATA, expanded, mode_all=False)
            if tmp:
                nota_parts.append("Se usaron sinónimos y se relajaron filtros para ampliar la búsqueda.")
                results = tmp
                relaxed = True

    # Ordenar y limitar
    results = _order(results, orden_norm)[:limite] if results else []

    # Preparar respuesta
    payload = {
        "query": query,
        "total": len(results),
        "resultados": [
            {
                "id_cendoj": r.get("id_cendoj"),
                "titulo": r["titulo"],
                "organo": r["organo"],
                "sala": r.get("sala"),
                "ponente": r.get("ponente"),
                "fecha": _fmt_date(r["fecha"]),
                "relevancia": r.get("relevancia"),
                "url": r["url"],
                "url_detalle": r.get("url_detalle"),
            }
            for r in results
        ],
        "nota": " ".join(nota_parts) if nota_parts else None,
    }

    return payload