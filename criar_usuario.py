# criar_usuario.py
#
# Cria um usuário (ou troca a senha de um que já existe).
#   python criar_usuario.py LOGIN
# A senha é pedida no terminal (não aparece na tela nem fica no histórico).
# Usa o mesmo banco do sistema: defina DATABASE_URL antes para mexer no Neon.
import getpass
import sys

from sqlalchemy import select
from werkzeug.security import generate_password_hash

from database import Usuario, get_session

if len(sys.argv) != 2:
    sys.exit("Uso: python criar_usuario.py LOGIN")

login = sys.argv[1].strip().lower()
senha = getpass.getpass("Senha: ")
if len(senha) < 6 or senha != getpass.getpass("Repita a senha: "):
    sys.exit("As senhas não conferem ou têm menos de 6 caracteres.")

with get_session() as db:
    u = db.scalar(select(Usuario).where(Usuario.login == login))
    if u:
        u.senha_hash = generate_password_hash(senha)
        print(f"Senha de '{login}' atualizada.")
    else:
        db.add(Usuario(login=login, senha_hash=generate_password_hash(senha)))
        print(f"Usuário '{login}' criado.")
    db.commit()
