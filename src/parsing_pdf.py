\
from __future__ import annotations
import re
from typing import List, Optional, Tuple
import pdfplumber

from .models import ExtracaoPDF, ColaboradorExtraido
from .utils import parse_brl_money

# --- Regex helpers (tuned for "Extrato Mensal") ---
RE_BLOCK_START = re.compile(r"\bEmpr\.\:\s*", re.IGNORECASE)

RE_MATRICULA = re.compile(r"Empr\.\:\s*([0-9]+)")
RE_CPF = re.compile(r"CPF\:\s*([0-9\.\-]+)")
RE_CARGO = re.compile(r"Cargo\:\s*\d+\s+(.*?)\s+C\.B\.O", re.IGNORECASE)
RE_SALARIO = re.compile(r"Sal[aá]rio\:\s*([0-9\.\,\s]+)")
RE_LIQUIDO = re.compile(r"L[ií]quido\:\s*.*?([0-9\.\,\s]+)")
# brute total patterns (optional)
RE_BRUTO = re.compile(r"(Total\s+Bruto|Bruto|Total\s+Proventos)\s*\:?\s*([0-9\.\,\s]+)", re.IGNORECASE)

RE_COMP_GLOBAL = re.compile(r"\b(0[1-9]|1[0-2])\/(20\d{2})\b")

def extract_text_from_pdf(pdf_path: str) -> str:
    parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            parts.append(t)
    return "\n".join(parts)

def split_blocks(text: str) -> List[str]:
    # split by "Empr.:", keep delimiter by re-splitting with capture not needed
    idxs = [m.start() for m in RE_BLOCK_START.finditer(text)]
    if not idxs:
        return []
    blocks = []
    for i, start in enumerate(idxs):
        end = idxs[i+1] if i+1 < len(idxs) else len(text)
        blocks.append(text[start:end])
    return blocks

def parse_block(block: str) -> ColaboradorExtraido:
    c = ColaboradorExtraido()
    # matricula
    m = RE_MATRICULA.search(block)
    if m:
        c.matricula = m.group(1)

    # name: usually after matricula on first line, naive capture
    # Example: "Empr.: 4000  NOME COMPLETO ... CPF:"
    first_line = block.splitlines()[0] if block.splitlines() else block[:120]
    # remove leading "Empr.: ####"
    name_guess = re.sub(r"Empr\.\:\s*\d+\s*", "", first_line).strip()
    # cut at common tokens
    name_guess = re.split(r"\s+CPF\:", name_guess)[0].strip()
    if name_guess:
        c.nome = name_guess

    # cpf
    cpf_all = RE_CPF.findall(block)
    if len(cpf_all) == 1:
        c.cpf = cpf_all[0]
        c.evidence.cpf = f"CPF: {cpf_all[0]}"
        c.confidence.cpf = 0.95
    elif len(cpf_all) == 0:
        c.warnings.append("cpf ausente no texto do bloco")
        c.confidence.cpf = 0.2
    else:
        c.warnings.append("cpf ambíguo: múltiplos candidatos")
        c.confidence.cpf = 0.2

    # cargo
    cm = RE_CARGO.search(block)
    if cm:
        c.cargo_pdf = cm.group(1).strip()

    # salario_clt
    sal_all = RE_SALARIO.findall(block)
    if len(sal_all) >= 1:
        # if multiple, try first; mark warning
        if len(sal_all) > 1:
            c.warnings.append("salário encontrado mais de uma vez; usando o primeiro candidato")
        val = parse_brl_money(sal_all[0])
        c.salario_clt = val
        c.evidence.salario_clt = f"Salário: {sal_all[0].strip()}"
        c.confidence.salario_clt = 0.9 if val is not None else 0.3
    else:
        c.warnings.append("salário_clt ausente")
        c.confidence.salario_clt = 0.2

    # liquido
    liq_all = RE_LIQUIDO.findall(block)
    if len(liq_all) >= 1:
        if len(liq_all) > 1:
            c.warnings.append("líquido encontrado mais de uma vez; usando o primeiro candidato")
        val = parse_brl_money(liq_all[0])
        c.liquido = val
        c.evidence.liquido = f"Líquido: {liq_all[0].strip()}"
        c.confidence.liquido = 0.9 if val is not None else 0.3
    else:
        c.warnings.append("líquido ausente")
        c.confidence.liquido = 0.2

    # bruto_total (optional)
    bm = RE_BRUTO.search(block)
    if bm:
        bruto_str = bm.group(2)
        val = parse_brl_money(bruto_str)
        c.bruto_total = val
        c.evidence.bruto_total = f"{bm.group(1)}: {bruto_str.strip()}"

    # competencia (best-effort inside block)
    comp = RE_COMP_GLOBAL.findall(block)
    if comp:
        mm, yyyy = comp[0]
        c.competencia = f"{mm}/{yyyy}"

    return c

def extract_competencia_global(text: str) -> Optional[str]:
    m = RE_COMP_GLOBAL.search(text)
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"

def parse_pdf_regex(pdf_path: str) -> ExtracaoPDF:
    text = extract_text_from_pdf(pdf_path)
    out = ExtracaoPDF(competencia_global=extract_competencia_global(text))
    blocks = split_blocks(text)
    for b in blocks:
        out.colaboradores.append(parse_block(b))
    return out

# --- GPT fallback (optional) ---
def gpt_extract_blocks(blocks: List[str], openai_model: str = "gpt-4.1-mini") -> ExtracaoPDF:
    """
    Calls OpenAI only for the provided blocks. Requires OPENAI_API_KEY in env.
    This is a minimal implementation; you can refine prompt/template in templates/.
    """
    import os
    from openai import OpenAI
    from .models import ExtracaoPDF  # avoid circular in some environments

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY não definido.")

    client = OpenAI(api_key=api_key)

    # Build prompt
    system = (
        "Você é um extrator de dados de folha/holerite a partir de TEXTO bruto. "
        "Extraia somente o que estiver explícito. Nunca invente. "
        "Se ambíguo, retorne null e registre warning."
    )

    user = "Extraia dados por colaborador para os blocos a seguir.\n\n" + "\n\n---\n\n".join(blocks)

    # NOTE: Here we use JSON mode via response_format for structured output (OpenAI SDK).
    schema = {
      "type": "object",
      "properties": {
        "competencia_global": {"type":["string","null"]},
        "colaboradores": {
          "type":"array",
          "items":{
            "type":"object",
            "properties":{
              "competencia":{"type":["string","null"]},
              "matricula":{"type":["string","null"]},
              "nome":{"type":["string","null"]},
              "cpf":{"type":["string","null"]},
              "cargo_pdf":{"type":["string","null"]},
              "salario_clt":{"type":["number","null"]},
              "liquido":{"type":["number","null"]},
              "bruto_total":{"type":["number","null"]},
              "evidence":{
                "type":"object",
                "properties":{
                  "cpf":{"type":["string","null"]},
                  "salario_clt":{"type":["string","null"]},
                  "liquido":{"type":["string","null"]},
                  "bruto_total":{"type":["string","null"]}
                },
                "required":["cpf","salario_clt","liquido","bruto_total"]
              },
              "confidence":{
                "type":"object",
                "properties":{
                  "cpf":{"type":"number"},
                  "salario_clt":{"type":"number"},
                  "liquido":{"type":"number"}
                },
                "required":["cpf","salario_clt","liquido"]
              },
              "warnings":{"type":"array","items":{"type":"string"}}
            },
            "required":[
              "competencia","matricula","nome","cpf","cargo_pdf",
              "salario_clt","liquido","bruto_total","evidence","confidence","warnings"
            ]
          }
        }
      },
      "required":["competencia_global","colaboradores"]
    }

    resp = client.responses.create(
        model=openai_model,
        input=[
            {"role":"system","content": system},
            {"role":"user","content": user},
        ],
        response_format={"type":"json_schema","json_schema":{"name":"extracao_folha","schema":schema}}
    )

    data = resp.output_parsed
    return ExtracaoPDF.model_validate(data)

def parse_pdf_with_fallback(pdf_path: str, use_gpt_fallback: bool, openai_model: str) -> ExtracaoPDF:
    base = parse_pdf_regex(pdf_path)

    if not use_gpt_fallback:
        return base

    # Identify problematic blocks by re-extracting and splitting again
    text = extract_text_from_pdf(pdf_path)
    blocks = split_blocks(text)

    # pick blocks where critical field missing
    bad_blocks = []
    for b, c in zip(blocks, base.colaboradores):
        if c.cpf is None or c.salario_clt is None or c.liquido is None:
            bad_blocks.append(b)

    if not bad_blocks:
        return base

    gpt = gpt_extract_blocks(bad_blocks, openai_model=openai_model)

    # Merge: replace only problematic records (by cpf if possible, else by order)
    # Simple strategy: overwrite in order of appearance for bad ones
    j = 0
    for i, c in enumerate(base.colaboradores):
        if c.cpf is None or c.salario_clt is None or c.liquido is None:
            if j < len(gpt.colaboradores):
                base.colaboradores[i] = gpt.colaboradores[j]
                j += 1

    return base
