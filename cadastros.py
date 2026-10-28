# cadastros.py
#
# API dos cadastros: PESSOAS, CLASSIFICAÇÕES e CONTAS (movimentos a pagar).
# Regras de negócio:
#   - cadastros NÃO são excluídos: o "excluir" só muda o STATUS para INATIVO;
#   - um cadastro INATIVO pode ser REATIVADO;
#   - no CREATE o status é sempre ATIVO (o campo não vem da tela);
#   - no UPDATE o status não muda;
#   - TODOS carrega só os ATIVOS; a BUSCA procura em todos (inclusive INATIVOS).
import re

from flask import Blueprint, jsonify, request
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased, selectinload

from database import (STATUS_ATIVO, STATUS_INATIVO, TIPOS_CLASSIFICACAO, TIPOS_PESSOA,
                      Classificacao, MovimentoContas, Pessoa, fmt_data, formata_documento,
                      get_session, info_banco, mascarar, norm_nome, so_digitos)

bp = Blueprint("cadastros", __name__, url_prefix="/api")
LIMITE = 500


class ErroApi(Exception):
    def __init__(self, mensagem, codigo=400):
        super().__init__(mensagem)
        self.mensagem, self.codigo = mensagem, codigo


@bp.errorhandler(ErroApi)
def _tratar_erro_api(e):
    return jsonify({"erro": e.mensagem}), e.codigo


@bp.errorhandler(SQLAlchemyError)
def _tratar_erro_banco(e):
    detalhe = mascarar(str(getattr(e, "orig", None) or e)).strip().split("\n")[0][:200]
    return jsonify({"erro": f"Banco de dados indisponível ou com erro: {detalhe}"}), 500


# ---------------------------------------------------------------- busca
def _condicoes_busca(model, campos, texto, coluna_documento=None):
    """Busca por mais de um elemento: cada palavra digitada precisa aparecer
    em algum dos campos (AND entre as palavras, OR entre os campos)."""
    condicoes = []
    for termo in [t for t in re.split(r"\s+", texto.strip().upper()) if t]:
        partes = [campo.like(f"%{termo}%") for campo in campos]
        partes.append(model.status == termo)
        if termo.isdigit() and len(termo) <= 9:
            partes.append(model.id == int(termo))
        digitos = so_digitos(termo)
        if coluna_documento is not None and digitos:
            partes.append(coluna_documento.like(f"%{digitos}%"))
        condicoes.append(or_(*partes))
    return condicoes


def _comando_lista(stmt, model, campos, coluna_documento=None):
    """TODOS (?todos=1) -> só ATIVOS.  BUSCA (?q=...) -> todos os status.
    Sem nenhum dos dois, a tabela fica vazia (None)."""
    texto = (request.args.get("q") or "").strip()
    if request.args.get("todos") == "1":
        stmt = stmt.where(model.status == STATUS_ATIVO)
    elif texto:
        for condicao in _condicoes_busca(model, campos, texto, coluna_documento):
            stmt = stmt.where(condicao)
    else:
        return None
    return stmt.order_by(model.id).limit(LIMITE)


def _corpo():
    corpo = request.get_json(silent=True)
    if not isinstance(corpo, dict):
        raise ErroApi("Envie os dados em JSON.")
    return corpo


def _mudar_status(model, id_, status, nome):
    with get_session() as db:
        objeto = db.get(model, id_)
        if objeto is None:
            raise ErroApi(f"{nome} não encontrado(a).", 404)
        objeto.status = status
        if isinstance(objeto, MovimentoContas):
            for parcela in objeto.parcelas:
                parcela.status = status
        db.commit()
        return objeto


@bp.get("/info")
def info():
    return jsonify(info_banco())


# ---------------------------------------------------------------- pessoas
def _pessoa_json(p):
    return {"id": p.id, "tipo": p.tipo, "razao_social": p.razao_social,
            "nome_fantasia": p.nome_fantasia,
            "documento": formata_documento(p.documento) or None, "status": p.status}


def _dados_pessoa(corpo):
    tipo = str(corpo.get("tipo") or "").strip().upper()
    if tipo not in TIPOS_PESSOA:
        raise ErroApi("Tipo inválido. Use " + " ou ".join(TIPOS_PESSOA) + ".")
    razao = norm_nome(corpo.get("razao_social"))
    if not razao:
        raise ErroApi("Informe a razão social / nome.")
    documento = so_digitos(corpo.get("documento")) or None
    if documento and len(documento) not in (11, 14):
        raise ErroApi("CPF deve ter 11 dígitos e CNPJ 14 dígitos.")
    return tipo, razao, norm_nome(corpo.get("nome_fantasia")), documento


def _verificar_pessoa_duplicada(db, tipo, razao, documento, ignorar_id=None):
    stmt = select(Pessoa).where(Pessoa.tipo == tipo)
    stmt = stmt.where(Pessoa.documento == documento) if documento \
        else stmt.where(Pessoa.razao_social == razao)
    if ignorar_id:
        stmt = stmt.where(Pessoa.id != ignorar_id)
    existente = db.scalars(stmt.limit(1)).first()
    if existente:
        raise ErroApi(f"Já existe um cadastro igual (ID {existente.id}, {existente.status}).", 409)


@bp.get("/pessoas")
def listar_pessoas():
    with get_session() as db:
        stmt = _comando_lista(select(Pessoa), Pessoa,
                              [Pessoa.razao_social, Pessoa.nome_fantasia, Pessoa.tipo],
                              Pessoa.documento)
        return jsonify([] if stmt is None else [_pessoa_json(p) for p in db.scalars(stmt)])


@bp.post("/pessoas")
def criar_pessoa():
    tipo, razao, fantasia, documento = _dados_pessoa(_corpo())
    with get_session() as db:
        _verificar_pessoa_duplicada(db, tipo, razao, documento)
        pessoa = Pessoa(tipo=tipo, razao_social=razao, nome_fantasia=fantasia,
                        documento=documento, status=STATUS_ATIVO)  # status sempre ATIVO
        db.add(pessoa)
        db.commit()
        return jsonify(_pessoa_json(pessoa)), 201


@bp.put("/pessoas/<int:id_>")
def editar_pessoa(id_):
    tipo, razao, fantasia, documento = _dados_pessoa(_corpo())
    with get_session() as db:
        pessoa = db.get(Pessoa, id_)
        if pessoa is None:
            raise ErroApi("Pessoa não encontrada.", 404)
        _verificar_pessoa_duplicada(db, tipo, razao, documento, ignorar_id=id_)
        pessoa.tipo, pessoa.razao_social = tipo, razao
        pessoa.nome_fantasia, pessoa.documento = fantasia, documento  # status não muda
        db.commit()
        return jsonify(_pessoa_json(pessoa))


@bp.delete("/pessoas/<int:id_>")
def inativar_pessoa(id_):  # exclusão LÓGICA
    return jsonify(_pessoa_json(_mudar_status(Pessoa, id_, STATUS_INATIVO, "Pessoa")))


@bp.post("/pessoas/<int:id_>/reativar")
def reativar_pessoa(id_):
    return jsonify(_pessoa_json(_mudar_status(Pessoa, id_, STATUS_ATIVO, "Pessoa")))


# ---------------------------------------------------------------- classificações
def _classificacao_json(c):
    return {"id": c.id, "tipo": c.tipo, "descricao": c.descricao, "status": c.status}


def _dados_classificacao(corpo):
    tipo = str(corpo.get("tipo") or "").strip().upper()
    if tipo not in TIPOS_CLASSIFICACAO:
        raise ErroApi("Tipo inválido. Use " + " ou ".join(TIPOS_CLASSIFICACAO) + ".")
    descricao = norm_nome(corpo.get("descricao"))
    if not descricao:
        raise ErroApi("Informe a descrição.")
    return tipo, descricao


def _verificar_classificacao_duplicada(db, tipo, descricao, ignorar_id=None):
    stmt = select(Classificacao).where(Classificacao.tipo == tipo,
                                       Classificacao.descricao == descricao)
    if ignorar_id:
        stmt = stmt.where(Classificacao.id != ignorar_id)
    existente = db.scalars(stmt.limit(1)).first()
    if existente:
        raise ErroApi(f"Já existe uma classificação igual (ID {existente.id}, {existente.status}).", 409)


@bp.get("/classificacoes")
def listar_classificacoes():
    with get_session() as db:
        stmt = _comando_lista(select(Classificacao), Classificacao,
                              [Classificacao.descricao, Classificacao.tipo])
        return jsonify([] if stmt is None else [_classificacao_json(c) for c in db.scalars(stmt)])


@bp.post("/classificacoes")
def criar_classificacao():
    tipo, descricao = _dados_classificacao(_corpo())
    with get_session() as db:
        _verificar_classificacao_duplicada(db, tipo, descricao)
        c = Classificacao(tipo=tipo, descricao=descricao, status=STATUS_ATIVO)
        db.add(c)
        db.commit()
        return jsonify(_classificacao_json(c)), 201


@bp.put("/classificacoes/<int:id_>")
def editar_classificacao(id_):
    tipo, descricao = _dados_classificacao(_corpo())
    with get_session() as db:
        c = db.get(Classificacao, id_)
        if c is None:
            raise ErroApi("Classificação não encontrada.", 404)
        _verificar_classificacao_duplicada(db, tipo, descricao, ignorar_id=id_)
        c.tipo, c.descricao = tipo, descricao
        db.commit()
        return jsonify(_classificacao_json(c))


@bp.delete("/classificacoes/<int:id_>")
def inativar_classificacao(id_):
    return jsonify(_classificacao_json(_mudar_status(Classificacao, id_, STATUS_INATIVO, "Classificação")))


@bp.post("/classificacoes/<int:id_>/reativar")
def reativar_classificacao(id_):
    return jsonify(_classificacao_json(_mudar_status(Classificacao, id_, STATUS_ATIVO, "Classificação")))


# ---------------------------------------------------------------- contas (movimentos)
def _conta_json(m):
    return {
        "id": m.id, "tipo": m.tipo, "numero_documento": m.numero_documento,
        "data_emissao": fmt_data(m.data_emissao),
        "fornecedor": m.pessoa.razao_social, "faturado": m.faturado.razao_social,
        "valor_total": float(m.valor_total),
        "parcelas": [{"identificacao": p.identificacao,
                      "data_vencimento": fmt_data(p.data_vencimento),
                      "valor": float(p.valor)} for p in m.parcelas],
        "despesas": [c.descricao for c in m.classificacoes],
        "descricao_produtos": m.descricao, "status": m.status,
    }


@bp.get("/contas")
def listar_contas():
    faturado = aliased(Pessoa)
    with get_session() as db:
        stmt = (select(MovimentoContas)
                .join(Pessoa, MovimentoContas.pessoa_id == Pessoa.id)
                .join(faturado, MovimentoContas.faturado_id == faturado.id)
                .options(selectinload(MovimentoContas.pessoa),
                         selectinload(MovimentoContas.faturado),
                         selectinload(MovimentoContas.parcelas),
                         selectinload(MovimentoContas.classificacoes)))
        stmt = _comando_lista(stmt, MovimentoContas,
                              [MovimentoContas.numero_documento, MovimentoContas.tipo,
                               MovimentoContas.descricao, Pessoa.razao_social,
                               faturado.razao_social])
        return jsonify([] if stmt is None else [_conta_json(m) for m in db.scalars(stmt)])


def _conta_por_id(id_):
    with get_session() as db:
        return _conta_json(db.scalars(
            select(MovimentoContas).where(MovimentoContas.id == id_).options(
                selectinload(MovimentoContas.pessoa), selectinload(MovimentoContas.faturado),
                selectinload(MovimentoContas.parcelas),
                selectinload(MovimentoContas.classificacoes))).one())


@bp.delete("/contas/<int:id_>")
def inativar_conta(id_):
    _mudar_status(MovimentoContas, id_, STATUS_INATIVO, "Conta")
    return jsonify(_conta_por_id(id_))


@bp.post("/contas/<int:id_>/reativar")
def reativar_conta(id_):
    _mudar_status(MovimentoContas, id_, STATUS_ATIVO, "Conta")
    return jsonify(_conta_por_id(id_))
