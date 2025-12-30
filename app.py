from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from typing import Optional, Tuple, Dict

import pandas as pd
import streamlit as st

from src.cargos import cargo_final, infer_familia, nivel_por_salario
from src.export_xlsx import export_consolidado_xlsx
from src.matching import find_salario_real, load_salario_real_xlsx
from src.parsing_pdf import parse_pdf_with_fallback
from src.receipts_pdf import generate_all_receipts

APP_TITLE = "Demonstrativo de Pagamento Contare"
LOGO_PATH = str(Path(__file__).parent / "assets" / "logo.png")


# -----------------------------
# Normalizações
# -----------------------------
def cpf_digits(cpf: Optional[str]) -> str:
    if not cpf:
        return ""
    return re.sub(r"\D", "", str(cpf))


def safe_float(x) -> Optional[float]:
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return None
        return float(x)
    except Exception:
        return None


# -----------------------------
# Helpers (CSV de eventos/pagamentos em folha)
# -----------------------------
def _detect_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    cols = {str(c).strip().upper(): c for c in df.columns}
    for cand in candidates:
        if cand.upper() in cols:
            return cols[cand.upper()]
    # fuzzy: contains
    for uc, orig in cols.items():
        for cand in candidates:
            if cand.upper() in uc:
                return orig
    return None


def _to_float_series(s: pd.Series) -> pd.Series:
    # converter pt-BR "1.234,56" para float
    def conv(v):
        if pd.isna(v):
            return None
        t = str(v).strip()
        t = t.replace("\xa0", " ").replace(" ", "")
        t = re.sub(r"[^0-9\.,\-]", "", t)
        if not t:
            return None
        if "," in t:
            t = t.replace(".", "").replace(",", ".")
        try:
            return float(t)
        except ValueError:
            return None

    return s.map(conv)


def load_eventos_csv(file_bytes: bytes, keywords_regex: str) -> Tuple[pd.DataFrame, Dict[str, float], Dict[str, float]]:
    """
    Lê CSV de eventos da folha e cria mapas:
      - pago_por_cpf[cpf_digits] = soma_valores_pagamento
      - pago_por_matricula[matricula] = soma_valores_pagamento
    """
    df = None
    for sep in [",", ";", "\t"]:
        try:
            df = pd.read_csv(pd.io.common.BytesIO(file_bytes), sep=sep, dtype=str, encoding_errors="ignore")
            if df is not None and df.shape[1] >= 2:
                break
        except Exception:
            df = None

    if df is None:
        raise ValueError("Não foi possível ler o CSV (tente exportar separado por ';' ou ',').")

    col_cpf = _detect_column(df, ["CPF", "CPF FUNCIONARIO", "CPF FUNCIONÁRIO"])
    col_matricula = _detect_column(df, ["MATRICULA", "MATRÍCULA", "EMPR", "EMPREGADO", "CODIGO", "CÓDIGO"])
    col_desc = _detect_column(df, ["DESCRICAO", "DESCRIÇÃO", "HISTORICO", "HISTÓRICO", "EVENTO", "RUBRICA", "VERBA"])
    col_val = _detect_column(df, ["VALOR", "VALOR_R$", "VALOR R$", "IMPORTANCIA", "IMPORTÂNCIA", "VLR"])

    if col_desc is None or col_val is None:
        raise ValueError("No CSV não identifiquei colunas de DESCRIÇÃO/EVENTO e VALOR.")

    df_norm = df.copy()
    df_norm["CPF_DIGITS"] = df_norm[col_cpf].map(cpf_digits) if col_cpf else ""
    df_norm["MATRICULA_NORM"] = df_norm[col_matricula].astype(str).str.strip() if col_matricula else ""
    df_norm["DESC_UP"] = df_norm[col_desc].astype(str).str.upper().str.strip()
    df_norm["VALOR_NUM"] = _to_float_series(df_norm[col_val])

    re_paid = re.compile(keywords_regex, re.IGNORECASE)

    df_paid = df_norm[df_norm["DESC_UP"].map(lambda x: bool(re_paid.search(x or "")))].copy()
    df_paid = df_paid[df_paid["VALOR_NUM"].notna()]

    pago_por_cpf: Dict[str, float] = {}
    pago_por_matricula: Dict[str, float] = {}

    for _, r in df_paid.iterrows():
        amt = float(r["VALOR_NUM"])
        # Considera somente valores positivos como "pagamento"
        if amt <= 0:
            continue

        cpf_d = (r.get("CPF_DIGITS") or "").strip()
        mat = (r.get("MATRICULA_NORM") or "").strip()

        if cpf_d:
            pago_por_cpf[cpf_d] = pago_por_cpf.get(cpf_d, 0.0) + amt
        elif mat:
            pago_por_matricula[mat] = pago_por_matricula.get(mat, 0.0) + amt

    return df_norm, pago_por_cpf, pago_por_matricula


# -----------------------------
# UI
# -----------------------------
st.set_page_config(page_title=APP_TITLE, layout="wide")

col1, col2 = st.columns([1, 4])
with col1:
    if Path(LOGO_PATH).exists():
        st.image(LOGO_PATH, width=180)
with col2:
    st.title(APP_TITLE)
    st.caption(
        "Regra: **Valor a pagar = Bruto (planilha Excel) − (Líquido do PDF + pagamentos em folha do CSV quando líquido=0,00)**"
    )

st.divider()

st.sidebar.header("Configurações")
use_gpt = st.sidebar.toggle("Usar GPT como fallback (se campos críticos faltarem)", value=False)
openai_model = st.sidebar.text_input("Modelo OpenAI", value=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
empresa_nome = st.sidebar.text_input("Nome da empresa no recibo", value="Contare")

st.sidebar.subheader("Tratativa de líquido zerado")
keywords = st.sidebar.text_input(
    "Palavras-chave (CSV) para reconhecer pagamentos em folha (regex)",
    value=r"(ADIANT|ADTO|VALE|ANTECIP|ANTECIPA|PAGAMENTO|TRANSFER|PIX)",
)
st.sidebar.caption("Dica: ajuste aqui para bater com o nome das rubricas/verb as do seu sistema.")

st.sidebar.markdown("---")
st.sidebar.caption("Se ativar GPT, defina OPENAI_API_KEY no ambiente.")

pdf_file = st.file_uploader("1) Suba o PDF do Extrato Mensal", type=["pdf"])
xlsx_file = st.file_uploader("2) Suba a planilha de salário real (XLSX)", type=["xlsx"])
csv_file = st.file_uploader(
    "3) (Opcional) CSV de eventos/pagamentos em folha (adiantamento/vale etc.)",
    type=["csv"],
)

if not pdf_file or not xlsx_file:
    st.info("Envie o PDF e a planilha para processar. O CSV é opcional.")
    st.stop()

workdir = Path(st.session_state.get("workdir", Path.cwd() / ".tmp_streamlit"))
workdir.mkdir(parents=True, exist_ok=True)
st.session_state["workdir"] = str(workdir)

pdf_path = workdir / "extrato.pdf"
xlsx_path = workdir / "salario_real.xlsx"
pdf_path.write_bytes(pdf_file.getbuffer())
xlsx_path.write_bytes(xlsx_file.getbuffer())

pago_por_cpf: Dict[str, float] = {}
pago_por_matricula: Dict[str, float] = {}
if csv_file:
    try:
        _, pago_por_cpf, pago_por_matricula = load_eventos_csv(csv_file.getbuffer().tobytes(), keywords_regex=keywords)
        st.success("CSV carregado. Vou somar pagamentos do CSV quando o líquido do PDF for 0,00.")
    except Exception as e:
        st.warning(f"Não consegui usar o CSV: {e}. Vou seguir só com PDF+XLSX.")

if st.button("Processar", type="primary"):
    with st.spinner("Extraindo dados do PDF..."):
        extracao = parse_pdf_with_fallback(
            str(pdf_path),
            use_gpt_fallback=use_gpt,
            openai_model=openai_model,
        )

    with st.spinner("Cruzando com planilha, atribuindo cargos e calculando..."):
        df_sal = load_salario_real_xlsx(str(xlsx_path))
        final_rows = []
        pendencias = []

        for c in extracao.colaboradores:
            competencia = c.competencia or extracao.competencia_global

            # alvo = bruto planilha
            salario_real_bruto, match_status = find_salario_real(df_sal, c.cpf, c.nome)

            # líquido do PDF
            liquido_pdf = safe_float(c.liquido)

            # cargos
            familia = infer_familia(c.cargo_pdf)
            nivel = nivel_por_salario(salario_real_bruto) if salario_real_bruto is not None else None
            cargo_plano = cargo_final(nivel, familia)

            notas = list(c.warnings or [])
            status = "OK"

            evidencia_liquido = getattr(c, "evidence", None).liquido if getattr(c, "evidence", None) else None
            evidencia_cpf = getattr(c, "evidence", None).cpf if getattr(c, "evidence", None) else None

            # pagamentos via CSV (somente quando liquido=0,00)
            pago_csv = 0.0
            if (liquido_pdf is not None) and (abs(liquido_pdf) < 0.00001) and (pago_por_cpf or pago_por_matricula):
                cpf_d = cpf_digits(c.cpf)
                if cpf_d and cpf_d in pago_por_cpf:
                    pago_csv = float(pago_por_cpf.get(cpf_d, 0.0))
                elif c.matricula and str(c.matricula).strip() in pago_por_matricula:
                    pago_csv = float(pago_por_matricula.get(str(c.matricula).strip(), 0.0))

            # pendências mínimas
            if liquido_pdf is None:
                status = "PENDENTE"
                notas.append("líquido não extraído do PDF")
            else:
                if abs(liquido_pdf) < 0.00001:
                    if pago_csv > 0:
                        notas.append(f"Líquido=0,00; somando pagamentos do CSV: {pago_csv:.2f}")
                    else:
                        notas.append("Líquido=0,00 (confirmado no PDF). Se houve adiantamento/vale, ajuste palavras-chave ou CSV.")

            if salario_real_bruto is None:
                status = "PENDENTE" if status == "OK" else status
                notas.append(f"salário real bruto não encontrado (match: {match_status})")

            if not c.cpf:
                status = "REVISAR" if status == "OK" else status
                notas.append("CPF ausente/ambíguo (recibo pode ser gerado por matrícula)")

            # cálculo final:
            total_pago = None
            diferenca = None
            valor_a_pagar = None

            if salario_real_bruto is not None and liquido_pdf is not None:
                total_pago = float(liquido_pdf)
                # tratativa específica: se líquido do PDF = 0,00, somar pagamentos do CSV
                if abs(liquido_pdf) < 0.00001 and pago_csv > 0:
                    total_pago += float(pago_csv)

                diferenca = float(salario_real_bruto) - float(total_pago)
                valor_a_pagar = max(diferenca, 0.0)

                if diferenca < 0:
                    status = "INCONSISTENTE"
                    notas.append("diferença negativa (bruto planilha < total pago reconhecido)")

            if getattr(c, "confidence", None) and c.confidence.liquido < 0.85:
                status = "REVISAR" if status == "OK" else status
                notas.append("confidence do líquido baixa")

            row = {
                "competencia_global": extracao.competencia_global,
                "competencia": competencia,

                "matricula": c.matricula,
                "nome": c.nome,
                "cpf": c.cpf,

                "cargo_pdf": c.cargo_pdf,
                "familia": familia,
                "nivel": nivel,
                "cargo_final": cargo_plano,

                # IMPORTANTE: manter estes nomes para o recibo achar:
                "liquido_folha": liquido_pdf,          # <— receipts_pdf usa este
                "liquido_folha_pdf": liquido_pdf,      # <— para seu controle
                "pagamentos_folha_csv": pago_csv if (pago_por_cpf or pago_por_matricula) else None,
                "total_pago_folha": total_pago,

                "salario_real_bruto_planilha": salario_real_bruto,
                "diferenca_calculada": diferenca,
                "valor_a_pagar": valor_a_pagar,

                "evidencia_cpf": evidencia_cpf,
                "evidencia_liquido": evidencia_liquido,

                "status": status,
                "notas": "; ".join(notas) if notas else "",
                "match_salario_real": match_status,
            }

            final_rows.append(row)
            if status != "OK":
                pendencias.append(row)

        st.session_state["final_rows"] = final_rows
        st.session_state["pendencias"] = pendencias

final_rows = st.session_state.get("final_rows")
if final_rows:
    df = pd.DataFrame(final_rows)
    st.subheader("Prévia (consolidado)")
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Pendências / Revisar")
    conf_rows = st.session_state.get("pendencias", [])
    dfp = pd.DataFrame(conf_rows)
    st.dataframe(dfp, use_container_width=True, hide_index=True)

    out_xlsx = workdir / "demonstrativo_complemento.xlsx"
    out_conf_xlsx = workdir / "relatorio_conferencia.xlsx"
    out_receipts_dir = workdir / "recibos"
    out_zip = workdir / "recibos_pdf.zip"

    colA, colB, colC = st.columns(3)

    with colA:
        if st.button("Gerar Excel (Consolidado)"):
            export_consolidado_xlsx(
                final_rows,
                str(out_xlsx),
                logo_path=LOGO_PATH if Path(LOGO_PATH).exists() else None,
            )
            st.success("Excel consolidado gerado.")
            st.download_button(
                "Baixar Excel (Consolidado)",
                data=out_xlsx.read_bytes(),
                file_name="demonstrativo_complemento.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    with colB:
        if st.button("Gerar Excel (Conferência)"):
            export_consolidado_xlsx(
                conf_rows,
                str(out_conf_xlsx),
                logo_path=LOGO_PATH if Path(LOGO_PATH).exists() else None,
            )
            st.success("Excel de conferência gerado.")
            st.download_button(
                "Baixar Excel (Conferência)",
                data=out_conf_xlsx.read_bytes(),
                file_name="relatorio_conferencia.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    with colC:
        if st.button("Gerar ZIP de Recibos PDF"):
            out_receipts_dir.mkdir(parents=True, exist_ok=True)
            eligible = [
                r for r in final_rows
                if r.get("valor_a_pagar") is not None and float(r.get("valor_a_pagar")) > 0
            ]
            pdfs = generate_all_receipts(
                eligible,
                str(out_receipts_dir),
                logo_path=LOGO_PATH if Path(LOGO_PATH).exists() else None,
                empresa_nome=empresa_nome,
            )

            with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
                for p in pdfs:
                    z.write(p, arcname=Path(p).name)

            st.success(f"ZIP gerado ({len(pdfs)} recibos).")
            st.download_button(
                "Baixar ZIP de Recibos",
                data=out_zip.read_bytes(),
                file_name="recibos_pdf.zip",
                mime="application/zip",
            )
