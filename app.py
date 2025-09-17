from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import base64
import tempfile
import os
import gzip
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
import logging
import urllib3

# Desabilitar warnings SSL para desenvolvimento
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI(title="SEFAZ Manifestação + Consulta API", version="3.1.0")

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

@app.get("/")
async def root():
    return {"message": "SEFAZ Manifestação + Consulta API - Funcionando!", "version": "3.1.0"}

@app.post("/consultar-notas-recebidas")
async def consultar_notas_recebidas(
    cnpj_empresa: str = Form(...),
    data_inicio: str = Form(...),
    data_fim: str = Form(...),
    certificado_base64: str = Form(...),
    senha_certificado: str = Form(...),
    estado: str = Form(default="AN")
):
    """
    Fluxo completo SEFAZ:
    1. Manifestação do Destinatário - listar chaves
    2. Consultar NF-e - baixar XMLs das chaves encontradas
    """
    try:
        logger.info(f"🚀 Iniciando fluxo completo SEFAZ v3.1.0")
        logger.info(f"📋 CNPJ Empresa: {cnpj_empresa}")
        logger.info(f"📅 Período: {data_inicio} a {data_fim}")
        logger.info(f"🏛️ Estado: {estado}")

        # Validar parâmetros
        if not cnpj_empresa or len(cnpj_empresa.replace('.', '').replace('/', '').replace('-', '')) != 14:
            raise HTTPException(status_code=400, detail="CNPJ inválido")

        # Limpar CNPJ
        cnpj_limpo = cnpj_empresa.replace('.', '').replace('/', '').replace('-', '')
        logger.info(f"🔍 CNPJ limpo para consulta: {cnpj_limpo}")

        logger.info("🔐 Processando consulta SEFAZ REAL com certificado digital")

        # Carregar certificado
        certificado_bytes = base64.b64decode(certificado_base64)
        private_key, cert, additional_certs = pkcs12.load_key_and_certificates(
            certificado_bytes,
            senha_certificado.encode()
        )
        logger.info("✅ Certificado carregado com sucesso")

        # Salvar certificado temporariamente
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
            # PASSO 1: Consultar Manifestação do Destinatário
            logger.info("🔍 PASSO 1: Consultando Manifestação do Destinatário...")
            chaves_encontradas = await consultar_manifestacao_destinatario(
                cnpj_limpo, data_inicio, data_fim, cert_path, key_path, estado
            )

            logger.info(f"📊 Chaves encontradas: {len(chaves_encontradas)}")

            if not chaves_encontradas:
                logger.info("📭 Nenhuma chave encontrada na Manifestação")
                return {
                    "success": True,
                    "notas": [],
                    "totalConsultado": 0,
                    "totalErros": 0,
                    "totalSalvo": 0,
                    "resumo": "Nenhuma nota fiscal encontrada na Manifestação do Destinatário",
                    "detalhes": [f"Período consultado: {data_inicio} a {data_fim}", f"CNPJ consultado: {cnpj_limpo}"]
                }

            logger.info(f"✅ Encontradas {len(chaves_encontradas)} chaves na Manifestação")

            # PASSO 2: Baixar XMLs das chaves encontradas
            logger.info("📥 PASSO 2: Baixando XMLs das chaves encontradas...")
            notas_completas = []
            total_erros = 0

            for chave in chaves_encontradas:
                try:
                    logger.info(f"⬇️ Baixando XML para chave: {chave}")
                    xml_completo = await consultar_nfe_por_chave(
                        chave, cert_path, key_path, estado
                    )
                    if xml_completo:
                        # Extrair informações do XML
                        info_nota = extrair_info_nfe(xml_completo, chave)
                        if info_nota:
                            notas_completas.append(info_nota)
                            logger.info(f"✅ XML baixado e processado: {chave[:20]}...")
                        else:
                            logger.warning(f"⚠️ Erro ao extrair info: {chave[:20]}...")
                            total_erros += 1
                    else:
                        logger.warning(f"⚠️ Erro ao baixar XML: {chave[:20]}...")
                        total_erros += 1
                except Exception as e:
                    logger.error(f"❌ Erro ao processar chave {chave[:20]}...: {str(e)}")
                    total_erros += 1

            return {
                "success": True,
                "notas": notas_completas,
                "totalConsultado": len(chaves_encontradas),
                "totalErros": total_erros,
                "totalSalvo": len(notas_completas),
                "resumo": f"Fluxo completo: {len(chaves_encontradas)} chaves → {len(notas_completas)} XMLs baixados",
                "detalhes": [
                    f"Manifestação: {len(chaves_encontradas)} chaves encontradas",
                    f"Consulta NF-e: {len(notas_completas)} XMLs baixados com sucesso",
                    f"Erros: {total_erros}",
                    f"CNPJ: {cnpj_limpo}"
                ]
            }

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

async def consultar_manifestacao_destinatario(cnpj_empresa, data_inicio, data_fim, cert_path, key_path, estado):
    """
    Consulta REAL Manifestação do Destinatário para obter lista de chaves
    """
    try:
        logger.info(f"🌐 Consultando Manifestação do Destinatário REAL no SEFAZ")
        logger.info(f"📋 CNPJ: {cnpj_empresa}")
        logger.info(f"📅 Período: {data_inicio} a {data_fim}")
        logger.info(f"🏛️ Estado: {estado}")

        # Determinar endpoint baseado no estado
        if estado == "SP":
            url = "https://nfe.fazenda.sp.gov.br/ws/nfedistribuicaodfe.asmx"
        else:
            # Ambiente Nacional (AN) - usado por SC e outros estados
            url = "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"

        logger.info(f"🔗 Endpoint: {url}")

        # Converter datas para formato do SEFAZ
        try:
            data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d')
            data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d')
        except ValueError as e:
            logger.error(f"❌ Erro no formato da data: {str(e)}")
            return []

        # Determinar código UF baseado no estado
        if estado == "SP":
            codigo_uf = "35"  # São Paulo
        elif estado == "SC":
            codigo_uf = "42"  # Santa Catarina
        else:
            codigo_uf = "35"  # Default SP

        # XML de consulta Manifestação do Destinatário
        xml_consulta = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:nfe="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">
    <soap:Header/>
    <soap:Body>
        <nfe:nfeDistDFeInteresse>
            <nfe:nfeDadosMsg>
                <distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
                    <tpAmb>1</tpAmb>
                    <cUFAutor>{codigo_uf}</cUFAutor>
                    <CNPJ>{cnpj_empresa}</CNPJ>
                    <consNSU>
                        <NSU>000000000000000</NSU>
                    </consNSU>
                </distDFeInt>
            </nfe:nfeDadosMsg>
        </nfe:nfeDistDFeInteresse>
    </soap:Body>
</soap:Envelope>"""

        headers = {
            'Content-Type': 'application/soap+xml; charset=utf-8',
            'SOAPAction': 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse'
        }

        logger.info("📤 Enviando requisição SOAP para SEFAZ...")

        # Fazer requisição SOAP com certificado digital
        # NOTA: verify=False temporariamente para resolver SSL verification
        response = requests.post(
            url,
            data=xml_consulta,
            headers=headers,
            cert=(cert_path, key_path),
            verify=False,  # Desabilitar verificação SSL temporariamente
            timeout=30
        )

        logger.info(f"📊 Status da resposta: {response.status_code}")

        if response.status_code != 200:
            logger.error(f"❌ Erro HTTP: {response.status_code} - {response.text}")
            return []

        # Parse da resposta XML
        response_xml = response.text
        logger.info(f"📄 Resposta SEFAZ recebida (primeiros 500 chars): {response_xml[:500]}...")

        # LOG: Resposta completa para debug (temporário)
        logger.info(f"🔍 Resposta SEFAZ COMPLETA: {response_xml}")

        # Extrair chaves da resposta
        chaves_encontradas = extrair_chaves_manifestacao(response_xml)

        logger.info(f"🔑 Chaves extraídas da Manifestação: {len(chaves_encontradas)}")

        if chaves_encontradas:
            for i, chave in enumerate(chaves_encontradas[:5]):  # Log das primeiras 5
                logger.info(f"   {i+1}. {chave}")

        return chaves_encontradas

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Erro na requisição SEFAZ: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"❌ Erro na consulta Manifestação: {str(e)}")
        return []

async def consultar_nfe_por_chave(chave_acesso, cert_path, key_path, estado):
    """
    Consulta REAL NF-e individual por chave para baixar XML completo
    """
    try:
        logger.info(f"📄 Consultando XML REAL para chave: {chave_acesso}")

        # Determinar endpoint baseado no estado
        if estado == "SP":
            url = "https://nfe.fazenda.sp.gov.br/ws/nfeconsultaprotocolo4.asmx"
        else:
            # Ambiente Nacional (AN) - usado por SC e outros estados
            url = "https://www1.nfe.fazenda.gov.br/NFeConsultaProtocolo/NFeConsultaProtocolo.asmx"

        logger.info(f"🔗 Endpoint Consulta NF-e: {url}")

        # XML de consulta NF-e por chave
        xml_consulta = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:nfe="http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsultaProtocolo">
    <soap:Header/>
    <soap:Body>
        <nfe:nfeConsultaNF>
            <nfe:nfeDadosMsg>
                <consSitNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
                    <tpAmb>1</tpAmb>
                    <xServ>CONSULTAR</xServ>
                    <chNFe>{chave_acesso}</chNFe>
                </consSitNFe>
            </nfe:nfeDadosMsg>
        </nfe:nfeConsultaNF>
    </soap:Body>
</soap:Envelope>"""

        headers = {
            'Content-Type': 'application/soap+xml; charset=utf-8',
            'SOAPAction': 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsultaProtocolo/nfeConsultaNF'
        }

        logger.info("📤 Enviando requisição SOAP para consulta NF-e...")

        # Fazer requisição SOAP com certificado digital
        # NOTA: verify=False temporariamente para resolver SSL verification
        response = requests.post(
            url,
            data=xml_consulta,
            headers=headers,
            cert=(cert_path, key_path),
            verify=False,  # Desabilitar verificação SSL temporariamente
            timeout=30
        )

        logger.info(f"📊 Status da resposta NF-e: {response.status_code}")

        if response.status_code != 200:
            logger.error(f"❌ Erro HTTP na consulta NF-e: {response.status_code} - {response.text}")
            return None

        # Parse da resposta XML
        response_xml = response.text
        logger.info(f"📄 Resposta NF-e recebida (primeiros 500 chars): {response_xml[:500]}...")

        # Extrair XML da NF-e da resposta
        xml_nfe = extrair_xml_nfe_da_resposta(response_xml)

        if xml_nfe:
            logger.info(f"✅ XML da NF-e extraído com sucesso para chave {chave_acesso[:20]}...")
            return xml_nfe
        else:
            logger.warning(f"⚠️ Não foi possível extrair XML da NF-e para chave {chave_acesso[:20]}...")
            return None

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Erro na requisição consulta NF-e: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"❌ Erro na consulta NF-e: {str(e)}")
        return None

def extrair_chaves_manifestacao(response_xml):
    """
    Extrai chaves de acesso da resposta da Manifestação do Destinatário
    """
    try:
        root = ET.fromstring(response_xml)

        # Múltiplos namespaces possíveis
        namespaces = {
            'soap': 'http://www.w3.org/2003/05/soap-envelope',
            'nfe': 'http://www.portalfiscal.inf.br/nfe',
            'default': 'http://www.portalfiscal.inf.br/nfe'
        }

        chaves = []

        logger.info(f"🔍 DEBUG: Root tag: {root.tag}")

        # LOG: Elementos encontrados para debug
        for elem in root.iter():
            if 'docZip' in elem.tag or 'resNFe' in elem.tag or 'chNFe' in elem.tag:
                logger.info(f"🔍 DEBUG: Elemento encontrado: {elem.tag}")

        # Buscar elementos docZip que contêm as chaves (sem namespace)
        doc_zips = root.findall('.//docZip') + root.findall('.//nfe:docZip', namespaces)

        logger.info(f"🔍 DEBUG: Encontrados {len(doc_zips)} docZips")

        for doc_zip in doc_zips:
            # Decodificar base64 do conteúdo
            try:
                conteudo_b64 = doc_zip.text
                if conteudo_b64:
                    logger.info(f"🔍 DEBUG: Processando docZip de {len(conteudo_b64)} chars")
                    conteudo_bytes = base64.b64decode(conteudo_b64)

                    # Descomprimir se necessário
                    try:
                        conteudo_xml = gzip.decompress(conteudo_bytes).decode('utf-8')
                        logger.info(f"🔍 DEBUG: XML descomprimido: {conteudo_xml[:200]}...")
                    except:
                        conteudo_xml = conteudo_bytes.decode('utf-8')
                        logger.info(f"🔍 DEBUG: XML sem compressão: {conteudo_xml[:200]}...")

                    # Extrair chave do XML interno
                    xml_interno = ET.fromstring(conteudo_xml)
                    chave_elem = xml_interno.find('.//chNFe') or xml_interno.find('.//nfe:chNFe', namespaces)

                    if chave_elem is not None and chave_elem.text:
                        chaves.append(chave_elem.text)
                        logger.info(f"🔑 DEBUG: Chave encontrada: {chave_elem.text}")

            except Exception as e:
                logger.warning(f"⚠️ Erro ao processar docZip: {str(e)}")
                continue

        # Buscar também elementos de resumo de NF-e (sem namespace)
        resumos_nfe = root.findall('.//resNFe') + root.findall('.//nfe:resNFe', namespaces)

        logger.info(f"🔍 DEBUG: Encontrados {len(resumos_nfe)} resumos")

        for resumo in resumos_nfe:
            chave_attr = resumo.get('chNFe')
            if chave_attr:
                chaves.append(chave_attr)
                logger.info(f"🔑 DEBUG: Chave de resumo: {chave_attr}")

        # Buscar elementos cStat para verificar status da resposta
        c_stats = root.findall('.//cStat')
        for c_stat in c_stats:
            logger.info(f"📊 DEBUG: Status SEFAZ: {c_stat.text}")

        # Buscar elementos xMotivo para mensagens
        x_motivos = root.findall('.//xMotivo')
        for x_motivo in x_motivos:
            logger.info(f"💬 DEBUG: Motivo SEFAZ: {x_motivo.text}")

        # Remover duplicatas
        chaves = list(set(chaves))

        logger.info(f"🔍 Processadas {len(doc_zips)} docZips e {len(resumos_nfe)} resumos")
        logger.info(f"🔑 Total de chaves únicas encontradas: {len(chaves)}")

        return chaves

    except Exception as e:
        logger.error(f"❌ Erro ao extrair chaves da manifestação: {str(e)}")
        return []

def extrair_xml_nfe_da_resposta(response_xml):
    """
    Extrai o XML da NF-e da resposta da consulta SEFAZ
    """
    try:
        root = ET.fromstring(response_xml)

        # Namespace para NF-e
        namespaces = {
            'soap': 'http://www.w3.org/2003/05/soap-envelope',
            'nfe': 'http://www.portalfiscal.inf.br/nfe'
        }

        # Buscar o elemento nfeProc ou NFe
        nfe_proc = root.find('.//nfe:nfeProc', namespaces)
        if nfe_proc is not None:
            return ET.tostring(nfe_proc, encoding='unicode')

        # Se não encontrar nfeProc, buscar NFe
        nfe = root.find('.//nfe:NFe', namespaces)
        if nfe is not None:
            return ET.tostring(nfe, encoding='unicode')

        # Verificar se há protocolo de autorização
        prot_nfe = root.find('.//nfe:protNFe', namespaces)
        if prot_nfe is not None:
            # Construir nfeProc com NFe + protocolo
            nfe_elem = root.find('.//nfe:NFe', namespaces)
            if nfe_elem is not None:
                # Criar elemento nfeProc
                nfe_proc_xml = f'''<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">
{ET.tostring(nfe_elem, encoding='unicode')}
{ET.tostring(prot_nfe, encoding='unicode')}
</nfeProc>'''
                return nfe_proc_xml

        logger.warning("⚠️ Não foi possível localizar XML da NF-e na resposta")
        return None

    except Exception as e:
        logger.error(f"❌ Erro ao extrair XML da NF-e: {str(e)}")
        return None

def extrair_info_nfe(xml_content, chave):
    """
    Extrai informações básicas do XML da NF-e
    """
    try:
        root = ET.fromstring(xml_content)

        # Namespace
        ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}

        # Extrair dados
        data_emissao = root.find('.//nfe:dhEmi', ns)
        emit_cnpj = root.find('.//nfe:emit/nfe:CNPJ', ns)
        emit_nome = root.find('.//nfe:emit/nfe:xNome', ns)
        valor_nf = root.find('.//nfe:vNF', ns)
        numero_nf = root.find('.//nfe:nNF', ns)

        return {
            "chave": chave,
            "numero": numero_nf.text if numero_nf is not None else "",
            "dataEmissao": data_emissao.text if data_emissao is not None else "",
            "fornecedorCNPJ": emit_cnpj.text if emit_cnpj is not None else "",
            "fornecedorNome": emit_nome.text if emit_nome is not None else "",
            "valorTotal": float(valor_nf.text) if valor_nf is not None else 0.0,
            "xmlContent": xml_content
        }
    except Exception as e:
        logger.error(f"❌ Erro ao extrair info NFe: {str(e)}")
        return None

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)