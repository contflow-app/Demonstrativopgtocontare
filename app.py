\
from __future__ import annotations
import os
import io
import zipfile
from pathlib import Path
import streamlit as st
import pandas as pd

from src.parsing_pdf import parse_pdf_with_fallback
from src.matching import load_salario_real_xlsx, find_salario_real
from src.cargos import infer_familia, nivel_por_salario, cargo_final
from src.export_xlsx import export_consolidado_xlsx
from src.receipts_pdf import generate_all_receipts

APP_TITLE = "Demonstrativo de Pagamento Contare"
LOGO_PATH = str(Path(__file__).parent / "assets" / "logo.png")

st.set_page_config(page_title=APP_TITLE, layout="wide")

# Header
col1, col2 = st.columns([1, 4])
with col1:
    if Path(LOGO_PATH).exists():
        st.image(LOGO_PATH, width=180)
with col2:
    st.title(APP_TITLE)
    st.caption("PDF (Extrato Mensal) + Planilha de salário real → Excel consolidado + recibos PDF (ZIP).")

st.divider()

# Sidebar settings
st.sidebar.header("Configurações")
use_gpt = st.sidebar.toggle("Usar GPT como fallback (se campos críticos faltarem)", value=False)
openai_model = st.sidebar.text_input("Modelo OpenAI", value=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
empresa_nome = st.sidebar.text_input("Nome da empresa no recibo", value="Contare")

base_calc = st.sidebar.selectbox(
    "Base para cálculo do complemento",
    options=["Salário CLT (contratual)", "Bruto total (se disponível)", "Líquido (para conferência)"],
    index=0
)

st.sidebar.markdown("---")
st.sidebar.caption("Dica: se ativar GPT, defina OPENAI_API_KEY no ambiente.")

# Uploads
pdf_file = st.file_uploader("1) Suba o PDF do Extrato Mensal", type=["pdf"])
xlsx_file = st.file_uploader("2) Suba a planilha de salário real (XLSX)", type=["xlsx"])

if not pdf_file or not xlsx_file:
    st.info("Envie os dois arquivos para processar.")
    st.stop()

# Save uploads to temp paths
workdir = Path(st.session_state.get("workdir", Path.cwd() / ".tmp_streamlit"))
workdir.mkdir(parents=True, exist_ok=True)
st.session_state["workdir"] = str(workdir)

pdf_path = workdir / "extrato.pdf"
xlsx_path = workdir / "salario_real.xlsx"
pdf_path.write_bytes(pdf_file.getbuffer())
xlsx_path.write_bytes(xlsx_file.getbuffer())

# Process button
if st.button("Processar", type="primary"):
    with st.spinner("Extraindo dados do PDF..."):
        extracao = parse_pdf_with_fallback(str(pdf_path), use_gpt_fallback=use_gpt, openai_model=openai_model)

    with st.spinner("Lendo planilha e cruzando informações..."):
        df_sal = load_salario_real_xlsx(str(xlsx_path))

        final_rows = []
        pendencias = []

        for c in extracao.colaboradores:
            salario_real, match_status = find_salario_real(df_sal, c.cpf, c.nome)

            familia = infer_familia(c.cargo_pdf)
            nivel = nivel_por_salario(salario_real) if salario_real is not None else None
            cargo_plano = cargo_final(nivel, familia)

            # Base selection
            if base_calc.startswith("Salário CLT"):
                base = c.salario_clt
            elif base_calc.startswith("Bruto total"):
                base = c.bruto_total
            else:
                base = c.liquido

            complemento = None
            status = "OK"
            notas = []

            if c.cpf is None or c.salario_clt is None or c.liquido is None:
                status = "PENDENTE"
                notas.extend(c.warnings or [])
            if salario_real is None:
                status = "PENDENTE" if status == "OK" else status
                notas.append(f"salário real não encontrado (match: {match_status})")

            if salario_real is not None and base is not None:
                complemento = float(salario_real) - float(base)
                if complemento < 0:
                    status = "INCONSISTENTE"
                    notas.append("complemento negativo (verifique base e planilha)")

            # Review gates
            if getattr(c, "confidence", None):
                if c.confidence.liquido < 0.85:
                    status = "REVISAR" if status == "OK" else status
                    notas.append("confidence do líquido baixa")

            row = {
                "competencia_global": extracao.competencia_global,
                "competencia": c.competencia or extracao.competencia_global,
                "matricula": c.matricula,
                "nome": c.nome,
                "cpf": c.cpf,
                "cargo_pdf": c.cargo_pdf,
                "familia": familia,
                "nivel": nivel,
                "cargo_final": cargo_plano,
                "salario_clt": c.salario_clt,
                "liquido": c.liquido,
                "bruto_total": c.bruto_total,
                "salario_real_bruto": salario_real,
                "base_calculo": base,
                "complemento": complemento,
                "status": status,
                "notas": "; ".join(notas) if notas else "",
                "match_salario_real": match_status,
            }

            final_rows.append(row)

            if status != "OK":
                pendencias.append(row)

        st.session_state["final_rows"] = final_rows
        st.session_state["pendencias"] = pendencias

# Display results if available
final_rows = st.session_state.get("final_rows")
if final_rows:
    df = pd.DataFrame(final_rows)
    st.subheader("Prévia (consolidado)")
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Pendências / Revisar")
    dfp = pd.DataFrame(st.session_state.get("pendencias", []))
    st.dataframe(dfp, use_container_width=True, hide_index=True)

    # Export buttons
    out_xlsx = workdir / "demonstrativo_complemento.xlsx"
    out_receipts_dir = workdir / "recibos"
    out_zip = workdir / "recibos_pdf.zip"

    colA, colB = st.columns(2)

    with colA:
        if st.button("Gerar Excel"):
            export_consolidado_xlsx(final_rows, str(out_xlsx), logo_path=LOGO_PATH if Path(LOGO_PATH).exists() else None)
            st.success("Excel gerado.")
            st.download_button(
                "Baixar Excel",
                data=out_xlsx.read_bytes(),
                file_name="demonstrativo_complemento.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    with colB:
        if st.button("Gerar ZIP de Recibos PDF"):
            out_receipts_dir.mkdir(parents=True, exist_ok=True)
            # Only generate for OK or REVISAR (optional); here we generate for everything with complemento not None
            eligible = [r for r in final_rows if r.get("complemento") is not None and r.get("cpf")]
            pdfs = generate_all_receipts(eligible, str(out_receipts_dir), logo_path=LOGO_PATH if Path(LOGO_PATH).exists() else None, empresa_nome=empresa_nome)

            # Zip
            with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
                for p in pdfs:
                    z.write(p, arcname=Path(p).name)

            st.success(f"ZIP gerado ({len(pdfs)} recibos).")
            st.download_button(
                "Baixar ZIP de Recibos",
                data=out_zip.read_bytes(),
                file_name="recibos_pdf.zip",
                mime="application/zip"
            )
