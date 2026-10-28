# main.py
import json
import os
import re
import subprocess
import time
import urllib.request
import uuid

from flask import Flask, render_template, request, jsonify

from pydantic import ValidationError

from agents.agent1 import Agent1
from agents.agent1.consulta_dados import analisar_nota
from agents.agent1.lancamento_dados import RegraDeNegocio, lancar_nota
from agents.agent1.manipulacao_dados import NotaFiscal
from auth import bp as auth_bp
from cadastros import bp as cadastros_bp
from database import get_session, info_banco, mascarar

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.json.sort_keys = False  # mantém a ordem dos campos do JSON (Flask ordena por padrão)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

# --- login: o cookie de sessão é assinado com SECRET_KEY (definir na hospedagem)
app.secret_key = os.environ.get("SECRET_KEY") or uuid.uuid4().hex
if not os.environ.get("SECRET_KEY"):
    print("AVISO: defina SECRET_KEY; sem ela todos saem do sistema a cada reinício.")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("RENDER"))  # HTTPS no Render

app.register_blueprint(auth_bp)
app.register_blueprint(cadastros_bp)

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Repositório no formato "usuario/repositorio" (definir na hospedagem)
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()
_cache_commits = {"quando": 0, "dados": None}


# ---------------------------------------------------------------- chave
def obter_chave() -> str:
    """Chave enviada pelo usuário na tela (tem prioridade) ou, se não vier,
    a GEMINI_API_KEY configurada no servidor. Nunca é gravada nem logada."""
    return (request.headers.get("X-Gemini-Key", "").strip()
            or os.environ.get("GEMINI_API_KEY", "").strip())


# ---------------------------------------------------------------- versionamento
def _commit_completo():
    """Hash completo do commit que está rodando (variável da hospedagem ou git local)."""
    for var in ("RENDER_GIT_COMMIT", "VERCEL_GIT_COMMIT_SHA", "SOURCE_VERSION",
                "HEROKU_SLUG_COMMIT", "GIT_COMMIT"):
        if os.environ.get(var):
            return os.environ[var].strip()
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=BASE_DIR,
            stderr=subprocess.DEVNULL, timeout=3)
        return out.decode().strip()
    except Exception:
        return None


def _buscar_commits_github(repo: str, limite: int = 10) -> list:
    url = f"https://api.github.com/repos/{repo}/commits?per_page={limite}"
    headers = {"User-Agent": "izi-contas", "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):  # opcional (repositório privado / limite de uso)
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=8) as r:
        itens = json.load(r)
    return [{
        "sha": c["sha"][:7],
        "sha_completo": c["sha"],
        "mensagem": c["commit"]["message"].split("\n")[0],
        "autor": c["commit"]["author"]["name"],
        "data": c["commit"]["author"]["date"],
        "url": c["html_url"],
    } for c in itens]


@app.route("/versao")
def versao():
    # no Render, o repositório também vem de RENDER_GIT_REPO_SLUG (usuario/repositorio)
    repo = GITHUB_REPO or os.environ.get("RENDER_GIT_REPO_SLUG", "").strip()
    completo = _commit_completo()
    resp = {
        "repositorio": repo or None,
        "url": f"https://github.com/{repo}" if repo else None,
        "commit_em_execucao": completo[:7] if completo else None,
        "commit_completo": completo,
        "ultimo_commit_github": None,
        "atualizado": None,  # True quando a versão no ar é igual ao último commit do GitHub
        "chave_no_servidor": bool(os.environ.get("GEMINI_API_KEY")),
        "commits": [],
        "erro": None,
    }
    if not repo:
        resp["erro"] = "Variável GITHUB_REPO não configurada (ex.: usuario/repositorio)."
        return jsonify(resp)
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        resp["erro"] = "GITHUB_REPO inválido. Use o formato usuario/repositorio."
        return jsonify(resp)

    commits = None
    # cache de 60s para não estourar o limite da API do GitHub
    if _cache_commits["dados"] is not None and time.time() - _cache_commits["quando"] < 60:
        commits = _cache_commits["dados"]
    else:
        try:
            commits = _buscar_commits_github(repo)
            _cache_commits.update(quando=time.time(), dados=commits)
        except Exception as exc:
            commits = _cache_commits["dados"]  # se der erro, usa a última lista boa (se houver)
            if commits is None:
                resp["erro"] = f"Não foi possível consultar o GitHub: {exc}"

    if commits:
        resp["commits"] = commits
        resp["ultimo_commit_github"] = commits[0]["sha"]
        if completo:
            resp["atualizado"] = commits[0]["sha_completo"] == completo
    return jsonify(resp)


# ---------------------------------------------------------------- rotas
@app.errorhandler(413)
def arquivo_grande(_):
    return jsonify({"erro": "Arquivo muito grande (máximo 10 MB)."}), 413


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/cadastros")
def cadastros():
    return render_template("cadastros.html")


def _ler_nota():
    """Lê o JSON da extração enviado pela tela e confere se está no formato esperado."""
    corpo = request.get_json(silent=True)
    if not isinstance(corpo, dict):
        return None, (jsonify({"erro": "Envie o JSON extraído da nota fiscal."}), 400)
    try:
        return NotaFiscal.model_validate(corpo).model_dump(mode="json"), None
    except ValidationError:
        return None, (jsonify({"erro": "O JSON da nota está fora do formato esperado."}), 400)


def _erro_banco(exc):
    return jsonify({"erro": "Banco de dados indisponível ou com erro: "
                            + mascarar(str(exc)).strip().split("\n")[0][:200]}), 500


@app.route("/analisar", methods=["POST"])
def analisar():
    """2ª etapa: consulta o banco e informa se fornecedor, faturado e despesa existem."""
    nota, erro = _ler_nota()
    if erro:
        return erro
    try:
        with get_session() as db:
            resultado = analisar_nota(db, nota)
    except Exception as exc:
        return _erro_banco(exc)
    resultado["banco"] = info_banco()
    return jsonify(resultado)


@app.route("/lancar", methods=["POST"])
def lancar():
    """2ª etapa: cria o que faltar (fornecedor, faturado, despesa) e lança o movimento + parcela."""
    nota, erro = _ler_nota()
    if erro:
        return erro
    try:
        with get_session() as db:
            return jsonify(lancar_nota(db, nota)), 201
    except RegraDeNegocio as exc:
        return jsonify({"erro": " ".join(exc.mensagens), "mensagens": exc.mensagens}), 422
    except Exception as exc:
        return _erro_banco(exc)


@app.route("/extrair", methods=["POST"])
def extrair():
    chave = obter_chave()
    if not chave:
        return jsonify({"erro": "Informe a chave da API do Gemini no campo da tela."}), 400

    file = request.files.get("nota_fiscal")
    if not file or file.filename == "":
        return jsonify({"erro": "Nenhum arquivo enviado."}), 400

    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"erro": "O arquivo precisa ser um PDF."}), 400

    # nome aleatório: evita conflito entre uploads e nomes maliciosos
    file_path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}.pdf")
    file.save(file_path)

    try:
        dados = Agent1(api_key=chave).extrair_dados(file_path)
    except Exception as exc:
        # a chave nunca deve aparecer na resposta
        return jsonify({"erro": str(exc).replace(chave, "***")}), 500
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

    return jsonify(dados)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
