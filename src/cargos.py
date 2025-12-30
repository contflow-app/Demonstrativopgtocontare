\
from __future__ import annotations
import json
from pathlib import Path
from typing import Tuple, Optional, List, Dict
from .utils import safe_contains

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

def load_plano() -> List[dict]:
    path = TEMPLATES_DIR / "plano_cargos.json"
    return json.loads(path.read_text(encoding="utf-8"))

def load_familias_keywords() -> Dict[str, str]:
    path = TEMPLATES_DIR / "familias_keywords.json"
    return json.loads(path.read_text(encoding="utf-8"))

def infer_familia(cargo_pdf: str) -> str:
    cargo_up = (cargo_pdf or "").upper()
    mapping = load_familias_keywords()
    for k, fam in mapping.items():
        if k.upper() in cargo_up:
            return fam
    return "Outros"

def nivel_por_salario(salario_real_bruto: float) -> Optional[str]:
    if salario_real_bruto is None:
        return None
    plano = load_plano()
    # nearest reference
    best = None
    best_diff = None
    for row in plano:
        ref = float(row["ref"])
        diff = abs(salario_real_bruto - ref)
        if best is None or diff < best_diff:
            best = row["nivel"]
            best_diff = diff
    return best

def cargo_final(nivel: Optional[str], familia: Optional[str]) -> Optional[str]:
    if not nivel:
        return None
    fam = familia or "Outros"
    return f"{nivel} {fam}"
