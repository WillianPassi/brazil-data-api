# 🇧🇷 Brazil Data & Financial API

Micro-serviço em Python de alta performance construído com **FastAPI**, **Pydantic v2** e **Requests (com HTTP Connection Pooling)** para validação cadastral, decodificação de boletos, geração de PIX e indicadores financeiros do Brasil.

Projetado especificamente para:
- Rodar no plano gratuito do **Render.com**.
- Ser monetizado e listado no **RapidAPI**.
- Atuar com baixa latência, alta resiliência e fail-safes.

---

## 📌 Endpoints Disponíveis

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/health` | Status de saúde da aplicação e timestamp UTC |
| `GET` | `/api/v1/cep/{cep}` | Sanitização e consulta de CEP via ViaCEP |
| `GET` | `/api/v1/cnpj/{cnpj}` | Validação prévia de Módulo 11 e enriquecimento cadastral via BrasilAPI |
| `POST` | `/api/v1/validador` | Validador universal de CPF, CNPJ e detecção automática de Chave PIX |
| `POST` | `/api/v1/boleto/decodificar` | Validação e decodificação de Linha Digitável de Boletos (Banco, Vencimento, Valor e Código de Barras) |
| `POST` | `/api/v1/pix/gerar` | Gerador oficial de código "PIX Copia e Cola" (Padrão BACEN / EMVCo com CRC16) |
| `POST` | `/api/v1/pix/decodificar` | Leitor e decodificador de código PIX Copia e Cola |
| `GET` | `/api/v1/indicadores` | Cotações em tempo real de Dólar/Euro e taxas Selic, CDI e IPCA |
| `GET` | `/docs` | Documentação interativa Swagger UI |
| `GET` | `/openapi.json` | Especificação OpenAPI 3.0 para RapidAPI |

---

## 🛠️ Como Executar Localmente

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Executar o servidor
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
Acesse no navegador: `http://localhost:8000/docs`
