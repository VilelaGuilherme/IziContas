# database.py
#
# Banco de dados do IZI Contas.
#   - Sem DATABASE_URL: usa SQLite local (arquivo izi_contas.db).
#   - Com DATABASE_URL (ex.: PostgreSQL do Render): usa o banco informado.
#
# Modelo (conforme o enunciado da 2ª etapa):
#   PESSOAS            tipo CLIENTE-FORNECEDOR ou FATURADO (uma tabela só)
#   CLASSIFICACAO      tipo DESPESA ou RECEITA
#   MOVIMENTO_CONTAS   tipo APAGAR ou ARECEBER
#   PARCELA_CONTAS     identificação ÚNICA (ou 1/3, 2/3, ...)
#   MOVIMENTO_CLASSIFICACAO  liga um movimento a uma ou mais classificações
#
# Regras de negócio: nada é excluído de verdade. Todo cadastro tem STATUS
# (ATIVO/INATIVO) e pode ser inativado ou reativado.
import os
import re
import threading
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (Column, Date, DateTime, ForeignKey, Integer, Numeric,
                        String, Table, Text, UniqueConstraint, create_engine, event)
from sqlalchemy.orm import (DeclarativeBase, Mapped, mapped_column, relationship,
                            sessionmaker)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATUS_ATIVO = "ATIVO"
STATUS_INATIVO = "INATIVO"

PESSOA_FORNECEDOR = "CLIENTE-FORNECEDOR"
PESSOA_FATURADO = "FATURADO"
TIPOS_PESSOA = (PESSOA_FORNECEDOR, PESSOA_FATURADO)

CLASSIF_DESPESA = "DESPESA"
CLASSIF_RECEITA = "RECEITA"
TIPOS_CLASSIFICACAO = (CLASSIF_DESPESA, CLASSIF_RECEITA)

MOV_APAGAR = "APAGAR"
MOV_ARECEBER = "ARECEBER"

PARCELA_UNICA = "ÚNICA"


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- modelos
class Base(DeclarativeBase):
    pass


movimento_classificacao = Table(
    "movimento_classificacao", Base.metadata,
    Column("movimento_id", ForeignKey("movimento_contas.id"), primary_key=True),
    Column("classificacao_id", ForeignKey("classificacao.id"), primary_key=True),
)


class Pessoa(Base):
    __tablename__ = "pessoas"
    __table_args__ = (UniqueConstraint("tipo", "documento", name="uq_pessoa_tipo_documento"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)  # CLIENTE-FORNECEDOR | FATURADO
    razao_social: Mapped[str] = mapped_column(String(255), nullable=False)
    nome_fantasia: Mapped[str] = mapped_column(String(255), nullable=True)
    documento: Mapped[str] = mapped_column(String(14), nullable=True)  # CNPJ/CPF, só dígitos
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=STATUS_ATIVO)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=_agora)


class Classificacao(Base):
    __tablename__ = "classificacao"
    __table_args__ = (UniqueConstraint("tipo", "descricao", name="uq_classificacao_tipo_descricao"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo: Mapped[str] = mapped_column(String(10), nullable=False)  # DESPESA | RECEITA
    descricao: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=STATUS_ATIVO)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=_agora)


class Usuario(Base):
    """Quem pode entrar no sistema. A senha fica só como hash (nunca em texto puro)."""
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    senha_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=_agora)


class MovimentoContas(Base):
    __tablename__ = "movimento_contas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo: Mapped[str] = mapped_column(String(10), nullable=False)  # APAGAR | ARECEBER
    pessoa_id: Mapped[int] = mapped_column(ForeignKey("pessoas.id"), nullable=False)  # fornecedor/cliente
    faturado_id: Mapped[int] = mapped_column(ForeignKey("pessoas.id"), nullable=False)
    numero_documento: Mapped[str] = mapped_column(String(60), nullable=True)  # nº da nota fiscal
    data_emissao: Mapped[date] = mapped_column(Date, nullable=True)
    descricao: Mapped[str] = mapped_column(Text, nullable=True)  # descrição dos produtos
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=STATUS_ATIVO)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=_agora)

    pessoa = relationship("Pessoa", foreign_keys=[pessoa_id])
    faturado = relationship("Pessoa", foreign_keys=[faturado_id])
    parcelas = relationship("ParcelaContas", back_populates="movimento",
                            order_by="ParcelaContas.numero")
    classificacoes = relationship("Classificacao", secondary=movimento_classificacao)


class ParcelaContas(Base):
    __tablename__ = "parcela_contas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    movimento_id: Mapped[int] = mapped_column(ForeignKey("movimento_contas.id"), nullable=False)
    numero: Mapped[int] = mapped_column(Integer, nullable=False)
    identificacao: Mapped[str] = mapped_column(String(20), nullable=False)  # ÚNICA | 1/3 | 2/3 ...
    data_vencimento: Mapped[date] = mapped_column(Date, nullable=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=STATUS_ATIVO)

    movimento = relationship("MovimentoContas", back_populates="parcelas")


# ---------------------------------------------------------------- conexão
def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///" + os.path.join(BASE_DIR, "izi_contas.db")
    # O Render entrega "postgres://..." ou "postgresql://...". Deixamos o driver
    # explícito (psycopg2), porque o padrão do SQLAlchemy mudou entre versões.
    for prefixo in ("postgres://", "postgresql://"):
        if url.startswith(prefixo):
            return "postgresql+psycopg2://" + url[len(prefixo):]
    return url


def info_banco() -> dict:
    sqlite = database_url().startswith("sqlite")
    return {
        "banco": "SQLite" if sqlite else "PostgreSQL",
        # no Render gratuito o disco é apagado a cada reinício: SQLite perde os dados
        "temporario": bool(sqlite and os.environ.get("RENDER")),
    }


def mascarar(texto: str) -> str:
    """Tira a senha do DATABASE_URL de mensagens de erro."""
    m = re.match(r"^[\w+]+://[^:@/]+:([^@]+)@", os.environ.get("DATABASE_URL", ""))
    return texto.replace(m.group(1), "***") if m and m.group(1) else texto


_lock = threading.Lock()
_engine = None
_Session = None


def _criar_engine():
    url = database_url()
    if url.startswith("sqlite"):
        eng = create_engine(url)

        @event.listens_for(eng, "connect")
        def _ativar_fk(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
        return eng
    return create_engine(url, pool_pre_ping=True, pool_recycle=300)


def get_session():
    """Abre uma sessão. Na primeira vez cria as tabelas que ainda não existem.
    Se o banco estiver fora do ar, só as telas que usam banco falham
    (a extração do PDF continua funcionando) e a próxima chamada tenta de novo."""
    global _engine, _Session
    if _Session is None:
        with _lock:
            if _Session is None:
                eng = _criar_engine()
                Base.metadata.create_all(eng)
                _engine, _Session = eng, sessionmaker(eng, expire_on_commit=False)
    return _Session()


def reiniciar_conexao():
    """Usado nos testes: descarta o engine para trocar de banco."""
    global _engine, _Session
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = _Session = None


# ---------------------------------------------------------------- utilitários
def so_digitos(valor) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def norm_nome(valor):
    """Nome em MAIÚSCULAS, sem espaços sobrando. Vazio vira None."""
    texto = re.sub(r"\s+", " ", str(valor or "").strip()).upper()
    return texto or None


def formata_documento(digitos) -> str:
    d = so_digitos(digitos)
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return d


def rotulo_documento(digitos, padrao="CNPJ") -> str:
    n = len(so_digitos(digitos))
    return "CNPJ" if n == 14 else "CPF" if n == 11 else padrao


def parse_data(texto):
    """DD/MM/AAAA (ou AAAA-MM-DD) -> date. Inválida ou vazia -> None."""
    if not texto:
        return None
    if isinstance(texto, date):
        return texto
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(texto).strip(), formato).date()
        except ValueError:
            pass
    return None


def fmt_data(d) -> str:
    return d.strftime("%d/%m/%Y") if d else None


def para_decimal(valor):
    if valor is None or valor == "":
        return None
    return Decimal(str(valor)).quantize(Decimal("0.01"))
