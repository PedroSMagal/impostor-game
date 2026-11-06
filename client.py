#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
 client.py - Cliente de terminal do jogo multiplayer "IMPOSTOR" (TCP)
==============================================================================

Funcionamento
-------------
Duas threads trabalham em paralelo:

  * Thread de RECEPÇÃO (receive_loop): fica bloqueada lendo linhas JSON do
    socket. Cada evento do servidor atualiza o estado local e imprime a
    novidade na tela, sem esperar o usuário digitar.
  * Thread PRINCIPAL (input_loop): lê o que o usuário digita e, conforme o
    estado local (self.state) e a ação esperada (self.pending), converte o
    texto em um evento do protocolo e o envia ao servidor.

O cliente é "burro" de propósito: quem decide de quem é a vez é SEMPRE o
servidor. O cliente só habilita a digitação quando recebe o convite
(turn_ask, turn_answer, ask_continue, start_vote, request_guess).

Uso:
    python3 client.py --host 127.0.0.1 --port 5000 --name Ana

Somente biblioteca padrão.
"""

import argparse
import json
import os
import socket
import sys
import threading
import time

PROTOCOL_VERSION = "IMPOSTOR/1.0"

# Texto do prompt conforme a ação esperada do usuário
PROMPTS = {
    "ask": "Digite sua PERGUNTA",
    "answer": "Digite sua RESPOSTA",
    "continue": "[v] votar no impostor  |  [c] jogar mais uma rodada",
    "vote": "Em quem você vota? (número ou nome)",
    "guess": "Qual é a PALAVRA SECRETA?",
}

AJUDA = """
Comandos (valem a qualquer momento):
  /ajuda   mostra esta ajuda
  /ping    mede a latência até o servidor
  /sair    sai do jogo
No lobby, digite  pronto  para indicar que está pronto para começar.
Nos demais momentos, apenas responda quando o jogo pedir (seta  ->).
"""


class ImpostorClient:
    # Eventos que NÃO cancelam a ação pendente do usuário (são só informativos)
    PASSIVE = {"info", "error", "pong", "lobby_update", "vote_progress"}

    def __init__(self, host, port, name):
        self.host, self.port, self.name = host, port, name
        self.sock = None
        self.rfile = None
        self.send_lock = threading.Lock()
        self.print_lock = threading.Lock()
        self.state_lock = threading.Lock()

        # Estado local do cliente (espelha o estado do servidor)
        self.state = "CONNECTING"   # CONNECTING, WAIT_PLAYERS, IN_GAME, DECIDING,
                                    # VOTING, GUESSING, END_GAME
        self.pending = None         # ask | answer | continue | vote | guess | None
        self.last_pending = None    # para reabrir o prompt após erro de validação
        self.candidates = []        # candidatos da votação
        self.role = None
        self.closing = False

    # ------------------------------------------------------------------
    # Saída no terminal (thread-safe)
    # ------------------------------------------------------------------
    def out(self, text=""):
        with self.print_lock:
            print(text, flush=True)

    def prompt(self, text):
        with self.print_lock:
            print("  -> %s: " % text, end="", flush=True)

    def set_pending(self, kind):
        with self.state_lock:
            self.pending = kind
        self.prompt(PROMPTS[kind])

    # ------------------------------------------------------------------
    # Rede
    # ------------------------------------------------------------------
    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        self.sock.settimeout(None)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.rfile = self.sock.makefile("r", encoding="utf-8", newline="\n")
        # Handshake de aplicação: o primeiro evento é sempre player_join.
        self.send("player_join", name=self.name, protocol=PROTOCOL_VERSION)

    def send(self, type_, **data):
        payload = json.dumps({"type": type_, "data": data}, ensure_ascii=False) + "\n"
        try:
            with self.send_lock:
                self.sock.sendall(payload.encode("utf-8"))
        except OSError:
            self.out("[!] Falha ao enviar: conexão perdida.")
            self.shutdown()

    def shutdown(self):
        """Encerra o programa. os._exit evita que o processo fique preso na
        thread principal, bloqueada em input(), quando o servidor cai."""
        if self.closing:
            return
        self.closing = True
        try:
            self.sock.close()
        except (OSError, AttributeError):
            pass
        sys.stdout.flush()
        os._exit(0)

    def receive_loop(self):
        try:
            for line in self.rfile:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    self.handle(msg["type"], msg.get("data", {}))
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue          # ignora mensagem malformada
        except (OSError, ValueError):
            pass
        self.out("\n[!] Conexão com o servidor encerrada.")
        self.shutdown()

    # ------------------------------------------------------------------
    # Tratamento dos eventos do servidor
    # ------------------------------------------------------------------
    def handle(self, type_, d):
        if type_ not in self.PASSIVE:
            with self.state_lock:
                self.pending = None       # o servidor seguiu em frente
        handler = getattr(self, "on_" + type_, None)
        if handler:
            handler(d)

    def on_welcome(self, d):
        self.name = d["name"]
        self.state = "WAIT_PLAYERS"
        self.out("\n=== Conectado como '%s' (protocolo %s) ===" % (self.name, d["protocol"]))
        self.out("Sala: mínimo %d, máximo %d jogadores. /ajuda para comandos."
                 % (d["min_players"], d["max_players"]))

    def on_join_rejected(self, d):
        self.out("\n[X] Entrada recusada: %s" % d["reason"])
        self.shutdown()

    def on_lobby_update(self, d):
        lista = ", ".join("%s%s" % (p["name"], " [PRONTO]" if p["ready"] else "")
                          for p in d["players"])
        self.out("\n* %s" % d["notice"])
        self.out("  Jogadores (%d/%d): %s" % (len(d["players"]), d["max_players"], lista))
        if self.state == "WAIT_PLAYERS":
            me_ready = any(p["name"] == self.name and p["ready"] for p in d["players"])
            if not me_ready:
                self.prompt("Digite 'pronto' para começar")

    def on_state_change(self, d):
        self.state = d["state"]

    def on_start_game(self, d):
        self.out("\n" + "=" * 60)
        self.out(" A PARTIDA COMEÇOU!   Jogadores: %s" % ", ".join(d["players"]))
        self.out(" TEMA: %s" % d["theme"])
        self.out(" Votação será oferecida após %d rodada(s) (máx. %d)."
                 % (d["rounds_before_vote"], d["max_rounds"]))
        self.out("=" * 60)

    def on_role_assign(self, d):
        self.role = d["role"]
        if d["role"] == "IMPOSTOR":
            self.out("\n>>> VOCÊ É O IMPOSTOR! <<<")
            self.out("    Tema: %s   |   Palavra: (você não sabe!)" % d["theme"])
            self.out("    Disfarce-se: responda de forma convincente sem conhecer a palavra.")
        else:
            self.out("\n>>> Você é um JOGADOR NORMAL.")
            self.out("    Tema: %s   |   Palavra secreta: %s" % (d["theme"], d["word"]))
            self.out("    Descubra quem não conhece a palavra (sem entregá-la de bandeja!).")

    def on_round_start(self, d):
        self.out("\n----- RODADA %d  (ordem: %s) -----" % (d["round"], " > ".join(d["order"])))

    def on_turn_ask(self, d):
        if d["asker"] == self.name:
            self.out("\n*** SUA VEZ! Faça UMA pergunta sobre a palavra para TODOS os outros. (turno %d/%d)"
                     % (d["turn"], d["turns"]))
            self.out("    Quem vai responder, em ordem: %s" % ", ".join(d["responders"]))
            self.set_pending("ask")
        else:
            self.out("\n[turno %d/%d] Vez de %s fazer uma pergunta para todos..."
                     % (d["turn"], d["turns"], d["asker"]))

    def on_broadcast_question(self, d):
        self.out("  [PERGUNTA] %s (para todos): %s" % (d["asker"], d["text"]))

    def on_turn_answer(self, d):
        if d["responder"] == self.name:
            self.out("\n*** Sua vez de RESPONDER (%d/%d) à pergunta de %s: \"%s\""
                     % (d["position"], d["total"], d["asker"], d["question"]))
            self.set_pending("answer")
        else:
            self.out("  Aguardando %s responder (%d/%d)..." % (d["responder"], d["position"], d["total"]))

    def on_broadcast_answer(self, d):
        self.out("  [RESPOSTA] %s: %s" % (d["responder"], d["text"]))

    def on_turn_timeout(self, d):
        motivo = "desconectou" if d["reason"] == "disconnected" else "não respondeu a tempo"
        self.out("  [!] %s %s (%s). Turno ignorado." % (d["player"], motivo, d["action"]))

    def on_round_end(self, d):
        self.out("----- fim da rodada %d -----" % d["round"])

    def on_ask_continue(self, d):
        self.out("\n" + "#" * 60)
        self.out(" %s" % d["message"])
        self.out(" (maioria absoluta precisa escolher votar; empate = mais uma rodada)")
        self.out("#" * 60)
        self.set_pending("continue")

    def on_continue_result(self, d):
        self.out("\nResultado: votar=%d | continuar=%d | abstenções=%d  =>  %s"
                 % (d["votes_vote"], d["votes_continue"], d["abstentions"],
                    "VAMOS VOTAR!" if d["decision"] == "vote" else "mais uma rodada"))

    def on_start_vote(self, d):
        self.candidates = [c for c in d["candidates"] if c != self.name]
        self.out("\n=== VOTAÇÃO === Quem é o Impostor?")
        for i, c in enumerate(self.candidates, 1):
            self.out("   %d) %s" % (i, c))
        self.set_pending("vote")

    def on_vote_progress(self, d):
        self.out("  (votos recebidos: %d/%d)" % (d["received"], d["total"]))

    def on_vote_result(self, d):
        self.out("\n--- Votos de cada jogador ---")
        for voter, target in d["ballot"].items():
            self.out("   %s votou em %s" % (voter, target))
        self.out("Contagem: %s" % (", ".join("%s=%d" % kv for kv in d["tally"].items()) or "nenhum voto"))
        if d["tie"]:
            self.out("Empate: ninguém foi o mais votado.")
        else:
            self.out("Mais votado: %s" % d["accused"])

    def on_request_guess(self, d):
        if d["impostor"] == self.name:
            self.out("\n!!! Você foi descoberto, mas ainda pode EMPATAR!")
            self.out("    Adivinhe a palavra secreta do tema %s." % d["theme"])
            self.set_pending("guess")
        else:
            self.out("\nO Impostor (%s) foi descoberto e agora tenta adivinhar a palavra..." % d["impostor"])

    def on_guess_result(self, d):
        palpite = d["guess"] if d["guess"] else "(nenhum palpite)"
        self.out("Palpite do Impostor: %s  =>  %s" % (palpite, "ACERTOU!" if d["correct"] else "ERROU!"))

    def on_end_game(self, d):
        self.out("\n" + "=" * 60)
        self.out(" FIM DE JOGO: %s" % d["result"])
        self.out(" %s" % d["message"])
        self.out(" Impostor: %s | Tema: %s | Palavra: %s" % (d["impostor"], d["theme"], d["word"]))
        self.out("=" * 60)

    def on_info(self, d):
        self.out("  [i] %s" % d["message"])

    def on_pong(self, d):
        if d.get("t"):
            self.out("  [ping] %.1f ms" % ((time.time() - d["t"]) * 1000))

    def on_error(self, d):
        self.out("\n  [ERRO %s] %s" % (d["code"], d["message"]))
        # Entrada inválida: o servidor continua esperando; reabrimos o prompt.
        if d["code"] == "INVALID_INPUT" and self.last_pending:
            with self.state_lock:
                restore = self.pending is None
            if restore:
                self.set_pending(self.last_pending)

    # ------------------------------------------------------------------
    # Entrada do usuário
    # ------------------------------------------------------------------
    def input_loop(self):
        while True:
            try:
                line = input().strip()
            except (EOFError, KeyboardInterrupt):
                self.send("player_quit")
                self.shutdown()
                return
            if line:
                self.handle_input(line)

    def handle_input(self, line):
        low = line.lower()
        if low in ("/sair", "/quit"):
            self.send("player_quit")
            self.out("Saindo...")
            self.shutdown()
            return
        if low in ("/ajuda", "/help"):
            self.out(AJUDA)
            return
        if low == "/ping":
            self.send("ping", t=time.time())
            return

        with self.state_lock:
            pending, state = self.pending, self.state

        if pending is None:
            if state == "WAIT_PLAYERS" and low in ("pronto", "/pronto", "ready"):
                self.send("player_ready")
            elif state == "WAIT_PLAYERS":
                self.prompt("Digite 'pronto' para começar")
            else:
                self.out("  (aguarde: ainda não é a sua vez de agir)")
            return

        self.last_pending = pending
        if pending == "ask":
            self._consume(); self.send("send_question", text=line)
        elif pending == "answer":
            self._consume(); self.send("send_answer", text=line)
        elif pending == "guess":
            self._consume(); self.send("send_guess", word=line)
        elif pending == "continue":
            choice = {"v": "vote", "votar": "vote", "vote": "vote",
                      "c": "continue", "continuar": "continue", "continue": "continue"}.get(low)
            if not choice:
                self.prompt(PROMPTS["continue"])
                return
            self._consume(); self.send("send_continue_vote", choice=choice)
        elif pending == "vote":
            target = None
            if line.isdigit() and 1 <= int(line) <= len(self.candidates):
                target = self.candidates[int(line) - 1]
            else:
                for c in self.candidates:
                    if c.casefold() == low:
                        target = c
            if target is None:
                self.out("  Opção inválida.")
                self.prompt(PROMPTS["vote"])
                return
            self._consume(); self.send("send_vote", target=target)
            self.out("  Voto enviado. Aguardando os demais...")

    def _consume(self):
        with self.state_lock:
            self.pending = None

    # ------------------------------------------------------------------
    def run(self):
        try:
            self.connect()
        except OSError as exc:
            print("Não foi possível conectar a %s:%d -> %s" % (self.host, self.port, exc))
            sys.exit(1)
        threading.Thread(target=self.receive_loop, daemon=True).start()
        self.input_loop()


def main():
    # Garante UTF-8 no terminal (acentos) mesmo em locales antigos.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Cliente do jogo IMPOSTOR (TCP)")
    ap.add_argument("--host", default="127.0.0.1", help="IP/host do servidor")
    ap.add_argument("--port", type=int, default=5000, help="porta do servidor")
    ap.add_argument("--name", help="seu apelido (1-15 letras/números/_/-)")
    args = ap.parse_args()

    name = args.name or input("Seu nome: ").strip()
    ImpostorClient(args.host, args.port, name).run()


if __name__ == "__main__":
    main()
