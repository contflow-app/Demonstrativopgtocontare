\
from __future__ import annotations
import pandas as pd
from typing import Optional, Tuple
from .utils import normalize_name

def load_salario_real_xlsx(xlsx_path: str) -> pd.DataFrame:
    df = pd.read_excel(xlsx_path)
    # Expect columns like: CPF, NOME, TOTAL BRUTO 2026
    # Normalize:
    cols = {c: str(c).strip().upper() for c in df.columns}
    df.rename(columns=cols, inplace=True)

    # Try common names
    def pick(*names):
        for n in names:
            if n in df.columns:
                return n
        return None

    col_cpf = pick("CPF")
    col_nome = pick("NOME", "COLABORADOR", "FUNCIONARIO")
    col_bruto = pick("TOTAL BRUTO 2026", "TOTAL BRUTO", "BRUTO", "SALARIO REAL", "SALÁRIO REAL")

    if col_bruto is None:
        raise ValueError("Não encontrei a coluna de salário real bruto na planilha. Ex.: 'TOTAL BRUTO 2026'.")

    df["CPF_NORM"] = df[col_cpf].astype(str).str.strip() if col_cpf else None
    df["NOME_NORM"] = df[col_nome].astype(str).map(normalize_name) if col_nome else None
    df["SALARIO_REAL_BRUTO"] = pd.to_numeric(df[col_bruto], errors="coerce")
    return df

def find_salario_real(df_sal: pd.DataFrame, cpf: Optional[str], nome: Optional[str]) -> Tuple[Optional[float], str]:
    """
    Returns (salary, match_status)
    match_status: CPF | NOME | NAO_ENCONTRADO
    """
    if cpf:
        hit = df_sal[df_sal["CPF_NORM"] == str(cpf).strip()]
        if len(hit) >= 1:
            return float(hit.iloc[0]["SALARIO_REAL_BRUTO"]), "CPF"

    if nome:
        nn = normalize_name(nome)
        hit = df_sal[df_sal["NOME_NORM"] == nn]
        if len(hit) >= 1:
            return float(hit.iloc[0]["SALARIO_REAL_BRUTO"]), "NOME"

    return None, "NAO_ENCONTRADO"
