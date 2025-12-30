\
from __future__ import annotations
from typing import Optional, List, Dict
from pydantic import BaseModel, Field

class Evidence(BaseModel):
    cpf: Optional[str] = None
    salario_clt: Optional[str] = None
    liquido: Optional[str] = None
    bruto_total: Optional[str] = None

class Confidence(BaseModel):
    cpf: float = 0.0
    salario_clt: float = 0.0
    liquido: float = 0.0

class ColaboradorExtraido(BaseModel):
    competencia: Optional[str] = None  # MM/AAAA
    matricula: Optional[str] = None
    nome: Optional[str] = None
    cpf: Optional[str] = None
    cargo_pdf: Optional[str] = None
    salario_clt: Optional[float] = None
    liquido: Optional[float] = None
    bruto_total: Optional[float] = None
    evidence: Evidence = Field(default_factory=Evidence)
    confidence: Confidence = Field(default_factory=Confidence)
    warnings: List[str] = Field(default_factory=list)

class ExtracaoPDF(BaseModel):
    competencia_global: Optional[str] = None
    colaboradores: List[ColaboradorExtraido] = Field(default_factory=list)

class ColaboradorFinal(BaseModel):
    competencia: Optional[str] = None
    matricula: Optional[str] = None
    nome: Optional[str] = None
    cpf: Optional[str] = None
    familia: Optional[str] = None
    nivel: Optional[str] = None
    cargo_final: Optional[str] = None
    cargo_pdf: Optional[str] = None

    salario_clt: Optional[float] = None
    liquido: Optional[float] = None
    bruto_total: Optional[float] = None

    salario_real_bruto: Optional[float] = None
    base_calculo: Optional[float] = None
    complemento: Optional[float] = None

    status: str = "OK"          # OK | REVISAR | PENDENTE | INCONSISTENTE
    notas: List[str] = Field(default_factory=list)
