import os
import re
from datetime import datetime, timezone
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
# CONFIGURAÇÃO DE SESSÃO HTTP COM POOLING E TIMEOUT
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
# APLICAÇÃO FASTAPI & METADADOS OPENAPI (OTIMIZADO PARA RAPIDAPI)
# ==============================================================================
app = FastAPI(
    title="Brazil Data API",
    description="""
API de alta performance para validação e enriquecimento de dados cadastrais e financeiros brasileiros.
Desenvolvida para integração direta com **RapidAPI** e deploy em ambientes serverless/PaaS como **Render.com**.

### Recursos Disponíveis:
- **Health Check**: Monitoramento de disponibilidade e uptime.
- **Consulta de CEP**: Consulta via ViaCEP com sanitização e normalização de dados.
- **Consulta de CNPJ**: Validação de módulo 11 antecipada e enriquecimento cadastral via Receita/BrasilAPI.
- **Validador Multifunção**: Validação matemática de CPF, CNPJ e detecção e validação de chaves PIX.
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# Habilita CORS irrestrito para consumo direto por frontend web ou RapidAPI Proxy
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# SCHEMAS PYDANTIC
# ==============================================================================
class HealthResponse(BaseModel):
    status: str = Field(..., example="online")
    timestamp: str = Field(..., example="2026-09-24T14:53:13.000Z")
    service: str = Field(default="brazil-data-api")
    version: str = Field(default="1.0.0")


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
        example="123.456.789-00"
    )

    model_config = ConfigDict(use_enum_values=True)


class DetalhesValidacao(BaseModel):
    subtipo_pix: Optional[str] = Field(None, example="email")
    algoritmo: Optional[str] = Field(None, example="Módulo 11 (Receita Federal)")
    valor_formatado: Optional[str] = Field(None, example="123.456.789-00")


class ValidadorResponse(BaseModel):
    tipo: str = Field(..., example="cpf")
    valor_original: str = Field(..., example="123.456.789-00")
    valor_limpo: str = Field(..., example="12345678900")
    valido: bool = Field(..., example=True)
    mensagem: str = Field(..., example="CPF válido com sucesso.")
    detalhes: DetalhesValidacao


# ==============================================================================
# ALGORITMOS DE VALIDAÇÃO MATEMÁTICA E REGRAS DE NEGÓCIO
# ==============================================================================
def sanitizar_numeros(valor: str) -> str:
    """Remove qualquer caractere não numérico da string."""
    return re.sub(r"\D", "", valor or "")


def validar_cpf_matematico(cpf: str) -> tuple[bool, str]:
    """
    Valida CPF através do cálculo dos dois dígitos verificadores em Módulo 11.
    Retorna (valido: bool, motivo: str).
    """
    digitos = sanitizar_numeros(cpf)

    if len(digitos) != 11:
        return False, f"CPF deve conter exatamente 11 dígitos numéricos (recebido: {len(digitos)})."

    if len(set(digitos)) == 1:
        return False, "CPF não pode ser composto por dígitos repetidos."

    # Cálculo do 1º dígito verificador
    soma_1 = sum(int(digitos[i]) * (10 - i) for i in range(9))
    resto_1 = (soma_1 * 10) % 11
    d1 = 0 if resto_1 == 10 else resto_1
    if int(digitos[9]) != d1:
        return False, f"1º dígito verificador inválido (esperado: {d1}, recebido: {digitos[9]})."

    # Cálculo do 2º dígito verificador
    soma_2 = sum(int(digitos[i]) * (11 - i) for i in range(10))
    resto_2 = (soma_2 * 10) % 11
    d2 = 0 if resto_2 == 10 else resto_2
    if int(digitos[10]) != d2:
        return False, f"2º dígito verificador inválido (esperado: {d2}, recebido: {digitos[10]})."

    return True, "CPF válido matematicamente."


def validar_cnpj_matematico(cnpj: str) -> tuple[bool, str]:
    """
    Valida CNPJ através do cálculo dos dois dígitos verificadores em Módulo 11.
    Retorna (valido: bool, motivo: str).
    """
    digitos = sanitizar_numeros(cnpj)

    if len(digitos) != 14:
        return False, f"CNPJ deve conter exatamente 14 dígitos numéricos (recebido: {len(digitos)})."

    if len(set(digitos)) == 1:
        return False, "CNPJ não pode ser composto por dígitos repetidos."

    # Pesos para o 1º dígito
    pesos_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma_1 = sum(int(digitos[i]) * pesos_1[i] for i in range(12))
    resto_1 = soma_1 % 11
    d1 = 0 if resto_1 < 2 else 11 - resto_1
    if int(digitos[12]) != d1:
        return False, f"1º dígito verificador inválido (esperado: {d1}, recebido: {digitos[12]})."

    # Pesos para o 2º dígito
    pesos_2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma_2 = sum(int(digitos[i]) * pesos_2[i] for i in range(13))
    resto_2 = soma_2 % 11
    d2 = 0 if resto_2 < 2 else 11 - resto_2
    if int(digitos[13]) != d2:
        return False, f"2º dígito verificador inválido (esperado: {d2}, recebido: {digitos[13]})."

    return True, "CNPJ válido matematicamente."


def formatar_cpf(digitos: str) -> str:
    """Aplica a máscara padrão de CPF: 000.000.000-00."""
    return f"{digitos[0:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:11]}"


def formatar_cnpj(digitos: str) -> str:
    """Aplica a máscara padrão de CNPJ: 00.000.000/0000-00."""
    return f"{digitos[0:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:14]}"


def identificar_e_validar_pix(chave: str) -> dict:
    """
    Identifica automaticamente a natureza da chave PIX e valida seu formato e consistência
    conforme as normas do Banco Central do Brasil (BACEN).
    Tipos suportados:
      - Aleatória (EVP / UUID v4)
      - E-mail (RFC 5322)
      - Telefone (Padrão Internacional E.164 ou Nacional com DDD)
      - CPF (Com validação Módulo 11)
      - CNPJ (Com validação Módulo 11)
    """
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

    # 3. Telefone (detectado por prefixo '+' ou parênteses / hífens típicos de número telefônico)
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
                "mensagem": "Telefone inválido. O formato oficial do BACEN exige DDI +55 seguido de DDD e 8 ou 9 dígitos."
            }
        else:
            # Telefone nacional sem +55 informado
            if len(apenas_digitos) in (10, 11):
                ddd = int(apenas_digitos[:2])
                if 11 <= ddd <= 99:
                    fmt = f"+55{apenas_digitos}"
                    return {
                        "subtipo": "telefone",
                        "valido": True,
                        "valor_limpo": apenas_digitos,
                        "valor_formatado": fmt,
                        "mensagem": f"Telefone brasileiro com DDD válido. Formato BACEN: {fmt}."
                    }
            return {
                "subtipo": "telefone",
                "valido": False,
                "valor_limpo": apenas_digitos,
                "valor_formatado": chave_limpa,
                "mensagem": "Formato de telefone inválido para chave PIX."
            }

    # 4. CNPJ (14 dígitos ou com barra / ponto)
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

    # 5. CPF (11 dígitos ou pontuação explícita de CPF)
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

    # Não reconhecido
    return {
        "subtipo": "desconhecido",
        "valido": False,
        "valor_limpo": apenas_digitos or chave_limpa,
        "valor_formatado": chave_limpa,
        "mensagem": "Formato de chave PIX não identificado. Deve ser CPF, CNPJ, e-mail, telefone (+55) ou chave aleatória UUID."
    }


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
        version="1.0.0"
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

    # ViaCEP sinaliza CEP inexistente via flag 'erro'
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

    # 1. Validação matemática rigorosa antecipada (Módulo 11)
    valido, motivo = validar_cnpj_matematico(cnpj_limpo)
    if not valido:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CNPJ inválido: {motivo}"
        )

    # 2. Consulta à base aberta da BrasilAPI
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

    # Monta estrutura dos schemas
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
# ENTRYPOINT PARA EXECUÇÃO LOCAL E EM AMBIENTES DE NUVEM (RENDER.COM)
# ==============================================================================
if __name__ == "__main__":
    import uvicorn
    # Render.com define a variável de ambiente $PORT dinamicamente
    porta = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=porta, reload=False)
