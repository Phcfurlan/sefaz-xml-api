# 🐍 SEFAZ XML API - Python

API Python para consulta de Notas Fiscais via SEFAZ, resolvendo limitações do Google Apps Script.

## 🚀 Deploy no Render

### 1. Preparar repositório
```bash
# Criar repositório Git para esta pasta
cd sefaz-api-python
git init
git add .
git commit -m "feat: API Python para consulta SEFAZ"

# Conectar com GitHub (opcional)
git remote add origin https://github.com/SEU_USUARIO/sefaz-xml-api.git
git push -u origin main
```

### 2. Deploy no Render
1. Acesse https://render.com
2. Conecte seu GitHub
3. Crie novo **Web Service**
4. Selecione este repositório
5. Configure:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
   - **Python Version**: 3.11

### 3. Configurar URL no Google Apps Script
Após deploy, ajuste a URL na linha 2015 do `importacao-xml-sefaz.js`:
```javascript
const API_URL = 'https://SEU-APP.onrender.com/consultar-notas-recebidas';
```

## 🧪 Teste Local
```bash
# Instalar dependências
pip install -r requirements.txt

# Executar
uvicorn app:app --reload

# Testar
curl http://localhost:8000/health
```

## 📋 Endpoints

- `GET /` - Status da API
- `GET /health` - Health check
- `POST /consultar-notas-recebidas` - Consulta SEFAZ

## 🔐 Parâmetros da consulta

- `cnpj_empresa`: CNPJ da empresa (14 dígitos)
- `data_inicio`: Data início (YYYY-MM-DD)
- `data_fim`: Data fim (YYYY-MM-DD)
- `certificado_base64`: Certificado .pfx em base64
- `senha_certificado`: Senha do certificado
- `estado`: Estado para consulta (SP, RS, etc.)

## 🎯 Vantagens vs Google Apps Script

✅ **Certificados PKCS#12** - Suporte nativo a .pfx
✅ **Requests SOAP** - Biblioteca completa
✅ **XML Processing** - Parser robusto
✅ **Timeouts longos** - Sem limite de 6 minutos
✅ **Debugging** - Logs detalhados

## 🔧 Configuração SEFAZ

A API consulta automaticamente:
- **SP**: SEFAZ São Paulo
- **RS**: SEFAZ Rio Grande do Sul
- **AN**: Ambiente Nacional (fallback)

## 🐛 Logs e Debug

A API gera logs detalhados para debug:
```
🚀 Iniciando consulta SEFAZ para CNPJ: 58521876000163
📅 Período: 2025-09-01 a 2025-09-17
🏛️ Estado: SP
✅ Certificado carregado com sucesso
🌐 Consultando: https://www1.nfe.fazenda.gov.br/...
📊 Status response: 200
✅ Encontradas 5 notas no período
```