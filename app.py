from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import base64
import tempfile
import os
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
import logging

app = FastAPI(title="SEFAZ XML API", version="1.0.0")

# CORS para permitir chamadas do Google Apps Script
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Endpoints SEFAZ por estado
SEFAZ_ENDPOINTS = {
    'SP': {
        'consulta_nfe': 'https://nfe.fazenda.sp.gov.br/ws/nfestatusservico4.asmx',
        'distribuicao_dfe': 'https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx'
    },
    'RS': {
        'consulta_nfe': 'https://nfe.sefazrs.rs.gov.br/ws/NfeStatusServico/NfeStatusServico4.asmx',
        'distribuicao_dfe': 'https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx'
    },
    'AN': {  # Ambiente Nacional
        'distribuicao_dfe': 'https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx'
    }
}

@app.get("/")
async def root():
    return {"message": "SEFAZ XML API - Funcionando!", "version": "1.0.0"}

@app.post("/consultar-notas-recebidas")
async def consultar_notas_recebidas(
    cnpj_empresa: str = Form(...),
    data_inicio: str = Form(...),
    data_fim: str = Form(...),
    certificado_base64: str = Form(...),
    senha_certificado: str = Form(...),
    estado: str = Form(default="SP")
):
    """
    Consulta notas fiscais recebidas via SEFAZ
    """
    try:
        logger.info(f"🚀 Iniciando consulta SEFAZ para CNPJ: {cnpj_empresa}")
        logger.info(f"📅 Período: {data_inicio} a {data_fim}")
        logger.info(f"🏛️ Estado: {estado}")

        # Validar parâmetros
        if not cnpj_empresa or len(cnpj_empresa.replace('.', '').replace('/', '').replace('-', '')) != 14:
            raise HTTPException(status_code=400, detail="CNPJ inválido")

        # Limpar CNPJ
        cnpj_limpo = cnpj_empresa.replace('.', '').replace('/', '').replace('-', '')

        # Validar datas
        try:
            datetime.strptime(data_inicio, '%Y-%m-%d')
            datetime.strptime(data_fim, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de data inválido. Use YYYY-MM-DD")

        # Decodificar certificado
        try:
            certificado_bytes = base64.b64decode(certificado_base64)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro ao decodificar certificado: {str(e)}")

        # Carregar certificado PKCS#12
        try:
            private_key, cert, additional_certs = pkcs12.load_key_and_certificates(
                certificado_bytes,
                senha_certificado.encode()
            )
            logger.info("✅ Certificado carregado com sucesso")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro ao carregar certificado: {str(e)}")

        # Salvar certificado temporariamente para requests
        with tempfile.NamedTemporaryFile(suffix='.pem', delete=False) as cert_file:
            cert_pem = cert.public_bytes(serialization.Encoding.PEM)
            cert_file.write(cert_pem)
            cert_path = cert_file.name

        with tempfile.NamedTemporaryFile(suffix='.key', delete=False) as key_file:
            key_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            key_file.write(key_pem)
            key_path = key_file.name

        try:
            # Consultar Distribuição DFe
            resultado = await consultar_distribuicao_dfe(
                cnpj_limpo,
                data_inicio,
                data_fim,
                cert_path,
                key_path,
                estado
            )

            return resultado

        finally:
            # Limpar arquivos temporários
            try:
                os.unlink(cert_path)
                os.unlink(key_path)
            except:
                pass

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erro inesperado: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")

async def consultar_distribuicao_dfe(cnpj_empresa, data_inicio, data_fim, cert_path, key_path, estado):
    """
    Consulta Distribuição DFe para encontrar notas recebidas
    """
    try:
        # Endpoint baseado no estado
        if estado in SEFAZ_ENDPOINTS:
            url = SEFAZ_ENDPOINTS[estado]['distribuicao_dfe']
        else:
            url = SEFAZ_ENDPOINTS['AN']['distribuicao_dfe']

        # XML de consulta Distribuição DFe
        xml_consulta = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:nfe="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">
    <soap:Header/>
    <soap:Body>
        <nfe:nfeDistDFeInteresse>
            <nfe:nfeDadosMsg>
                <distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
                    <tpAmb>1</tpAmb>
                    <cUFAutor>35</cUFAutor>
                    <CNPJ>{cnpj_empresa}</CNPJ>
                    <consNSU>
                        <NSU>000000000000000</NSU>
                    </consNSU>
                </distDFeInt>
            </nfe:nfeDadosMsg>
        </nfe:nfeDistDFeInteresse>
    </soap:Body>
</soap:Envelope>"""

        # Headers SOAP
        headers = {
            'Content-Type': 'application/soap+xml; charset=utf-8',
            'SOAPAction': 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse'
        }

        logger.info(f"🌐 Consultando: {url}")

        # Fazer request com certificado
        response = requests.post(
            url,
            data=xml_consulta,
            headers=headers,
            cert=(cert_path, key_path),
            verify=True,
            timeout=30
        )

        logger.info(f"📊 Status response: {response.status_code}")

        if response.status_code != 200:
            return {
                "success": False,
                "error": f"Erro HTTP {response.status_code}: {response.text}",
                "notas": [],
                "totalConsultado": 0,
                "totalErros": 1,
                "totalSalvo": 0
            }

        # Parse XML response
        try:
            root = ET.fromstring(response.text)

            # Encontrar elementos de nota fiscal
            notas_encontradas = []

            # Buscar por diferentes namespaces possíveis
            namespaces = {
                'soap': 'http://www.w3.org/2003/05/soap-envelope',
                'nfe': 'http://www.portalfiscal.inf.br/nfe'
            }

            # Procurar por documentos no response
            for doc in root.findall('.//nfe:docZip', namespaces):
                # Extrair informações do documento
                try:
                    # Decodificar conteúdo
                    conteudo_b64 = doc.text
                    if conteudo_b64:
                        # Processar documento
                        info_nota = processar_documento_nfe(conteudo_b64, data_inicio, data_fim)
                        if info_nota:
                            notas_encontradas.append(info_nota)
                except Exception as e:
                    logger.warning(f"⚠️ Erro ao processar documento: {str(e)}")
                    continue

            logger.info(f"✅ Encontradas {len(notas_encontradas)} notas no período")

            return {
                "success": True,
                "notas": notas_encontradas,
                "totalConsultado": len(notas_encontradas),
                "totalErros": 0,
                "totalSalvo": 0,
                "resumo": f"Encontradas {len(notas_encontradas)} notas fiscais no período",
                "detalhes": [f"Consulta realizada com sucesso via {estado}"]
            }

        except ET.ParseError as e:
            return {
                "success": False,
                "error": f"Erro ao parsear XML response: {str(e)}",
                "notas": [],
                "totalConsultado": 0,
                "totalErros": 1,
                "totalSalvo": 0
            }

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "error": f"Erro de conexão com SEFAZ: {str(e)}",
            "notas": [],
            "totalConsultado": 0,
            "totalErros": 1,
            "totalSalvo": 0
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Erro inesperado na consulta: {str(e)}",
            "notas": [],
            "totalConsultado": 0,
            "totalErros": 1,
            "totalSalvo": 0
        }

def processar_documento_nfe(conteudo_b64, data_inicio, data_fim):
    """
    Processa documento NFe decodificado
    """
    try:
        import gzip

        # Decodificar base64 e descompactar
        conteudo_zip = base64.b64decode(conteudo_b64)
        conteudo_xml = gzip.decompress(conteudo_zip).decode('utf-8')

        # Parse XML da nota
        root = ET.fromstring(conteudo_xml)

        # Extrair informações básicas
        namespaces = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}

        # Chave de acesso
        chave_elem = root.find('.//nfe:infNFe', namespaces)
        chave = chave_elem.get('Id', '').replace('NFe', '') if chave_elem is not None else ''

        # Data de emissão
        data_emissao_elem = root.find('.//nfe:dhEmi', namespaces)
        data_emissao = data_emissao_elem.text if data_emissao_elem is not None else ''

        # Verificar se está no período
        if data_emissao:
            try:
                data_nota = datetime.fromisoformat(data_emissao[:10])
                data_inicio_dt = datetime.strptime(data_inicio, '%Y-%m-%d')
                data_fim_dt = datetime.strptime(data_fim, '%Y-%m-%d')

                if not (data_inicio_dt <= data_nota <= data_fim_dt):
                    return None  # Fora do período
            except:
                pass

        # Fornecedor (emitente)
        emit_elem = root.find('.//nfe:emit', namespaces)
        fornecedor_cnpj = ''
        fornecedor_nome = ''
        if emit_elem is not None:
            cnpj_elem = emit_elem.find('nfe:CNPJ', namespaces)
            nome_elem = emit_elem.find('nfe:xNome', namespaces)
            fornecedor_cnpj = cnpj_elem.text if cnpj_elem is not None else ''
            fornecedor_nome = nome_elem.text if nome_elem is not None else ''

        # Valor total
        valor_elem = root.find('.//nfe:vNF', namespaces)
        valor_total = float(valor_elem.text) if valor_elem is not None else 0.0

        return {
            "chave": chave,
            "dataEmissao": data_emissao,
            "fornecedorCNPJ": fornecedor_cnpj,
            "fornecedorNome": fornecedor_nome,
            "valorTotal": valor_total,
            "xmlContent": conteudo_xml
        }

    except Exception as e:
        logger.warning(f"⚠️ Erro ao processar documento NFe: {str(e)}")
        return None

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)