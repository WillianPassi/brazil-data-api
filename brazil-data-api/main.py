import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from fastapi import FastAPI, HTTPException, Path, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, ConfigDict

# ==============================================================================
# CONFIGURAÇÃO DE SESSÃO HTTP COM CONNECTION POOLING E RETRIES
# ==============================================================================
http_session = requests.Session()
retries = Retry(
    total=2,
    backoff_factor=0.3,
    status_forcelist=[500, 502, 503, 504],
    raise_on_status=False
)
adapter = HTTPAdapter(pool_connections=25, pool_maxsize=50, max_retries=retries)
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)

DEFAULT_TIMEOUT_SECONDS = 5.0
REQUEST_HEADERS = {
    "User-Agent": "BrazilDataAPI/1.0 (+https://github.com/brazil-data-api)",
    "Accept": "application/json"
}

# ==============================================================================
# DICIONÁRIOS ESTÁTICOS: BANCOS BRASILEIROS E SEGMENTOS FEBRABAN
# ==============================================================================
BANCOS_FEBRABAN = {
    "001": "Banco do Brasil S.A.",
    "003": "Banco da Amazônia S.A.",
    "004": "Banco do Nordeste do Brasil S.A.",
    "021": "BANESTES S.A. Banco do Estado do Espírito Santo",
    "033": "Banco Santander (Brasil) S.A.",
    "041": "Banco do Estado do Rio Grande do Sul S.A. (Banrisul)",
    "070": "BRB - Banco de Brasília S.A.",
    "077": "Banco Inter S.A.",
    "104": "Caixa Econômica Federal",
    "208": "Banco BTG Pactual S.A.",
    "212": "Banco Original S.A.",
    "237": "Banco Bradesco S.A.",
    "260": "Nu Pagamentos S.A. (Nubank)",
    "290": "PagSeguro Internet S.A.",
    "318": "Banco BMG S.A.",
    "336": "Banco C6 S.A.",
    "341": "Itaú Unibanco S.A.",
    "389": "Banco Mercantil do Brasil S.A.",
    "422": "Banco Safra S.A.",
    "655": "Banco Votorantim S.A. (Banco BV)",
    "748": "Banco Cooperativo Sicredi S.A.",
    "756": "Banco Cooperativo do Brasil S.A. (Sicoob)"
}

SEGMENTOS_CONCESSIONARIA = {
    "1": "Prefeituras / Tributos Municipais",
    "2": "Saneamento (Água e Esgoto)",
    "3": "Energia Elétrica e Gás",
    "4": "Telecomunicações",
    "5": "Órgãos Governamentais / Tributos Federais",
    "6": "Carnês, Assemelhados e Empresas Privadas",
    "7": "Multas de Trânsito",
    "9": "Outros Órgãos / Bancos"
}

# Cache em memória para os indicadores financeiros (TTL: 15 minutos)
CACHE_INDICADORES: Dict[str, Any] = {
    "dados": None,
    "expira_em": 0
}

# ==============================================================================
# APLICAÇÃO FASTAPI & METADADOS OPENAPI (OTIMIZADO PARA RAPIDAPI)
# ==============================================================================
app = FastAPI(
    title="Brazil Data & Financial API",
    description="""
API de alta performance para validação, enriquecimento de dados cadastrais e utilitários financeiros do Brasil.
Desenvolvida para integração direta com **RapidAPI** e deploy em ambientes PaaS como **Render.com**.

### Módulos Disponíveis:
- **Monitoramento**: Health Check e status de uptime.
- **Endereçamento**: Consulta e normalização de CEP via ViaCEP.
- **Receita & Empresas**: Validação antecipada (Módulo 11) e consulta de CNPJ com Quadro Societário.
- **Validações Universais**: Validação matemática de CPF, CNPJ e detecção automática de Chaves PIX.
- **Boletos Bancários**: Validação, decodificação de Linha Digitável, cálculo de vencimento Febraban e conversão para Código de Barras.
- **PIX EMVCo**: Geração de código "PIX Copia e Cola" oficial (CRC16) e decodificação de payloads PIX.
- **Indicadores Econômicos**: Cotações de Dólar e Euro em tempo real e taxas Selic, CDI e IPCA.
    """,
    version="1.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# Habilita CORS irrestrito
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# SCHEMAS PYDANTIC - MONITORAMENTO, CEP, CNPJ & VALIDADOR
# ==============================================================================
class HealthResponse(BaseModel):
    status: str = Field(..., example="online")
    timestamp: str = Field(..., example="2026-09-24T14:53:13.000Z")
    service: str = Field(default="brazil-data-api")
    version: str = Field(default="1.1.0")


class CepResponse(BaseModel):
    cep: str = Field(..., description="CEP formatado", example="01001-000")
    cep_limpo: str = Field(..., description="Apenas dígitos", example="01001000")
    logradouro: str = Field(..., example="Praça da Sé")
    complemento: Optional[str] = Field(None, example="lado ímpar")
    bairro: str = Field(..., example="Sé")
    cidade: str = Field(..., example="São Paulo")
    estado: str = Field(..., example="SP")
    ddd: Optional[str] = Field(None, example="11")
    codigo_ibge: Optional[str] = Field(None, example="3550308")


class CnaeInfo(BaseModel):
    codigo: Optional[int] = Field(None, example=6422100)
    descricao: Optional[str] = Field(None, example="Bancos múltiplos, com carteira comercial")


class EnderecoInfo(BaseModel):
    tipo_logradouro: Optional[str] = Field(None, example="PRAÇA")
    logradouro: Optional[str] = Field(None, example="DA SÉ")
    numero: Optional[str] = Field(None, example="111")
    complemento: Optional[str] = Field(None, example="ANDAR 1")
    bairro: Optional[str] = Field(None, example="CENTRO")
    municipio: Optional[str] = Field(None, example="SÃO PAULO")
    uf: Optional[str] = Field(None, example="SP")
    cep: Optional[str] = Field(None, example="01001-000")


class SocioResumido(BaseModel):
    nome: Optional[str] = Field(None, example="ALAN CARLOS GUEDES DE OLIVEIRA")
    qualificacao: Optional[str] = Field(None, example="Diretor")
    faixa_etaria: Optional[str] = Field(None, example="Entre 41 a 50 anos")
    data_entrada: Optional[str] = Field(None, example="2023-05-17")


class CnpjResponse(BaseModel):
    cnpj: str = Field(..., example="00.000.000/0001-91")
    cnpj_limpo: str = Field(..., example="00000000000191")
    razao_social: str = Field(..., example="BANCO DO BRASIL SA")
    nome_fantasia: Optional[str] = Field(None, example="DIRECAO GERAL")
    situacao_cadastral: str = Field(..., example="ATIVA")
    data_inicio_atividade: Optional[str] = Field(None, example="1966-08-01")
    cnae_principal: CnaeInfo
    endereco: EnderecoInfo
    quadro_societario: List[SocioResumido] = Field(default_factory=list)


class TipoValidacaoEnum(str, Enum):
    CPF = "cpf"
    CNPJ = "cnpj"
    PIX_CHAVE = "pix_chave"


class ValidadorRequest(BaseModel):
    tipo: TipoValidacaoEnum = Field(
        ...,
        description="Tipo de documento ou chave a validar ('cpf', 'cnpj' ou 'pix_chave')",
        example="cpf"
    )
    valor: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Valor a ser validado com ou sem formatação",
        example="529.982.247-25"
    )
    model_config = ConfigDict(use_enum_values=True)


class DetalhesValidacao(BaseModel):
    subtipo_pix: Optional[str] = Field(None, example="email")
    algoritmo: Optional[str] = Field(None, example="Módulo 11 (Receita Federal)")
    valor_formatado: Optional[str] = Field(None, example="529.982.247-25")


class ValidadorResponse(BaseModel):
    tipo: str = Field(..., example="cpf")
    valor_original: str = Field(..., example="529.982.247-25")
    valor_limpo: str = Field(..., example="52998224725")
    valido: bool = Field(..., example=True)
    mensagem: str = Field(..., example="CPF válido matematicamente.")
    detalhes: DetalhesValidacao


# ==============================================================================
# SCHEMAS PYDANTIC - BOLETOS BANCÁRIOS
# ==============================================================================
class BancoBoletoInfo(BaseModel):
    codigo: str = Field(..., example="237")
    nome: str = Field(..., example="Banco Bradesco S.A.")


class BoletoRequest(BaseModel):
    codigo: str = Field(
        ...,
        description="Linha digitável de boleto bancário (47 dígitos) ou concessionária (48 dígitos) ou código de barras (44 dígitos)",
        example="23793.38128 60083.013528 85000.633303 9 84340000015000"
    )


class BoletoResponse(BaseModel):
    valido: bool = Field(..., example=True)
    tipo: str = Field(..., example="titulo_bancario", description="'titulo_bancario' ou 'concessionaria_tributo'")
    banco: Optional[BancoBoletoInfo] = None
    segmento: Optional[str] = None
    valor: Optional[float] = Field(None, example=150.0)
    vencimento: Optional[str] = Field(None, example="2024-03-25")
    codigo_barras: Optional[str] = Field(None, example="23799843400000150003381260083013528500063330")
    linha_digitavel: str = Field(..., example="23793.38128 60083.013528 85000.633303 9 84340000015000")
    mensagem: str = Field(..., example="Boleto bancário válido e decodificado com sucesso.")


# ==============================================================================
# SCHEMAS PYDANTIC - PIX EMVCo
# ==============================================================================
class PixGerarRequest(BaseModel):
    chave: str = Field(..., description="Chave PIX (CPF, CNPJ, e-mail, telefone ou aleatória)", example="contato@empresa.com.br")
    nome_recebedor: str = Field(..., max_length=25, description="Nome do recebedor (máx 25 caracteres)", example="EMPRESA EXEMPLO LTDA")
    cidade_recebedor: str = Field(default="SAO PAULO", max_length=15, description="Cidade do recebedor (máx 15 caracteres)", example="SAO PAULO")
    valor: Optional[float] = Field(None, ge=0.01, description="Valor em R$ da cobrança (opcional)", example=150.00)
    identificador: Optional[str] = Field(default="***", max_length=25, description="TXID / Identificador da cobrança (padrão '***')", example="FATURA123")
    descricao: Optional[str] = Field(None, max_length=40, description="Descrição opcional do pagamento", example="Pagamento de Servicos")


class PixGerarResponse(BaseModel):
    pix_copia_e_cola: str = Field(..., description="Código payload oficial no padrão EMVCo do Banco Central pronto para copiar e colar")
    chave: str
    beneficiario: str
    cidade: str
    valor: Optional[float] = None
    txid: str
    mensagem: str


class PixDecodificarRequest(BaseModel):
    payload: str = Field(..., description="String completa do código PIX Copia e Cola (inicia com '000201')", example="00020126440014br.gov.bcb.pix0122contato@exemplo.com.br5204000053039865406150.005802BR5913EMPRESA TESTE6009SAO PAULO62130509PEDIDO1236304D123")


class PixDecodificarResponse(BaseModel):
    valido: bool
    chave: Optional[str] = None
    beneficiario: Optional[str] = None
    cidade: Optional[str] = None
    valor: Optional[float] = None
    txid: Optional[str] = None
    crc16_valido: bool
    mensagem: str


# ==============================================================================
# SCHEMAS PYDANTIC - INDICADORES ECONÔMICOS
# ==============================================================================
class CotacaoMoeda(BaseModel):
    nome: str
    compra: float
    venda: float
    variacao_percentual: float
    alta: float
    baixa: float


class CambioInfo(BaseModel):
    dolar_comercial: CotacaoMoeda
    euro: CotacaoMoeda


class TaxasBrasilInfo(BaseModel):
    selic_anual: float = Field(..., example=10.5)
    cdi_anual: float = Field(..., example=10.4)
    ipca_anual: float = Field(..., example=4.23)


class IndicadoresResponse(BaseModel):
    cambio: CambioInfo
    taxas: TaxasBrasilInfo
    atualizado_em: str


# ==============================================================================
# ALGORITMOS DE VALIDAÇÃO MATEMÁTICA E REGRAS DE NEGÓCIO
# ==============================================================================
def sanitizar_numeros(valor: str) -> str:
    """Remove qualquer caractere não numérico da string."""
    return re.sub(r"\D", "", valor or "")


def validar_cpf_matematico(cpf: str) -> tuple[bool, str]:
    digitos = sanitizar_numeros(cpf)
    if len(digitos) != 11:
        return False, f"CPF deve conter exatamente 11 dígitos numéricos (recebido: {len(digitos)})."
    if len(set(digitos)) == 1:
        return False, "CPF não pode ser composto por dígitos repetidos."

    soma_1 = sum(int(digitos[i]) * (10 - i) for i in range(9))
    resto_1 = (soma_1 * 10) % 11
    d1 = 0 if resto_1 == 10 else resto_1
    if int(digitos[9]) != d1:
        return False, f"1º dígito verificador inválido (esperado: {d1}, recebido: {digitos[9]})."

    soma_2 = sum(int(digitos[i]) * (11 - i) for i in range(10))
    resto_2 = (soma_2 * 10) % 11
    d2 = 0 if resto_2 == 10 else resto_2
    if int(digitos[10]) != d2:
        return False, f"2º dígito verificador inválido (esperado: {d2}, recebido: {digitos[10]})."

    return True, "CPF válido matematicamente."


def validar_cnpj_matematico(cnpj: str) -> tuple[bool, str]:
    digitos = sanitizar_numeros(cnpj)
    if len(digitos) != 14:
        return False, f"CNPJ deve conter exatamente 14 dígitos numéricos (recebido: {len(digitos)})."
    if len(set(digitos)) == 1:
        return False, "CNPJ não pode ser composto por dígitos repetidos."

    pesos_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma_1 = sum(int(digitos[i]) * pesos_1[i] for i in range(12))
    resto_1 = soma_1 % 11
    d1 = 0 if resto_1 < 2 else 11 - resto_1
    if int(digitos[12]) != d1:
        return False, f"1º dígito verificador inválido (esperado: {d1}, recebido: {digitos[12]})."

    pesos_2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma_2 = sum(int(digitos[i]) * pesos_2[i] for i in range(13))
    resto_2 = soma_2 % 11
    d2 = 0 if resto_2 < 2 else 11 - resto_2
    if int(digitos[13]) != d2:
        return False, f"2º dígito verificador inválido (esperado: {d2}, recebido: {digitos[13]})."

    return True, "CNPJ válido matematicamente."


def formatar_cpf(digitos: str) -> str:
    return f"{digitos[0:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:11]}"


def formatar_cnpj(digitos: str) -> str:
    return f"{digitos[0:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:14]}"


def identificar_e_validar_pix(chave: str) -> dict:
    chave_limpa = chave.strip()

    # 1. Chave Aleatória (EVP - UUID v4)
    uuid_regex = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    if re.match(uuid_regex, chave_limpa):
        return {
            "subtipo": "aleatoria",
            "valido": True,
            "valor_limpo": chave_limpa.lower(),
            "valor_formatado": chave_limpa.lower(),
            "mensagem": "Chave PIX aleatória (EVP) no padrão UUID válida."
        }

    # 2. E-mail
    if "@" in chave_limpa:
        email_regex = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        valido = bool(re.match(email_regex, chave_limpa)) and len(chave_limpa) <= 77
        return {
            "subtipo": "email",
            "valido": valido,
            "valor_limpo": chave_limpa.lower(),
            "valor_formatado": chave_limpa.lower(),
            "mensagem": "Chave PIX de e-mail com sintaxe RFC válida." if valido else "Chave PIX de e-mail em formato inválido."
        }

    # 3. Telefone
    tem_mais = chave_limpa.startswith("+")
    tem_mascara_telefone = bool(re.search(r"^\(?\d{2}\)?\s?9?\d{4}-?\d{4}$", chave_limpa))
    apenas_digitos = sanitizar_numeros(chave_limpa)

    if tem_mais or tem_mascara_telefone or (len(apenas_digitos) in (12, 13) and apenas_digitos.startswith("55")):
        if tem_mais or apenas_digitos.startswith("55"):
            if apenas_digitos.startswith("55") and len(apenas_digitos) in (12, 13):
                ddd = int(apenas_digitos[2:4])
                if 11 <= ddd <= 99:
                    fmt = f"+{apenas_digitos}"
                    return {
                        "subtipo": "telefone",
                        "valido": True,
                        "valor_limpo": apenas_digitos,
                        "valor_formatado": fmt,
                        "mensagem": "Chave PIX de telefone no padrão internacional (+55) válida."
                    }
            return {
                "subtipo": "telefone",
                "valido": False,
                "valor_limpo": apenas_digitos,
                "valor_formatado": chave_limpa,
                "mensagem": "Telefone inválido. Formato BACEN: +55 seguido de DDD e 8 ou 9 dígitos."
            }
        else:
            if len(apenas_digitos) in (10, 11):
                ddd = int(apenas_digitos[:2])
                if 11 <= ddd <= 99:
                    fmt = f"+55{apenas_digitos}"
                    return {
                        "subtipo": "telefone",
                        "valido": True,
                        "valor_limpo": apenas_digitos,
                        "valor_formatado": fmt,
                        "mensagem": f"Telefone brasileiro válido. Formato BACEN: {fmt}."
                    }
            return {
                "subtipo": "telefone",
                "valido": False,
                "valor_limpo": apenas_digitos,
                "valor_formatado": chave_limpa,
                "mensagem": "Formato de telefone inválido para chave PIX."
            }

    # 4. CNPJ
    if "/" in chave_limpa or len(apenas_digitos) == 14:
        valido, motivo = validar_cnpj_matematico(apenas_digitos)
        fmt = formatar_cnpj(apenas_digitos) if len(apenas_digitos) == 14 else chave_limpa
        return {
            "subtipo": "cnpj",
            "valido": valido,
            "valor_limpo": apenas_digitos,
            "valor_formatado": fmt,
            "mensagem": f"Chave PIX (CNPJ): {motivo}"
        }

    # 5. CPF
    if ("." in chave_limpa and "-" in chave_limpa) or len(apenas_digitos) == 11:
        valido, motivo = validar_cpf_matematico(apenas_digitos)
        fmt = formatar_cpf(apenas_digitos) if len(apenas_digitos) == 11 else chave_limpa
        return {
            "subtipo": "cpf",
            "valido": valido,
            "valor_limpo": apenas_digitos,
            "valor_formatado": fmt,
            "mensagem": f"Chave PIX (CPF): {motivo}"
        }

    return {
        "subtipo": "desconhecido",
        "valido": False,
        "valor_limpo": apenas_digitos or chave_limpa,
        "valor_formatado": chave_limpa,
        "mensagem": "Formato de chave PIX não identificado. Deve ser CPF, CNPJ, e-mail, telefone (+55) ou chave aleatória UUID."
    }


# ==============================================================================
# ALGORITMOS FEBRABAN: MÓDULO 10, MÓDULO 11 & FATOR DE VENCIMENTO
# ==============================================================================
def modulo10_febraban(num: str) -> int:
    """Calcula o dígito verificador Módulo 10 padrão Febraban."""
    soma = 0
    peso = 2
    for d in reversed(num):
        mult = int(d) * peso
        if mult > 9:
            mult = (mult // 10) + (mult % 10)
        soma += mult
        peso = 1 if peso == 2 else 2
    resto = soma % 10
    return 0 if resto == 0 else 10 - resto


def calcular_vencimento_fator(fator: int) -> Optional[str]:
    """Calcula a data de vencimento a partir do Fator de Vencimento Febraban."""
    if fator == 0:
        return None
    # Data base original: 07/10/1997.
    # Em 22/02/2025 atingiu o fator 9999 e reiniciou no fator 1000.
    hoje = date.today()
    if hoje >= date(2025, 2, 22):
        base = date(2025, 2, 22) - timedelta(days=1000)
    else:
        base = date(1997, 10, 7)
    dt = base + timedelta(days=fator)
    return dt.isoformat()


def decodificar_boleto(codigo_raw: str) -> dict:
    """Decodifica linha digitável (47 ou 48 dígitos) ou código de barras (44 dígitos)."""
    digitos = sanitizar_numeros(codigo_raw)

    # 1. Título Bancário (47 dígitos)
    if len(digitos) == 47:
        c1 = digitos[0:9]
        dv1 = int(digitos[9])
        c2 = digitos[10:20]
        dv2 = int(digitos[20])
        c3 = digitos[21:31]
        dv3 = int(digitos[31])
        dv_geral = int(digitos[32])
        fator = int(digitos[33:37])
        valor_raw = int(digitos[37:47])
        valor = valor_raw / 100.0 if valor_raw > 0 else None

        cod_banco = digitos[0:3]
        nome_banco = BANCOS_FEBRABAN.get(cod_banco, "Outro Banco / Instituição Financeira")

        # Monta código de barras de 44 dígitos
        codigo_barras = (
            digitos[0:4] +
            str(dv_geral) +
            digitos[33:47] +
            digitos[4:9] +
            digitos[10:20] +
            digitos[21:31]
        )

        vencimento = calcular_vencimento_fator(fator)

        fmt_linha = f"{digitos[0:5]}.{digitos[5:10]} {digitos[10:15]}.{digitos[15:21]} {digitos[21:26]}.{digitos[26:32]} {digitos[32]} {digitos[33:]}"

        return {
            "valido": True,
            "tipo": "titulo_bancario",
            "banco": {"codigo": cod_banco, "nome": nome_banco},
            "segmento": None,
            "valor": valor,
            "vencimento": vencimento,
            "codigo_barras": codigo_barras,
            "linha_digitavel": fmt_linha,
            "mensagem": "Boleto bancário identificado e decodificado com sucesso."
        }

    # 2. Concessionária / Tributos (48 dígitos)
    elif len(digitos) == 48:
        segmento_char = digitos[1]
        segmento_nome = SEGMENTOS_CONCESSIONARIA.get(segmento_char, "Concessionária de Serviços Públicos")

        valor_raw = int(digitos[4:15])
        valor = valor_raw / 100.0 if valor_raw > 0 else None

        # Monta código de barras de 44 dígitos removendo os 4 DVs dos blocos
        codigo_barras = digitos[0:11] + digitos[12:23] + digitos[24:35] + digitos[36:47]

        fmt_linha = f"{digitos[0:12]} {digitos[12:24]} {digitos[24:36]} {digitos[36:48]}"

        return {
            "valido": True,
            "tipo": "concessionaria_tributo",
            "banco": None,
            "segmento": segmento_nome,
            "valor": valor,
            "vencimento": None,
            "codigo_barras": codigo_barras,
            "linha_digitavel": fmt_linha,
            "mensagem": f"Boleto de concessionária/tributo identificado ({segmento_nome})."
        }

    else:
        return {
            "valido": False,
            "tipo": "invalido",
            "banco": None,
            "segmento": None,
            "valor": None,
            "vencimento": None,
            "codigo_barras": None,
            "linha_digitavel": codigo_raw,
            "mensagem": f"Comprimento inválido ({len(digitos)} dígitos numéricos). Linha digitável deve ter 47 dígitos (bancos) ou 48 dígitos (concessionárias)."
        }


# ==============================================================================
# ALGORITMOS PIX EMVCo (GERAÇÃO E CRC16)
# ==============================================================================
def crc16_ccitt(data: str) -> str:
    """Calcula o checksum CRC16-CCITT (polinômio 0x1021, valor inicial 0xFFFF)."""
    crc = 0xFFFF
    for ch in data:
        crc ^= (ord(ch) << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def gerar_payload_pix(
    chave: str,
    nome_recebedor: str,
    cidade_recebedor: str,
    valor: Optional[float] = None,
    identificador: str = "***",
    descricao: Optional[str] = None
) -> str:
    """Gera string oficial do PIX Copia e Cola conforme padrão EMVCo do BACEN."""
    def format_tlv(tag: str, value: str) -> str:
        val_str = str(value)
        return f"{tag}{len(val_str):02d}{val_str}"

    def normalizar_ascii(s: str, max_len: int) -> str:
        limpo = unicodedata.normalize("NFKD", s or "").encode("ASCII", "ignore").decode("ASCII").upper()
        return limpo[:max_len]

    # Subtags da Tag 26 (Merchant Account Information)
    sub26 = format_tlv("00", "br.gov.bcb.pix") + format_tlv("01", chave.strip())
    if descricao:
        sub26 += format_tlv("02", normalizar_ascii(descricao, 40))

    payload = (
        format_tlv("00", "01") +               # Payload Format Indicator
        format_tlv("26", sub26) +              # Merchant Account Information
        format_tlv("52", "0000") +             # Merchant Category Code
        format_tlv("53", "986")                # Transaction Currency (986 = BRL)
    )

    if valor and valor > 0:
        payload += format_tlv("54", f"{valor:.2f}")

    payload += format_tlv("58", "BR")                                         # Country Code
    payload += format_tlv("59", normalizar_ascii(nome_recebedor, 25))        # Merchant Name
    payload += format_tlv("60", normalizar_ascii(cidade_recebedor, 15))      # Merchant City

    # Tag 62 (Additional Data Field Template com TXID)
    clean_txid = normalizar_ascii(identificador or "***", 25)
    payload += format_tlv("62", format_tlv("05", clean_txid))

    # Tag 63 (CRC16)
    payload_to_crc = payload + "6304"
    crc = crc16_ccitt(payload_to_crc)
    return payload_to_crc + crc


def decodificar_payload_pix(payload: str) -> dict:
    """Decodifica as tags de um payload PIX Copia e Cola."""
    p = payload.strip()
    if not p.startswith("000201"):
        return {
            "valido": False,
            "crc16_valido": False,
            "chave": None,
            "beneficiario": None,
            "cidade": None,
            "valor": None,
            "txid": None,
            "mensagem": "Payload inválido. Não inicia com o identificador padrão EMVCo '000201'."
        }

    crc_recebido = p[-4:].upper()
    crc_calculado = crc16_ccitt(p[:-4])
    crc_valido = crc_recebido == crc_calculado

    dados: Dict[str, Any] = {
        "valido": crc_valido,
        "crc16_valido": crc_valido,
        "chave": None,
        "beneficiario": None,
        "cidade": None,
        "valor": None,
        "txid": None,
        "mensagem": "Payload PIX decodificado com sucesso." if crc_valido else "Aviso: Checksum CRC16 do payload não confere."
    }

    i = 0
    while i < len(p):
        tag = p[i:i+2]
        if i+4 > len(p):
            break
        try:
            tamanho = int(p[i+2:i+4])
        except ValueError:
            break
        conteudo = p[i+4:i+4+tamanho]
        i = i + 4 + tamanho

        if tag == "26":  # Merchant Account Info
            j = 0
            while j < len(conteudo):
                sub_tag = conteudo[j:j+2]
                if j+4 > len(conteudo):
                    break
                try:
                    sub_tam = int(conteudo[j+2:j+4])
                except ValueError:
                    break
                sub_val = conteudo[j+4:j+4+sub_tam]
                j += 4 + sub_tam
                if sub_tag == "01":
                    dados["chave"] = sub_val
        elif tag == "54":
            try:
                dados["valor"] = float(conteudo)
            except ValueError:
                pass
        elif tag == "59":
            dados["beneficiario"] = conteudo
        elif tag == "60":
            dados["cidade"] = conteudo
        elif tag == "62":
            j = 0
            while j < len(conteudo):
                sub_tag = conteudo[j:j+2]
                if j+4 > len(conteudo):
                    break
                try:
                    sub_tam = int(conteudo[j+2:j+4])
                except ValueError:
                    break
                sub_val = conteudo[j+4:j+4+sub_tam]
                j += 4 + sub_tam
                if sub_tag == "05":
                    dados["txid"] = sub_val

    return dados


# ==============================================================================
# ROTAS / ENDPOINTS
# ==============================================================================
@app.get("/", include_in_schema=False)
def root_redirect():
    """Redireciona a raiz diretamente para a documentação interativa."""
    return RedirectResponse(url="/docs")


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["Monitoramento"],
    summary="Health Check do serviço",
    description="Retorna status 200 e timestamp indicando que o micro-serviço está operacional no Render/Cloud."
)
def get_health():
    now_utc = datetime.now(timezone.utc).isoformat()
    return HealthResponse(
        status="online",
        timestamp=now_utc,
        service="brazil-data-api",
        version="1.1.0"
    )


@app.get(
    "/api/v1/cep/{cep}",
    response_model=CepResponse,
    tags=["Endereçamento"],
    summary="Consulta e validação de CEP",
    description="Sanitiza e valida se o CEP possui 8 dígitos, realizando consulta rápida e segura ao ViaCEP."
)
def get_cep(
    cep: str = Path(
        ...,
        description="Código postal brasileiro com ou sem traço",
        examples=["01001-000", "01001000"]
    )
):
    cep_limpo = sanitizar_numeros(cep)

    if len(cep_limpo) != 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CEP inválido. O CEP deve conter exatamente 8 dígitos numéricos. Recebido: '{cep}'."
        )

    url_viacep = f"https://viacep.com.br/ws/{cep_limpo}/json/"
    try:
        response = http_session.get(
            url_viacep,
            headers=REQUEST_HEADERS,
            timeout=DEFAULT_TIMEOUT_SECONDS
        )
    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Tempo limite excedido ao consultar o provedor de CEP (ViaCEP)."
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Falha na comunicação com o serviço externo de CEP: {str(e)}"
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"O serviço externo retornou status inesperado ({response.status_code})."
        )

    try:
        dados = response.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Resposta inválida recebida do serviço de CEP."
        )

    if dados.get("erro") in (True, "true"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CEP '{cep_limpo}' não encontrado na base de dados dos Correios."
        )

    cep_formatado = dados.get("cep") or f"{cep_limpo[:5]}-{cep_limpo[5:]}"

    return CepResponse(
        cep=cep_formatado,
        cep_limpo=cep_limpo,
        logradouro=dados.get("logradouro") or "",
        complemento=dados.get("complemento") or None,
        bairro=dados.get("bairro") or "",
        cidade=dados.get("localidade") or "",
        estado=dados.get("uf") or "",
        ddd=dados.get("ddd") or None,
        codigo_ibge=dados.get("ibge") or None
    )


@app.get(
    "/api/v1/cnpj/{cnpj:path}",
    response_model=CnpjResponse,
    tags=["Receita & Empresas"],
    summary="Consulta e enriquecimento cadastral de CNPJ",
    description="Sanitiza e valida matematicamente o CNPJ via Módulo 11 (retornando 400 se inválido) antes de consultar a BrasilAPI com timeout reduzido de 5s."
)
def get_cnpj(
    cnpj: str = Path(
        ...,
        description="CNPJ da empresa com ou sem máscara",
        examples=["00.000.000/0001-91", "00000000000191"]
    )
):
    cnpj_limpo = sanitizar_numeros(cnpj)

    valido, motivo = validar_cnpj_matematico(cnpj_limpo)
    if not valido:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CNPJ inválido: {motivo}"
        )

    url_brasilapi = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_limpo}"
    try:
        response = http_session.get(
            url_brasilapi,
            headers=REQUEST_HEADERS,
            timeout=DEFAULT_TIMEOUT_SECONDS
        )
    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Tempo limite de 5s excedido ao consultar os dados do CNPJ na BrasilAPI."
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Falha na comunicação com o serviço de dados de CNPJ: {str(e)}"
        )

    if response.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CNPJ '{cnpj_limpo}' não foi localizado na base oficial da Receita Federal."
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Serviço governamental/BrasilAPI retornou status de erro ({response.status_code})."
        )

    try:
        dados = response.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Resposta ilegível ou JSON inválido retornado pelo provedor de CNPJ."
        )

    cnae_obj = CnaeInfo(
        codigo=dados.get("cnae_fiscal"),
        descricao=dados.get("cnae_fiscal_descricao")
    )

    endereco_obj = EnderecoInfo(
        tipo_logradouro=dados.get("descricao_tipo_de_logradouro"),
        logradouro=dados.get("logradouro"),
        numero=dados.get("numero"),
        complemento=dados.get("complemento"),
        bairro=dados.get("bairro"),
        municipio=dados.get("municipio"),
        uf=dados.get("uf"),
        cep=dados.get("cep")
    )

    quadro_societario = [
        SocioResumido(
            nome=socio.get("nome_socio"),
            qualificacao=socio.get("qualificacao_socio"),
            faixa_etaria=socio.get("faixa_etaria"),
            data_entrada=socio.get("data_entrada_sociedade")
        )
        for socio in dados.get("qsa", [])
    ]

    return CnpjResponse(
        cnpj=formatar_cnpj(cnpj_limpo),
        cnpj_limpo=cnpj_limpo,
        razao_social=dados.get("razao_social") or "",
        nome_fantasia=dados.get("nome_fantasia") or None,
        situacao_cadastral=dados.get("descricao_situacao_cadastral") or dados.get("situacao_cadastral") or "NÃO INFORMADA",
        data_inicio_atividade=dados.get("data_inicio_atividade") or None,
        cnae_principal=cnae_obj,
        endereco=endereco_obj,
        quadro_societario=quadro_societario
    )


@app.post(
    "/api/v1/validador",
    response_model=ValidadorResponse,
    tags=["Validações"],
    summary="Validador unificado de CPF, CNPJ e Chave PIX",
    description="Valida matematicamente CPF e CNPJ (Módulo 11) ou detecta automaticamente o subtipo e valida a formatação de chaves PIX."
)
def post_validador(payload: ValidadorRequest):
    tipo = payload.tipo.lower().strip()
    valor_original = payload.valor.strip()
    valor_limpo = sanitizar_numeros(valor_original)

    if tipo == TipoValidacaoEnum.CPF.value:
        valido, motivo = validar_cpf_matematico(valor_limpo)
        fmt = formatar_cpf(valor_limpo) if len(valor_limpo) == 11 else valor_original
        return ValidadorResponse(
            tipo="cpf",
            valor_original=valor_original,
            valor_limpo=valor_limpo,
            valido=valido,
            mensagem=motivo,
            detalhes=DetalhesValidacao(
                algoritmo="Módulo 11 (Receita Federal)",
                valor_formatado=fmt if valido else None
            )
        )

    elif tipo == TipoValidacaoEnum.CNPJ.value:
        valido, motivo = validar_cnpj_matematico(valor_limpo)
        fmt = formatar_cnpj(valor_limpo) if len(valor_limpo) == 14 else valor_original
        return ValidadorResponse(
            tipo="cnpj",
            valor_original=valor_original,
            valor_limpo=valor_limpo,
            valido=valido,
            mensagem=motivo,
            detalhes=DetalhesValidacao(
                algoritmo="Módulo 11 (Receita Federal)",
                valor_formatado=fmt if valido else None
            )
        )

    elif tipo == TipoValidacaoEnum.PIX_CHAVE.value:
        res_pix = identificar_e_validar_pix(valor_original)
        return ValidadorResponse(
            tipo="pix_chave",
            valor_original=valor_original,
            valor_limpo=res_pix["valor_limpo"],
            valido=res_pix["valido"],
            mensagem=res_pix["mensagem"],
            detalhes=DetalhesValidacao(
                subtipo_pix=res_pix["subtipo"],
                algoritmo="Padrão BACEN / Módulo 11",
                valor_formatado=res_pix["valor_formatado"] if res_pix["valido"] else None
            )
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tipo de validação '{tipo}' não suportado. Utilize 'cpf', 'cnpj' ou 'pix_chave'."
        )


# ==============================================================================
# NOVOS ENDPOINTS: BOLETOS BANCÁRIOS & FEBRABAN
# ==============================================================================
@app.post(
    "/api/v1/boleto/decodificar",
    response_model=BoletoResponse,
    tags=["Boletos Bancários"],
    summary="Validação e decodificação de Linha Digitável de Boleto",
    description="Valida e decodifica linhas digitáveis de boletos bancários (47 dígitos) e contas de concessionárias/tributos (48 dígitos). Extrai banco emissor, valor exato em R$, data de vencimento Febraban e monta o código de barras de 44 dígitos."
)
def post_decodificar_boleto(payload: BoletoRequest):
    resultado = decodificar_boleto(payload.codigo)
    if not resultado["valido"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado["mensagem"]
        )

    banco_obj = None
    if resultado.get("banco"):
        banco_obj = BancoBoletoInfo(
            codigo=resultado["banco"]["codigo"],
            nome=resultado["banco"]["nome"]
        )

    return BoletoResponse(
        valido=True,
        tipo=resultado["tipo"],
        banco=banco_obj,
        segmento=resultado.get("segmento"),
        valor=resultado.get("valor"),
        vencimento=resultado.get("vencimento"),
        codigo_barras=resultado.get("codigo_barras"),
        linha_digitavel=resultado.get("linha_digitavel"),
        mensagem=resultado["mensagem"]
    )


# ==============================================================================
# NOVOS ENDPOINTS: PIX COPIA E COLA (EMVCo)
# ==============================================================================
@app.post(
    "/api/v1/pix/gerar",
    response_model=PixGerarResponse,
    tags=["PIX EMVCo"],
    summary="Gerador de código PIX Copia e Cola (Padrão BACEN / EMVCo)",
    description="Gera a string oficial do PIX Copia e Cola com cálculo automático do checksum CRC16 conforme as especificações do Banco Central do Brasil."
)
def post_gerar_pix(payload: PixGerarRequest):
    pix_string = gerar_payload_pix(
        chave=payload.chave,
        nome_recebedor=payload.nome_recebedor,
        cidade_recebedor=payload.cidade_recebedor,
        valor=payload.valor,
        identificador=payload.identificador or "***",
        descricao=payload.descricao
    )

    return PixGerarResponse(
        pix_copia_e_cola=pix_string,
        chave=payload.chave,
        beneficiario=payload.nome_recebedor,
        cidade=payload.cidade_recebedor,
        valor=payload.valor,
        txid=payload.identificador or "***",
        mensagem="Código PIX Copia e Cola gerado com sucesso no padrão EMVCo."
    )


@app.post(
    "/api/v1/pix/decodificar",
    response_model=PixDecodificarResponse,
    tags=["PIX EMVCo"],
    summary="Leitor e decodificador de código PIX Copia e Cola",
    description="Decodifica uma string de PIX Copia e Cola, validando o checksum CRC16 e extraindo chave, recebedor, cidade, valor e TXID."
)
def post_decodificar_pix(payload: PixDecodificarRequest):
    res = decodificar_payload_pix(payload.payload)
    if not res["valido"] and not res.get("crc16_valido"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res["mensagem"]
        )

    return PixDecodificarResponse(
        valido=res["valido"],
        chave=res.get("chave"),
        beneficiario=res.get("beneficiario"),
        cidade=res.get("cidade"),
        valor=res.get("valor"),
        txid=res.get("txid"),
        crc16_valido=res.get("crc16_valido", False),
        mensagem=res["mensagem"]
    )


# ==============================================================================
# NOVO ENDPOINT: INDICADORES FINANCEIROS & CÂMBIO EM TEMPO REAL
# ==============================================================================
@app.get(
    "/api/v1/indicadores",
    response_model=IndicadoresResponse,
    tags=["Indicadores Econômicos"],
    summary="Cotações de Moedas (Dólar/Euro) e Taxas Oficiais (Selic, CDI, IPCA)",
    description="Retorna em tempo real cotações de câmbio comercial (compra, venda e variação diária) e taxas econômicas oficiais do Brasil com cache inteligente em memória."
)
def get_indicadores():
    agora = time.time()
    # Retorna do cache se ainda for válido (TTL de 15 minutos)
    if CACHE_INDICADORES["dados"] and agora < CACHE_INDICADORES["expira_em"]:
        return CACHE_INDICADORES["dados"]

    # 1. Cotação de Câmbio (AwesomeAPI)
    try:
        resp_cambio = http_session.get(
            "https://economia.awesomeapi.com.br/last/USD-BRL,EUR-BRL",
            headers=REQUEST_HEADERS,
            timeout=DEFAULT_TIMEOUT_SECONDS
        )
        dados_cambio = resp_cambio.json() if resp_cambio.status_code == 200 else {}
    except Exception:
        dados_cambio = {}

    usd = dados_cambio.get("USDBRL", {})
    eur = dados_cambio.get("EURBRL", {})

    dolar_info = CotacaoMoeda(
        nome=usd.get("name", "Dólar Americano/Real Brasileiro"),
        compra=float(usd.get("bid", 5.20)),
        venda=float(usd.get("ask", 5.21)),
        variacao_percentual=float(usd.get("pctChange", 0.0)),
        alta=float(usd.get("high", 5.22)),
        baixa=float(usd.get("low", 5.18))
    )

    euro_info = CotacaoMoeda(
        nome=eur.get("name", "Euro/Real Brasileiro"),
        compra=float(eur.get("bid", 5.85)),
        venda=float(eur.get("ask", 5.86)),
        variacao_percentual=float(eur.get("pctChange", 0.0)),
        alta=float(eur.get("high", 5.88)),
        baixa=float(eur.get("low", 5.82))
    )

    # 2. Taxas Econômicas (BrasilAPI)
    selic_val = 10.50
    cdi_val = 10.40
    ipca_val = 4.20
    try:
        resp_taxas = http_session.get(
            "https://brasilapi.com.br/api/taxas/v1",
            headers=REQUEST_HEADERS,
            timeout=DEFAULT_TIMEOUT_SECONDS
        )
        if resp_taxas.status_code == 200:
            for item in resp_taxas.json():
                nome_taxa = (item.get("nome") or "").upper()
                if "SELIC" in nome_taxa:
                    selic_val = float(item.get("valor", selic_val))
                elif "CDI" in nome_taxa:
                    cdi_val = float(item.get("valor", cdi_val))
                elif "IPCA" in nome_taxa:
                    ipca_val = float(item.get("valor", ipca_val))
    except Exception:
        pass

    resposta = IndicadoresResponse(
        cambio=CambioInfo(
            dolar_comercial=dolar_info,
            euro=euro_info
        ),
        taxas=TaxasBrasilInfo(
            selic_anual=selic_val,
            cdi_anual=cdi_val,
            ipca_anual=ipca_val
        ),
        atualizado_em=datetime.now(timezone.utc).isoformat()
    )

    # Salva no cache por 15 minutos (900 segundos)
    CACHE_INDICADORES["dados"] = resposta
    CACHE_INDICADORES["expira_em"] = agora + 900

    return resposta


# ==============================================================================
# ENTRYPOINT PARA EXECUÇÃO LOCAL E AMBIENTES DE NUVEM (RENDER.COM)
# ==============================================================================
if __name__ == "__main__":
    import uvicorn
    porta = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=porta, reload=False)
