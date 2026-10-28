# agents/agent1/lancamento_dados.py
#
# 2ª etapa: depois de mostrar a análise, cria o que ainda não existe
# (fornecedor, faturado, despesa) e lança o movimento A PAGAR com a(s) parcela(s).
# Tudo em uma única transação: ou grava tudo, ou não grava nada.
from sqlalchemy.exc import IntegrityError

from database import (CLASSIF_DESPESA, MOV_APAGAR, PESSOA_FATURADO, PESSOA_FORNECEDOR,
                      STATUS_ATIVO, Classificacao, MovimentoContas, ParcelaContas, Pessoa,
                      fmt_data, norm_nome, para_decimal, parse_data, so_digitos)
from .consulta_dados import (analisar_nota, buscar_classificacao, buscar_pessoa,
                             normalizar_parcelas)


class RegraDeNegocio(Exception):
    """Lançamento recusado por uma regra de negócio (vira resposta 422)."""

    def __init__(self, mensagens):
        self.mensagens = [mensagens] if isinstance(mensagens, str) else list(mensagens)
        super().__init__(" ".join(self.mensagens))


def _obter_ou_criar_pessoa(db, tipo, nome, documento, fantasia, rotulo, criados):
    pessoa = buscar_pessoa(db, tipo, documento, nome)
    if pessoa is None:
        pessoa = Pessoa(tipo=tipo, razao_social=nome, nome_fantasia=fantasia,
                        documento=documento or None, status=STATUS_ATIVO)
        db.add(pessoa)
        db.flush()  # gera o ID
        criados.append({"entidade": rotulo, "id": pessoa.id, "nome": nome})
    return pessoa


def lancar_nota(db, nota):
    analise = analisar_nota(db, nota)
    if analise["bloqueios"]:
        raise RegraDeNegocio(analise["bloqueios"])

    fornecedor = nota.get("fornecedor") or {}
    faturado = nota.get("faturado") or {}
    criados = []

    try:
        forn = _obter_ou_criar_pessoa(
            db, PESSOA_FORNECEDOR,
            norm_nome(fornecedor.get("razao_social")), so_digitos(fornecedor.get("cnpj")),
            norm_nome(fornecedor.get("nome_fantasia")), "Fornecedor", criados)
        fat = _obter_ou_criar_pessoa(
            db, PESSOA_FATURADO,
            norm_nome(faturado.get("nome_completo")), so_digitos(faturado.get("cpf")),
            None, "Faturado", criados)

        despesas = []
        for item in analise["despesas"]:
            c = buscar_classificacao(db, CLASSIF_DESPESA, item["descricao"])
            if c is None:
                c = Classificacao(tipo=CLASSIF_DESPESA, descricao=item["descricao"],
                                  status=STATUS_ATIVO)
                db.add(c)
                db.flush()
                criados.append({"entidade": "Despesa", "id": c.id, "nome": c.descricao})
            despesas.append(c)

        numero = nota.get("numero_nota_fiscal")
        movimento = MovimentoContas(
            tipo=MOV_APAGAR,
            pessoa_id=forn.id,
            faturado_id=fat.id,
            numero_documento=str(numero) if numero else None,
            data_emissao=parse_data(nota.get("data_emissao")),
            descricao="\n".join(str(p) for p in (nota.get("descricao_produtos") or [])) or None,
            valor_total=para_decimal(nota.get("valor_total")),
            status=STATUS_ATIVO,
        )
        movimento.classificacoes = despesas
        db.add(movimento)
        db.flush()

        parcelas = []
        for p in normalizar_parcelas(nota):
            parcela = ParcelaContas(
                movimento_id=movimento.id, numero=p["numero"],
                identificacao=p["identificacao"], data_vencimento=p["data_vencimento"],
                valor=p["valor"], status=STATUS_ATIVO)
            db.add(parcela)
            parcelas.append(parcela)
        db.flush()
        db.commit()
    except IntegrityError:
        db.rollback()
        raise RegraDeNegocio(
            "Outro lançamento criou o mesmo cadastro ao mesmo tempo. Tente novamente.")
    except Exception:
        db.rollback()
        raise

    return {
        "mensagem": "Registro lançado com sucesso!",
        "movimento_id": movimento.id,
        "tipo": MOV_APAGAR,
        "valor_total": float(movimento.valor_total),
        "parcelas": [{
            "id": p.id, "numero": p.numero, "identificacao": p.identificacao,
            "data_vencimento": fmt_data(p.data_vencimento), "valor": float(p.valor),
        } for p in parcelas],
        "criados": criados,
        "avisos": analise["avisos"],
    }
