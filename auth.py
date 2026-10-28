# auth.py
#
# Login e senha do IZI Contas.
#   - Usuários ficam na tabela "usuarios" (senha só como hash).
#   - Todas as telas e rotas exigem login, menos /login.
#   - O primeiro usuário pode nascer das variáveis ADMIN_USER e ADMIN_PASSWORD
#     (útil no Render gratuito, que não tem terminal) ou do criar_usuario.py.
import os
import time

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   session, url_for)
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from database import Usuario, get_session

bp = Blueprint("auth", __name__)

ROTAS_LIVRES = {"auth.login", "static"}

# Freio simples contra tentativa de adivinhar senha: 5 erros por IP em 5 minutos.
MAX_FALHAS, JANELA_SEG = 5, 300
_falhas: dict = {}

# Hash de mentira, comparado quando o login não existe (o tempo de resposta
# fica igual e ninguém descobre quais logins existem).
_HASH_FALSO = generate_password_hash("senha-falsa-para-igualar-o-tempo")

_admin_garantido = False


def _ip() -> str:
    # Atrás do proxy do Render, o último item do X-Forwarded-For é o IP real.
    encaminhado = request.headers.get("X-Forwarded-For", "")
    return encaminhado.split(",")[-1].strip() or (request.remote_addr or "?")


def _bloqueado(ip: str) -> bool:
    agora = time.time()
    recentes = [t for t in _falhas.get(ip, []) if agora - t < JANELA_SEG]
    _falhas[ip] = recentes
    return len(recentes) >= MAX_FALHAS


def _destino_seguro(destino) -> str:
    """Só aceita caminhos internos (evita redirecionar para outro site)."""
    if destino and destino.startswith("/") and not destino.startswith("//"):
        return destino
    return "/"


def garantir_admin() -> None:
    """Cria o usuário de ADMIN_USER/ADMIN_PASSWORD se ele ainda não existir."""
    global _admin_garantido
    if _admin_garantido:
        return
    login = os.environ.get("ADMIN_USER", "").strip().lower()
    senha = os.environ.get("ADMIN_PASSWORD", "")
    if login and senha:
        with get_session() as db:
            if not db.scalar(select(Usuario).where(Usuario.login == login)):
                db.add(Usuario(login=login, senha_hash=generate_password_hash(senha)))
                db.commit()
    _admin_garantido = True


@bp.before_app_request
def exigir_login():
    if request.endpoint is None or request.endpoint in ROTAS_LIVRES:
        return None
    if session.get("usuario"):
        return None
    pagina = (request.method == "GET" and not request.path.startswith("/api")
              and request.path != "/versao")
    if pagina:
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
    return jsonify({"erro": "Sessão expirada. Recarregue a página e entre de novo."}), 401


@bp.route("/login", methods=["GET", "POST"])
def login():
    destino = _destino_seguro(request.values.get("next"))
    if request.method == "GET":
        if session.get("usuario"):
            return redirect(destino)
        return render_template("login.html", erro=None, proximo=destino)

    ip = _ip()
    if _bloqueado(ip):
        return render_template("login.html", proximo=destino,
                               erro="Muitas tentativas. Aguarde alguns minutos."), 429

    nome = request.form.get("login", "").strip().lower()
    senha = request.form.get("senha", "")
    try:
        garantir_admin()
        with get_session() as db:
            usuario = db.scalar(select(Usuario).where(Usuario.login == nome))
    except Exception:
        return render_template("login.html", proximo=destino,
                               erro="Banco de dados indisponível. Tente de novo."), 503

    confere = check_password_hash(usuario.senha_hash if usuario else _HASH_FALSO, senha)
    if not (usuario and confere):
        _falhas.setdefault(ip, []).append(time.time())
        return render_template("login.html", proximo=destino,
                               erro="Login ou senha incorretos."), 401

    _falhas.pop(ip, None)
    session.clear()
    session["usuario"] = usuario.login
    return redirect(destino)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
