from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import pandas as pd
import streamlit as st

from src.cargos import cargo_final, infer_familia, nivel_por_salario
from src.export_xlsx import export_consolidado_xlsx
from src.matching import find_salario_real, load_salario_real_xlsx
from src.parsing_pdf import parse_pdf_with_fallback
from src.receipts_pdf import generate_all_receipts

APP_TITLE = "Demonstrativo de Pagamento Contare"
LOGO_PATH = str(Path(__file__).parent / "assets" / "logo.png")

EPS_ZERO = 1e-6


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


def _detect_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    cols = {str(c).strip().upper(): c for c in df.columns}
    for cand in candidates:
        if cand.upper() in cols:
            return cols[cand.upper()]
    # contains
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


# -----------------------------
# Status ATIVO (vem da planilha de salário real)
# -----------------------------
def _get_status_info(df_sal: pd.DataFrame, cpf: Optional[str], nome: Optional[str]) -> tuple[Optional[str], Optional[bool]]:
    """
    Tenta obter STATUS da mesma linha encontrada no match de salário real.
    Considera ATIVO se:
      - STATUS contém 'ATIV' (ATIVO/ATIVA)
      - ou se não existir coluna de status, retorna None (desconhecido)
    """
    status_col = _detect_column(df_sal, ["STATUS", "SITUAÇÃO", "SITUACAO", "ATIVO"])
    if status_col is None:
        return None, None

    # Tenta localizar a linha do colaborador igual ao find_salario_real
    cpf_col = _detect_column(df_sal, ["CPF"])
    nome_col = _detect_column(df_sal, ["NOME", "COLABORADOR", "FUNCIONARIO", "FUNCIONÁRIO"])

    hit = None
    if cpf_col and cpf:
        hit = df_sal[df_sal[cpf_col].astype(str).str.strip() == str(cpf).strip()]
        if len(hit) >= 1:
            val = str(hit.iloc[0][status_col]).strip()
            return val, ("ATIV" in val.upper())

    if nome_col and nome:
        nn = re.sub(r"\s+", " ", str(nome)).strip().upper()
        ser = df_sal[nome_col].astype(str).map(lambda x: re.sub(r"\s+", " ", str(x)).strip().upper())
        hit = df_sal[ser == nn]
        if len(hit) >= 1:
            val = str(hit.iloc[0][status_col]).strip()
            return val, ("ATIV" in val.upper())

    return None, None


# -----------------------------
# CSV de eventos: capturar verbas 8781 e 981 por CPF/Matrícula
# -----------------------------
def load_eventos_csv_verbas(file_bytes: bytes) -> Tuple[pd.DataFrame, Dict[str, float], Dict[str, float], Dict[str, float], Dict[str, float]]:
    """
    Retorna:
      df_norm,
      verba8781_por_cpf, verba981_por_cpf,
      verba8781_por_mat, verba981_por_mat

    Observação: valores retornam sempre como ABS(valor) somado, para evitar sinal invertido no CSV.
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
    col_verba = _detect_column(df, ["VERBA", "COD VERBA", "CÓDIGO VERBA", "CODIGO VERBA", "EVENTO", "CÓDIGO", "CODIGO"])
    col_desc = _detect_column(df, ["DESCRICAO", "DESCRIÇÃO", "HISTORICO", "HISTÓRICO", "RUBRICA"])
    col_val = _detect_column(df, ["VALOR", "VALOR_R$", "VALOR R$", "IMPORTANCIA", "IMPORTÂNCIA", "VLR"])

    if col_val is None:
        raise ValueError("No CSV não identifiquei a coluna de VALOR.")
    if col_verba is None and col_desc is None:
        raise ValueError("No CSV não identifiquei coluna de VERBA/CÓDIGO nem DESCRIÇÃO para localizar 8781/981.")

    df_norm = df.copy()
    df_norm["CPF_DIGITS"] = df_norm[col_cpf].map(cpf_digits) if col_cpf else ""
    df_norm["MATRICULA_NORM"] = df_norm[col_matricula].astype(str).str.strip() if col_matricula else ""
    df_norm["VALOR_NUM"] = _to_float_series(df_norm[col_val])

    if col_verba:
        df_norm["VERBA_NORM"] = df_norm[col_verba].astype(str).str.strip()
    else:
        df_norm["VERBA_NORM"] = ""

    if col_desc:
        df_norm["DESC_UP"] = df_norm[col_desc].astype(str).str.upper().str.strip()
    else:
        df_norm["DESC_UP"] = ""

    verba8781_por_cpf: Dict[str, float] = {}
    verba981_por_cpf: Dict[str, float] = {}
    verba8781_por_mat: Dict[str, float] = {}
    verba981_por_mat: Dict[str, float] = {}

    def add(mapper: Dict[str, float], key: str, amount: float):
        if not key:
            return
        mapper[key] = mapper.get(key, 0.0) + abs(float(amount))

    for _, r in df_norm.iterrows():
        v = r.get("VALOR_NUM")
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue

        verba = str(r.get("VERBA_NORM") or "").strip()
        desc = str(r.get("DESC_UP") or "")

        is_8781 = (verba == "8781") or ("8781" in desc)
        is_981 = (verba == "981") or (" 981" in desc) or ("981 " in desc) or ("981" in desc)

        if not (is_8781 or is_981):
            continue

        cpf_d = str(r.get("CPF_DIGITS") or "").strip()
        mat = str(r.get("MATRICULA_NORM") or "").strip()

        if is_8781:
            if cpf_d:
                add(verba8781_por_cpf, cpf_d, v)
            elif mat:
                add(verba8781_por_mat, mat, v)

        if is_981:
            if cpf_d:
                add(verba981_por_cpf, cpf_d, v)
            elif mat:
                add(verba981_por_mat, mat, v)

    return df_norm, verba8781_por_cpf, verba981_por_cpf, verba8781_por_mat, verba981_por_mat


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
        "Regra geral: **Valor a pagar = Bruto(planilha) − Líquido(PDF)**.\n\n"
        "Regra especial: se **Líquido(PDF)=0,00** e colaborador **ATIVO**, então:\n"
        "**Valor a pagar = Bruto(planilha) − verba 8781 − verba 981**"
    )

st.divider()

st.sidebar.header("Configurações")
use_gpt = st.sidebar.toggle("Usar GPT como fallback (se campos críticos faltarem)", value=False)
openai_model = st.sidebar.text_input("Modelo OpenAI", value=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
empresa_nome = st.sidebar.text_input("Nome da empresa no recibo", value="Contare")

st.sidebar.subheader("Líquido=0,00 (ATIVO)")
st.sidebar.write("Usar verbas do CSV/anexo: **8781 (salário contratual)** e **981 (desc. adiantamento)**.")
st.sidebar.caption("Garanta que o CSV tenha coluna de VERBA/CÓDIGO, ou pelo menos DESCRIÇÃO contendo 8781/981.")

st.sidebar.markdown("---")
st.sidebar.caption("Se ativar GPT, defina OPENAI_API_KEY no ambiente.")

pdf_file = st.file_uploader("1) Suba o PDF do Extrato Mensal", type=["pdf"])
xlsx_file = st.file_uploader("2) Suba a planilha de salário real (XLSX)", type=["xlsx"])
csv_file = st.file_uploader("3) (Opcional, mas necessário p/ líquido=0) CSV de eventos/verbas (8781 e 981)", type=["csv"])

if not pdf_file or not xlsx_file:
    st.info("Envie o PDF e a planilha para processar. O CSV é opcional, mas necessário para tratar líquido=0 com 8781/981.")
    st.stop()

workdir = Path(st.session_state.get("workdir", Path.cwd() / ".tmp_streamlit"))
workdir.mkdir(parents=True, exist_ok=True)
st.session_state["workdir"] = str(workdir)

pdf_path = workdir / "extrato.pdf"
xlsx_path = workdir / "salario_real.xlsx"
pdf_path.write_bytes(pdf_file.getbuffer())
xlsx_path.write_bytes(xlsx_file.getbuffer())

verba8781_por_cpf: Dict[str, float] = {}
verba981_por_cpf: Dict[str, float] = {}
verba8781_por_mat: Dict[str, float] = {}
verba981_por_mat: Dict[str, float] = {}

if csv_file:
    try:
        _, verba8781_por_cpf, verba981_por_cpf, verba8781_por_mat, verba981_por_mat = load_eventos_csv_verbas(
            csv_file.getbuffer().tobytes()
        )
        st.success("CSV carregado. Vou usar verbas 8781 e 981 quando líquido=0,00 e status=ATIVO.")
    except Exception as e:
        st.warning(f"Não consegui usar o CSV para verbas 8781/981: {e}. (Casos de líquido=0 ficarão para conferência.)")

if st.button("Processar", type="primary"):
    with st.spinner("Extraindo dados do PDF..."):
        extracao = parse_pdf_with_fallback(
            str(pdf_path),
            use_gpt_fallback=use_gpt,
            openai_model=openai_model,
        )

    with st.spinner("Cruzando com planilha, atribuindo cargos e calculando valor a pagar..."):
        df_sal = load_salario_real_xlsx(str(xlsx_path))
        final_rows: list[dict[str, Any]] = []
        pendencias: list[dict[str, Any]] = []

        for c in extracao.colaboradores:
            competencia = c.competencia or extracao.competencia_global

            bruto_planilha, match_status = find_salario_real(df_sal, c.cpf, c.nome)
            liquido_pdf = safe_float(c.liquido)

            status_txt, is_ativo = _get_status_info(df_sal, c.cpf, c.nome)

            familia = infer_familia(c.cargo_pdf)
            nivel = nivel_por_salario(bruto_planilha) if bruto_planilha is not None else None
            cargo_plano = cargo_final(nivel, familia)

            notas = list(c.warnings or [])
            status = "OK"

            evidencia_liquido = getattr(c, "evidence", None).liquido if getattr(c, "evidence", None) else None
            evidencia_cpf = getattr(c, "evidence", None).cpf if getattr(c, "evidence", None) else None

            # valores de verbas (somente para regra especial)
            v8781 = 0.0
            v981 = 0.0

            cpf_d = cpf_digits(c.cpf)
            mat = str(c.matricula).strip() if c.matricula is not None else ""

            if cpf_d:
                v8781 = float(verba8781_por_cpf.get(cpf_d, 0.0))
                v981 = float(verba981_por_cpf.get(cpf_d, 0.0))
            elif mat:
                v8781 = float(verba8781_por_mat.get(mat, 0.0))
                v981 = float(verba981_por_mat.get(mat, 0.0))

            # Pendências mínimas
            if liquido_pdf is None:
                status = "PENDENTE"
                notas.append("líquido não extraído do PDF")

            if bruto_planilha is None:
                status = "PENDENTE" if status == "OK" else status
                notas.append(f"salário bruto (planilha) não encontrado (match: {match_status})")

            if not c.cpf:
                status = "REVISAR" if status == "OK" else status
                notas.append("CPF ausente/ambíguo (recibo pode ser gerado por matrícula)")

            # Cálculo
            diferenca = None
            valor_a_pagar = None
            regra_aplicada = "PADRAO: bruto_planilha - liquido_pdf"

            if bruto_planilha is not None and liquido_pdf is not None:
                if abs(liquido_pdf) < EPS_ZERO and (is_ativo is True):
                    # REGRA ESPECIAL
                    regra_aplicada = "ESPECIAL (liq=0 & ATIVO): bruto_planilha - verba8781 - verba981"
                    if v8781 <= 0 and v981 <= 0:
                        status = "REVISAR" if status == "OK" else status
                        notas.append("líquido=0 & ATIVO, mas não encontrei verbas 8781/981 no CSV (verificar anexo/colunas).")

                    diferenca = float(bruto_planilha) - float(v8781) - float(v981)
                    valor_a_pagar = max(float(diferenca), 0.0)
                else:
                    # REGRA PADRÃO
                    diferenca = float(bruto_planilha) - float(liquido_pdf)
                    valor_a_pagar = max(float(diferenca), 0.0)

                if diferenca is not None and diferenca < 0:
                    status = "INCONSISTENTE"
                    notas.append("diferença negativa (verifique dados).")

            # Gate de confiança
            if getattr(c, "confidence", None) and c.confidence.liquido < 0.85:
                status = "REVISAR" if status == "OK" else status
                notas.append("confidence do líquido baixa")

            row = {
                "competencia_global": extracao.competencia_global,
                "competencia": competencia,

                "matricula": c.matricula,
                "nome": c.nome,
                "cpf": c.cpf,

                "status_colaborador": status_txt,
                "is_ativo": is_ativo,

                "cargo_pdf": c.cargo_pdf,
                "familia": familia,
                "nivel": nivel,
                "cargo_final": cargo_plano,

                # nomes compatíveis com recibo:
                "liquido_folha": liquido_pdf,
                "liquido_folha_pdf": liquido_pdf,

                # verbas para regra especial:
                "verba_8781_salario_contratual": v8781 if (v8781 or v981) else None,
                "verba_981_desc_adiantamento": v981 if (v8781 or v981) else None,
                "regra_aplicada": regra_aplicada,

                "salario_real_bruto_planilha": bruto_planilha,
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
    st.dataframe(pd.DataFrame(conf_rows), use_container_width=True, hide_index=True)

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
