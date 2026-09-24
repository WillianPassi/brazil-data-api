# 🇧🇷 Brazil Data API

Micro-serviço em Python de alta performance construído com **FastAPI**, **Pydantic** e **Requests (com HTTP Connection Pooling)** para validação e enriquecimento de dados cadastrais e financeiros brasileiros.

Projetado especificamente para:
- Rodar no plano gratuito do **Render.com**.
- Ser monetizado e listado no **RapidAPI**.
- Atuar com baixa latência e fail-safe (tratamento de timeouts e exceções com status HTTP adequados).

---

## 🚀 Tecnologias

- **FastAPI**: Framework web assíncrono de altíssima performance.
- **Uvicorn**: Servidor ASGI leve e veloz.
- **Requests + Urllib3**: Sessões persistentes com Connection Pooling e retries para serviços externos.
- **Pydantic v2**: Validação estrita de contratos de entrada e saída.

---

## 📌 Endpoints da API

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/health` | Status de saúde da aplicação e timestamp UTC |
| `GET` | `/api/v1/cep/{cep}` | Sanitização e consulta de CEP via ViaCEP |
| `GET` | `/api/v1/cnpj/{cnpj}` | Validação prévia de Módulo 11 e enriquecimento cadastral via BrasilAPI |
| `POST` | `/api/v1/validador` | Validador universal de CPF, CNPJ e detecção automática de Chave PIX |
| `GET` | `/docs` | Documentação interativa Swagger UI |
| `GET` | `/openapi.json` | Especificação OpenAPI 3.0 (para importar no RapidAPI) |

---

## 🛠️ Como Executar Localmente

### 1. Clonar e Acessar o Diretório
```bash
git clone https://github.com/seu-usuario/brazil-data-api.git
cd brazil-data-api
```

### 2. Criar e Ativar Ambiente Virtual
```bash
python -m venv venv
# Linux / macOS:
source venv/bin/activate
# Windows:
.\venv\Scripts\activate
```

### 3. Instalar Dependências
```bash
pip install -r requirements.txt
```

### 4. Iniciar o Servidor
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
Acesse no navegador: `http://localhost:8000/docs`

---

## ☁️ Deploy Gratuito no Render.com

1. Suba o repositório no seu GitHub.
2. Acesse o [Dashboard do Render](https://dashboard.render.com/) e clique em **New +** -> **Web Service**.
3. Conecte ao seu repositório `brazil-data-api`.
4. Preencha as configurações:
   - **Name**: `brazil-data-api`
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: `Free`
5. Clique em **Create Web Service**. A sua API estará no ar com HTTPS gratuito!

---

## 🌐 Publicação no RapidAPI

1. Acesse o [RapidAPI Studio / Provider Dashboard](https://rapidapi.com/studio).
2. Clique em **Add New API**:
   - Preencha o nome: `Brazil Data API`.
   - Categoria: `Data` ou `Financial`.
3. Na aba **Definition**, clique em **Import from OpenAPI** e aponte para:
   - `https://sua-url-no-render.onrender.com/openapi.json`
4. Configure a URL base da sua API no Render como Target URL no RapidAPI.
5. Defina os planos de cobrança (ex: Freemium com 500 req/mês grátis e planos pagos por requisição).

---

## 📋 Exemplos de Chamadas cURL

### 1. Health Check
```bash
curl -X GET "http://localhost:8000/health" \
     -H "Accept: application/json"
```

### 2. Consulta de CEP
```bash
curl -X GET "http://localhost:8000/api/v1/cep/01001-000" \
     -H "Accept: application/json"
```

### 3. Consulta de CNPJ
```bash
curl -X GET "http://localhost:8000/api/v1/cnpj/00.000.000/0001-91" \
     -H "Accept: application/json"
```

### 4. Validador de CPF
```bash
curl -X POST "http://localhost:8000/api/v1/validador" \
     -H "Content-Type: application/json" \
     -d '{
       "tipo": "cpf",
       "valor": "529.982.247-25"
     }'
```

### 5. Validador de CNPJ
```bash
curl -X POST "http://localhost:8000/api/v1/validador" \
     -H "Content-Type: application/json" \
     -d '{
       "tipo": "cnpj",
       "valor": "33.000.167/0001-01"
     }'
```

### 6. Validador e Identificador de Chave PIX
```bash
curl -X POST "http://localhost:8000/api/v1/validador" \
     -H "Content-Type: application/json" \
     -d '{
       "tipo": "pix_chave",
       "valor": "financeiro@suaempresa.com.br"
     }'
```
