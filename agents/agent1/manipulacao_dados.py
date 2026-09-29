# agents/agent1/manipulacao_dados.py
import os
import time
from enum import Enum
from typing import List, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 8

# Categorias de DESPESA (conforme o enunciado da atividade).
# Com o tempo vão surgir mais: basta adicionar aqui que o prompt e o schema
# do Gemini se atualizam sozinhos.
CATEGORIAS_DESPESA = {
    "INSUMOS AGRÍCOLAS": [
        "Sementes", "Fertilizantes", "Defensivos Agrícolas", "Corretivos",
    ],
    "MANUTENÇÃO E OPERAÇÃO": [
        "Combustíveis e Lubrificantes",
        "Peças, Parafusos, Componentes Mecânicos",
        "Manutenção de Máquinas e Equipamentos",
        "Pneus, Filtros, Correias",
        "Ferramentas e Utensílios",
    ],
    "RECURSOS HUMANOS": ["Mão de Obra Temporária", "Salários e Encargos"],
    "SERVIÇOS OPERACIONAIS": [
        "Frete e Transporte", "Colheita Terceirizada",
        "Secagem e Armazenagem", "Pulverização e Aplicação",
    ],
    "INFRAESTRUTURA E UTILIDADES": [
        "Energia Elétrica", "Arrendamento de Terras",
        "Construções e Reformas", "Materiais de Construção",
    ],
    "ADMINISTRATIVAS": [
        "Honorários (Contábeis, Advocatícios, Agronômicos)",
        "Despesas Bancárias e Financeiras",
    ],
    "SEGUROS E PROTEÇÃO": [
        "Seguro Agrícola", "Seguro de Ativos (Máquinas/Veículos)",
        "Seguro Prestamista",
    ],
    "IMPOSTOS E TAXAS": ["ITR, IPTU, IPVA, INCRA-CCIR"],
    "INVESTIMENTOS": [
        "Aquisição de Máquinas e Implementos", "Aquisição de Veículos",
        "Aquisição de Imóveis", "Infraestrutura Rural",
    ],
}

CategoriaDespesa = Enum("CategoriaDespesa", {c: c for c in CATEGORIAS_DESPESA})


# ---- Schema do JSON (o Gemini é obrigado a devolver exatamente isto) ----
class Fornecedor(BaseModel):
    razao_social: Optional[str] = Field(description="Razão social do emitente da nota")
    nome_fantasia: Optional[str] = Field(description="Nome fantasia do emitente, se houver")
    cnpj: Optional[str] = Field(description="CNPJ do emitente")


class Faturado(BaseModel):
    nome_completo: Optional[str] = Field(description="Nome completo do destinatário")
    cpf: Optional[str] = Field(description="CPF do destinatário (null se for pessoa jurídica)")


class Parcela(BaseModel):
    numero: int = Field(description="Número da parcela, começando em 1")
    valor: Optional[float] = Field(description="Valor da parcela, sem símbolo de moeda")
    data_vencimento: Optional[str] = Field(description="Vencimento no formato DD/MM/AAAA")


class NotaFiscal(BaseModel):
    fornecedor: Fornecedor
    faturado: Faturado
    numero_nota_fiscal: Optional[str]
    data_emissao: Optional[str] = Field(description="Formato DD/MM/AAAA")
    descricao_produtos: List[str] = Field(description="Uma descrição por produto/serviço")
    parcelas: List[Parcela]
    valor_total: Optional[float] = Field(description="VALOR TOTAL DA NOTA, sem símbolo de moeda")
    tipo_despesa: List[CategoriaDespesa] = Field(
        description="Categoria(s) da despesa, inferida(s) a partir dos produtos"
    )


def _texto_categorias() -> str:
    linhas = []
    for categoria, subs in CATEGORIAS_DESPESA.items():
        linhas.append(f"- {categoria}: {'; '.join(subs)}")
    return "\n".join(linhas)


PROMPT_EXTRACAO = f"""
Você é um assistente especializado em extrair dados de notas fiscais brasileiras
(NF-e / DANFE) referentes a CONTAS A PAGAR.

Analise o PDF anexado e preencha todos os campos pedidos.

Regras:
- "fornecedor" é o EMITENTE da nota (quem vendeu): razão social, nome fantasia e CNPJ.
- "faturado" é o DESTINATÁRIO/REMETENTE (quem comprou e vai pagar): nome completo e CPF.
  Se o destinatário for pessoa jurídica (só tem CNPJ), retorne cpf = null.
- "numero_nota_fiscal": número da nota, do jeito que aparece no documento.
- "valor_total": use o campo "VALOR TOTAL DA NOTA" (não confunda com
  "VALOR TOTAL DOS PRODUTOS"). Converta para número: 3.086,75 vira 3086.75.
- "parcelas": uma entrada para cada parcela de FATURA/DUPLICATAS. Sempre uma lista,
  mesmo com uma parcela só. Se a nota não trouxer fatura/duplicata, retorne uma
  única parcela com o valor total da nota e data_vencimento = null.
- "descricao_produtos": uma descrição por item (não é necessário criar entidade produto).
- Datas no formato DD/MM/AAAA.
- Campo que não existir no documento: null.

"tipo_despesa" NÃO aparece na nota. Você deve INTERPRETAR os produtos e classificar
a despesa em UMA das categorias abaixo (o nome da categoria deve ser copiado
exatamente). As subcategorias servem só de guia. Retorne a categoria mais adequada
ao conjunto dos produtos, dentro de uma lista.
Ex.: Óleo diesel -> MANUTENÇÃO E OPERAÇÃO; Material hidráulico -> INFRAESTRUTURA E UTILIDADES.

Categorias:
{_texto_categorias()}
"""


class Agent1:
    """Agent responsável por extrair e classificar dados de notas fiscais em PDF."""

    def __init__(self, api_key: str = None, model_name: str = "gemini-3.8-flash"):
        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY não configurada. Defina a variável de ambiente "
                "GEMINI_API_KEY ou passe api_key ao instanciar Agent1."
            )
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def extrair_dados(self, file_path: str) -> dict:
        """
        Recebe o caminho de um PDF de nota fiscal, envia para o Gemini e
        retorna um dicionário (JSON) com os dados extraídos e a despesa
        já classificada. Tenta novamente automaticamente se o modelo
        estiver temporariamente sobrecarregado (erro 503).
        """
        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        response = None
        last_exception = None

        for tentativa in range(1, MAX_RETRIES + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=[
                        types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                        PROMPT_EXTRACAO,
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=NotaFiscal,  # Gemini gera o JSON já no schema
                    ),
                )
                break
            except Exception as exc:
                last_exception = exc
                is_overloaded = "UNAVAILABLE" in str(exc) or "503" in str(exc)
                if is_overloaded and tentativa < MAX_RETRIES:
                    print(
                        f"Modelo sobrecarregado (tentativa {tentativa}/{MAX_RETRIES}). "
                        f"Tentando novamente em {RETRY_DELAY_SECONDS}s..."
                    )
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                raise

        if response is None:
            raise ValueError(
                f"Não foi possível obter resposta do Gemini após {MAX_RETRIES} "
                f"tentativas. Erro original: {last_exception}"
            )

        if not response.text:
            raise ValueError(
                "O Agent não retornou nenhum texto. Pode ser bloqueio de "
                "segurança ou limite de uso da API atingido."
            )

        try:
            nota = NotaFiscal.model_validate_json(response.text)
        except ValueError as exc:
            raise ValueError(
                f"O Agent não retornou um JSON válido. Resposta bruta: {response.text!r}"
            ) from exc

        dados = nota.model_dump(mode="json")

        # Quantidade de parcelas calculada aqui (não depende do modelo),
        # mantendo a ordem dos campos da atividade.
        resultado = {}
        for chave, valor in dados.items():
            resultado[chave] = valor
            if chave == "descricao_produtos":
                resultado["quantidade_parcelas"] = len(dados["parcelas"])
        return resultado
