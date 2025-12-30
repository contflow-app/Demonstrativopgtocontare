# Demonstrativo de Pagamento Contare (Streamlit)

App para:
- Ler PDF do **Extrato Mensal** da folha
- Extrair por colaborador: matrícula, nome, CPF, salário CLT, líquido (e bruto/total se existir)
- Cruzar com planilha de **salário real** (bruto) e calcular **complemento**
- Atribuir **cargo final** por faixa salarial + família (Fiscal/Contábil/DP)
- Gerar:
  - Excel consolidado
  - ZIP com recibos PDF individuais (com logo)

> ⚠️ LGPD/segurança: não faça commit de PDFs/planilhas reais.

## Rodar local

```bash
python -m venv .venv
source .venv/bin/activate  # windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Variáveis de ambiente (opcional GPT fallback)

- `OPENAI_API_KEY` (se você quiser usar o fallback com GPT na extração)
- `OPENAI_MODEL` (default: `gpt-4.1-mini`)

## Como funciona

1) Tenta extrair com `pdfplumber + regex`.
2) Se faltar campo crítico (CPF, salário CLT ou líquido) chama GPT **apenas** para os blocos problemáticos (se configurado).

## Estrutura

- `app.py` UI Streamlit
- `src/parsing_pdf.py` extração do PDF (regex + fallback GPT)
- `src/matching.py` match com planilha (CPF e fallback por nome)
- `src/cargos.py` família + nível por faixa salarial
- `src/export_xlsx.py` Excel consolidado
- `src/receipts_pdf.py` recibos PDF (ReportLab) com logo
- `templates/` schemas e configs
