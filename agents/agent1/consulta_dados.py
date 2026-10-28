# agents/agent1/consulta_dados.py
#
# 2ª etapa: depois de extrair os dados da nota, consulta o banco e informa se
# FORNECEDOR, FATURADO e DESPESA já existem (e com qual ID).
from sqlalchemy import select

from database import (CLASSIF_DESPESA, MOV_APAGAR, PESSOA_FATURADO, PESSOA_FORNECEDOR,
                      STATUS_ATIVO, Classificacao, MovimentoContas, Pessoa, fmt_data,
                      formata_documento, norm_nome, para_decimal, parse_data,
                      rotulo_documento, so_digitos)


def buscar_pessoa(db, tipo, documento, nome):
    """Com CNPJ/CPF a busca é só pelo documento (filiais têm CNPJs diferentes).
    Sem documento na nota, procura pelo nome."""
    consulta = select(Pessoa).where(Pessoa.tipo == tipo)
    if documento:
        consulta = consulta.where(Pessoa.documento == documento)
    elif nome:
        consulta = consulta.where(Pessoa.razao_social == nome)
    else:
        return None
    return db.scalars(consulta.limit(1)).first()


def buscar_classificacao(db, tipo, descricao):
    return db.scalars(
        select(Classificacao)
        .where(Classificacao.tipo == tipo, Classificacao.descricao == descricao)
        .limit(1)
    ).first()


def normalizar_parcelas(nota):
    """Lista de parcelas pronta para gravar. Nota sem parcelas vira uma parcela
    única com o valor total. Numeração refeita (1..N)."""
    valor_total = para_decimal(nota.get("valor_total"))
    brutas = [p for p in (nota.get("parcelas") or []) if isinstance(p, dict)]
    if not brutas:
        brutas = [{"valor": valor_total, "data_vencimento": None}]
    total = len(brutas)
    parcelas = []
    for i, p in enumerate(brutas, start=1):
        valor = para_decimal(p.get("valor"))
        if valor is None and total == 1:
            valor = valor_total
        parcelas.append({
            "numero": i,
            "identificacao": "ÚNICA" if total == 1 else f"{i}/{total}",
            "valor": valor,
            "data_vencimento": parse_data(p.get("data_vencimento")),
        })
    return parcelas


def _item_pessoa(pessoa, nome, documento, rotulo_padrao):
    return {
        "nome": nome,
        "rotulo_documento": rotulo_documento(documento, rotulo_padrao),
        "documento": formata_documento(documento) or None,
        "existe": pessoa is not None,
        "id": pessoa.id if pessoa else None,
        "status": pessoa.status if pessoa else None,
    }


def analisar_nota(db, nota):
    """Consulta o banco e devolve, para FORNECEDOR, FATURADO e cada DESPESA:
    existe ou não, ID e status. Também devolve avisos e bloqueios do lançamento."""
    fornecedor = nota.get("fornecedor") or {}
    faturado = nota.get("faturado") or {}

    forn_nome = norm_nome(fornecedor.get("razao_social"))
    forn_doc = so_digitos(fornecedor.get("cnpj"))
    fat_nome = norm_nome(faturado.get("nome_completo"))
    fat_doc = so_digitos(faturado.get("cpf"))
    despesas_desc = list(dict.fromkeys(
        d for d in (norm_nome(x) for x in (nota.get("tipo_despesa") or [])) if d))

    forn = buscar_pessoa(db, PESSOA_FORNECEDOR, forn_doc, forn_nome)
    fat = buscar_pessoa(db, PESSOA_FATURADO, fat_doc, fat_nome)

    despesas = []
    for descricao in despesas_desc:
        c = buscar_classificacao(db, CLASSIF_DESPESA, descricao)
        despesas.append({
            "descricao": descricao,
            "existe": c is not None,
            "id": c.id if c else None,
            "status": c.status if c else None,
        })

    resultado = {
        "fornecedor": _item_pessoa(forn, forn_nome, forn_doc, "CNPJ"),
        "faturado": _item_pessoa(fat, fat_nome, fat_doc, "CPF"),
        "despesas": despesas,
        "avisos": [],
        "bloqueios": [],
    }
    avisos, bloqueios = resultado["avisos"], resultado["bloqueios"]

    # ---- o que impede o lançamento
    if not forn_nome:
        bloqueios.append("Não foi possível identificar o fornecedor (razão social) na nota.")
    if not fat_nome:
        bloqueios.append("Não foi possível identificar o faturado (nome) na nota.")
    if not despesas_desc:
        bloqueios.append("Nenhuma classificação de DESPESA foi identificada para a nota.")
    if para_decimal(nota.get("valor_total")) is None:
        bloqueios.append("O valor total da nota não foi identificado.")

    # cadastro inativo existe, mas não pode ser usado até ser reativado
    if forn and forn.status != STATUS_ATIVO:
        bloqueios.append(f"O fornecedor (ID {forn.id}) está INATIVO. Reative-o para lançar.")
    if fat and fat.status != STATUS_ATIVO:
        bloqueios.append(f"O faturado (ID {fat.id}) está INATIVO. Reative-o para lançar.")
    for d in despesas:
        if d["existe"] and d["status"] != STATUS_ATIVO:
            bloqueios.append(
                f"A despesa {d['descricao']} (ID {d['id']}) está INATIVA. Reative-a para lançar.")

    # ---- parcelas
    parcelas = normalizar_parcelas(nota)
    if any(p["valor"] is None for p in parcelas):
        bloqueios.append("Há parcela sem valor na nota.")
    else:
        total = para_decimal(nota.get("valor_total"))
        soma = sum(p["valor"] for p in parcelas)
        if total is not None and soma != total:
            avisos.append(
                f"A soma das parcelas (R$ {soma}) é diferente do valor total (R$ {total}).")
    if any(p["data_vencimento"] is None for p in parcelas):
        avisos.append("Há parcela sem data de vencimento válida.")

    # ---- nota já lançada antes (só avisa; não impede)
    duplicado_id = None
    numero = nota.get("numero_nota_fiscal")
    if forn and numero:
        duplicado_id = db.scalar(
            select(MovimentoContas.id).where(
                MovimentoContas.tipo == MOV_APAGAR,
                MovimentoContas.pessoa_id == forn.id,
                MovimentoContas.numero_documento == str(numero),
                MovimentoContas.status == STATUS_ATIVO,
            ).limit(1))
        if duplicado_id:
            avisos.append(
                f"Esta nota ({numero}) já foi lançada para este fornecedor "
                f"(movimento ID {duplicado_id}).")
    resultado["movimento_duplicado_id"] = duplicado_id

    resultado["pode_lancar"] = not bloqueios
    return resultado
