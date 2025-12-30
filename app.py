from __future__ import annotations

import os
import re
import json
import zipfile
from pathlib import Path
from typing import Optional, Tuple, Any, List

import pandas as pd
import streamlit as st
import pdfplumber

from src.cargos import cargo_final, infer_familia, nivel_por_salario
from src.export_xlsx import export_consolidado_xlsx
from src.matching import find_salario_real, load_salario_real_xlsx
from src.parsing_pdf import parse_pdf_with_fallback
from src.receipts_pdf import generate_all_receipts

APP_TITLE = "Demonstrativo de Pagamento Contare"
LOGO_PATH = str(Path(__file__).parent / "assets" / "logo.png")

# Considera "zerado" para regra especial (pode ajustar no sidebar)
DEFAULT_LIMIAR_ZERO = 1.0


# -----------------------------
# Helpers
# -----------------------------
def cpf_digits(cpf: Optional[str]) -> str:
    if not cpf:
        return ""
    return re.sub(r"\D", "", str(cpf))


def safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        if isinstance(x, float) and pd.isna(x):
            return None
        return float(x)
    except Exception:
        return None


def parse_brl_money(s: str) -> Optional[float]:
    if s is None:
        return None
    t = str(s).strip().replace("\xa0", " ").replace(" ", "")
    t = re.sub(r"[^0-9\.,\-]", "", t)
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except Exception:
        return None


def extract_full_text(pdf_path: str) -> str:
    parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for p in pdf.pages:
            parts.append(p.extract_text() or "")
    return "\n".join(parts)


def split_blocks(text: str) -> List[str]:
    starts = [m.start() for m in re.finditer(r"\bEmpr\.\:\s*", text, flags=re.IGNORECASE)]
    if not starts:
        return []
    blocks = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(text)
        blocks.append(text[s:e])
    return blocks


def find_block_by_cpf(text: str, cpf: str) -> Optional[str]:
    c = cpf_digits(cpf)
    if not c:
        return None
    for b in split_blocks(text):
        if c in cpf_digits(b):
            return b
    return None


def extract_verbas_8781_981_from_block(block: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Extrai valores da 8781 (P) e 981 (D) diretamente do texto do bloco.
    Retorna (v8781, v981, evidence_str).
    """
    if not block:
        return None, None, None

    flat = block.replace("\n", " ")

    re_8781 = re.compile(r"\b8781\b.*?([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})\s*P\b", re.IGNORECASE)
    re_981 = re.compile(r"\b981\b.*?([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})\s*D\b", re.IGNORECASE)

    m8781 = re_8781.search(flat)
    m981 = re_981.search(flat)

    v8781 = parse_brl_money(m8781.group(1)) if m8781 else None
    v981 = parse_brl_money(m981.group(1)) if m981 else None

    ev = []
    if m8781:
        ev.append(f"8781={m8781.group(1)}")
    if m981:
        ev.append(f"981={m981.group(1)}")

    return v8781, v981, "; ".join(ev) if ev else None


def gpt_extract_verbas(block: str, model: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Fallback GPT: extrair valores das verbas 8781 e 981 do TEXTO do bloco.
    Usa Chat Completions com JSON mode (compatível no Streamlit Cloud).
    """
    try:
        from openai import OpenAI
    except Exception:
        return None, None, "openai lib indisponível"

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, None, "OPENAI_API_KEY não definido"

    client = OpenAI(api_key=api_key)

    system = (
        "Você extrai valores monetários de verbas em um texto de folha. "
        "Extraia APENAS se estiver explícito. Se não encontrar, retorne null. "
        "Não invente valores."
    )
    user = (
        "Do texto a seguir, extraia os valores das verbas:\n"
        "- 8781 (salário contratual) -> valor monetário\n"
        "- 981 (desc adiantamento salarial) -> valor monetário\n\n"
        "Retorne SOMENTE JSON no formato:\n"
        "{\"v8781\": number|null, \"v981\": number|null, \"evidence\": string|null}\n\n"
        f"TEXTO:\n{block}"
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)

        v8781 = data.get("v8781", None)
        v981 = data.get("v981", None)
        ev = data.get("evidence", None)

        try:
            v8781 = float(v8781) if v8781 is not None else None
        except Exception:
            v8781 = None

        try:
            v981 = float(v981) if v981 is not None else None
        except Exception:
            v981 = None

        return v8781, v981, ev

    except Exception as e:
        return None, None, f"erro GPT: {e}"


def infer_ativo_from_pdf_block(block: Optional[str]) -> Optional[bool]:
    """
    Inferir ATIVO pelo texto do bloco:
    - se existir 'Situação: Trabalhando' (ou variações) => ATIVO
    """
    if not block:
        return None
    up = block.upper()
    if "SITUAÇÃO" in up and ("TRABALHANDO" in up or "TRABALH" in up):
        return True
    return None


def detect_status_col(df: pd.DataFrame) -> Optional[str]:
    cols = [str(c).strip().upper() for c in df.columns]
    for cand in ["STATUS", "SITUAÇÃO", "SITUACAO", "ATIVO"]:
        if cand in cols:
            return next(c for c in df.columns if str(c).strip().upper() == cand)
    return None


def is_ativo_from_xlsx(df: pd.DataFrame, cpf: Optional[str], nome: Optional[str]) -> Optional[bool]:
    status_col = detect_status_col(df)
    if not status_col:
        return None

    cpf_col = next((c for c in df.columns if str(c).strip().upper() == "CPF"), None)
    nome_col = next(
        (c for c in df.columns if str(c).strip().upper() in ["NOME", "COLABORADOR", "FUNCIONARIO", "FUNCIONÁRIO"]),
        None,
    )

    if cpf_col and cpf:
        hit = df[df[cpf_col].astype(str).str.strip() == str(cpf).strip()]
        if len(hit) >= 1:
            v = str(hit.iloc[0][status_col]).upper()
            return ("ATIV" in v) or (v in ["SIM", "S", "TRUE", "1"])

    if nome_col and nome:
        nn = re.sub(r"\s+", " ", str(nome)).strip().upper()
        ser = df[nome_col].astype(str).map(lambda x: re.sub(r"\s+", " ", str(x)).strip().upper())
        hit = df[ser == nn]
        if len(hit) >= 1:
            v = str(hit.iloc[0][status_col]).upper()
            return ("ATIV" in v) or (v in ["SIM", "S", "TRUE", "1"])

    return None


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
        "Regra geral: **Valor a pagar = Bruto(planilha) − Líquido(PDF)**.\n"
        "Regra especial (somente quando líquido ~0 e ATIVO):\n"
        "**Valor a pagar = Bruto(planilha) − verba 8781 − verba 981**"
    )

st.divider()

st.sidebar.header("Configurações")
use_gpt = st.sidebar.toggle("Usar GPT (fallback) para extrair 8781/981 quando regex falhar", value=True)
openai_model = st.sidebar.text_input("Modelo OpenAI", value=os.getenv("OPENAI_MODEL", "gpt-4.1"))
empresa_nome = st.sidebar.text_input("Nome da empresa no recibo", value="Contare")
limiar_zero = st.sidebar.number_input(
    "Limiar p/ considerar líquido como 'zerado' (R$)",
    value=float(DEFAULT_LIMIAR_ZERO),
    min_value=0.0,
    step=1.0,
)

st.sidebar.markdown("---")
st.sidebar.caption("Se ativar GPT, defina OPENAI_API_KEY no ambiente.")

pdf_file = st.file_uploader("1) Suba o PDF do Extrato Mensal", type=["pdf"])
xlsx_file = st.file_uploader("2) Suba a planilha de salário real (XLSX)", type=["xlsx"])

if not pdf_file or not xlsx_file:
    st.info("Envie o PDF e a planilha para processar.")
    st.stop()

workdir = Path(st.session_state.get("workdir", Path.cwd() / ".tmp_streamlit"))
workdir.mkdir(parents=True, exist_ok=True)
st.session_state["workdir"] = str(workdir)

pdf_path = workdir / "extrato.pdf"
xlsx_path = workdir / "salario_real.xlsx"
pdf_path.write_bytes(pdf_file.getbuffer())
xlsx_path.write_bytes(xlsx_file.getbuffer())

if st.button("Processar", type="primary"):
    with st.spinner("Extraindo dados do PDF..."):
        extracao = parse_pdf_with_fallback(
            str(pdf_path),
            use_gpt_fallback=False,  # GPT aqui fica apenas para verbas 8781/981 se regex falhar
            openai_model=openai_model,
        )

    with st.spinner("Carregando texto do PDF para tratar verbas 8781/981..."):
        full_text = extract_full_text(str(pdf_path))

    with st.spinner("Cruzando com planilha, atribuindo cargos e calculando valor a pagar..."):
        df_sal = load_salario_real_xlsx(str(xlsx_path))

        final_rows: list[dict[str, Any]] = []
        pendencias: list[dict[str, Any]] = []

        for c in extracao.colaboradores:
            competencia = c.competencia or extracao.competencia_global

            bruto_planilha, match_status = find_salario_real(df_sal, c.cpf, c.nome)
            liquido_pdf = safe_float(c.liquido)

            # localizar bloco do colaborador no PDF (por CPF)
            block = find_block_by_cpf(full_text, c.cpf) if c.cpf else None

            # status ativo: prefere planilha; fallback pelo bloco do PDF
            ativo_xlsx = is_ativo_from_xlsx(df_sal, c.cpf, c.nome)
            ativo_pdf = infer_ativo_from_pdf_block(block)
            is_ativo = ativo_xlsx if ativo_xlsx is not None else ativo_pdf

            # cargos
            familia = infer_familia(c.cargo_pdf)
            nivel = nivel_por_salario(bruto_planilha) if bruto_planilha is not None else None
            cargo_plano = cargo_final(nivel, familia)

            notas = list(c.warnings or [])
            status = "OK"

            evidencia_liquido = getattr(c, "evidence", None).liquido if getattr(c, "evidence", None) else None
            evidencia_cpf = getattr(c, "evidence", None).cpf if getattr(c, "evidence", None) else None

            # extrair verbas 8781/981 do bloco
            v8781, v981, ev_verbas = extract_verbas_8781_981_from_block(block or "")

            # GPT fallback (se ativado) quando regex falhar
            if use_gpt and block and (v8781 is None or v981 is None):
                gv8781, gv981, gev = gpt_extract_verbas(block, model=openai_model)
                v8781 = v8781 if v8781 is not None else gv8781
                v981 = v981 if v981 is not None else gv981
                if gev and not ev_verbas:
                    ev_verbas = gev

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

            # --------
            # REGRA PADRÃO vs ESPECIAL
            # ESPECIAL: SOMENTE quando líquido estiver "zerado" (<= limiar_zero) e ATIVO
            # --------
            regra_aplicada = "PADRAO: bruto_planilha - liquido_pdf"
            diferenca = None
            valor_a_pagar = None

            if bruto_planilha is not None and liquido_pdf is not None:
                gatilho_especial = (liquido_pdf <= float(limiar_zero))

                if gatilho_especial and (is_ativo is True):
                    regra_aplicada = "ESPECIAL: bruto_planilha - verba8781 - verba981 (liq ~0 & ATIVO)"
                    if v8781 is None or v981 is None:
                        status = "REVISAR" if status == "OK" else status
                        notas.append("regra especial acionada, mas 8781/981 não foram extraídas (verificar bloco/PDF).")
                        # fallback seguro: usa regra padrão
                        diferenca = float(bruto_planilha) - float(liquido_pdf)
                        valor_a_pagar = max(diferenca, 0.0)
                    else:
                        diferenca = float(bruto_planilha) - float(v8781) - float(v981)
                        valor_a_pagar = max(diferenca, 0.0)
                else:
                    diferenca = float(bruto_planilha) - float(liquido_pdf)
                    valor_a_pagar = max(diferenca, 0.0)

                if diferenca is not None and diferenca < 0:
                    status = "INCONSISTENTE"
                    notas.append("diferença negativa (verifique dados).")

            # gate de revisão por confiança do parser
            if getattr(c, "confidence", None) and c.confidence.liquido < 0.85:
                status = "REVISAR" if status == "OK" else status
                notas.append("confidence do líquido baixa")

            row = {
                "competencia_global": extracao.competencia_global,
                "competencia": competencia,

                "matricula": c.matricula,
                "nome": c.nome,
                "cpf": c.cpf,

                "is_ativo": is_ativo,

                "cargo_pdf": c.cargo_pdf,
                "familia": familia,
                "nivel": nivel,
                "cargo_final": cargo_plano,

                # campo esperado pelo recibo
                "liquido_folha": liquido_pdf,

                "salario_real_bruto_planilha": bruto_planilha,

                "verba_8781_salario_contratual": v8781,
                "verba_981_desc_adiantamento": v981,
                "evidencia_verbas_pdf": ev_verbas,

                "regra_aplicada": regra_aplicada,
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

    out_xlsx = Path(st.session_state["workdir"]) / "demonstrativo_complemento.xlsx"
    out_conf_xlsx = Path(st.session_state["workdir"]) / "relatorio_conferencia.xlsx"
    out_receipts_dir = Path(st.session_state["workdir"]) / "recibos"
    out_zip = Path(st.session_state["workdir"]) / "recibos_pdf.zip"

    colA, colB, colC = st.columns(3)

    with colA:
        if st.button("Gerar Excel (Consolidado)"):
            export_consolidado_xlsx(
                st.session_state["final_rows"],
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
                r for r in st.session_state["final_rows"]
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
