#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_bots.py - Teste automatizado do protocolo (extra).

Sobe N "bots" que falam o protocolo IMPOSTOR/1.0 diretamente via socket TCP e
jogam partidas completas contra um servidor já em execução. Serve para:
  * validar o fluxo de mensagens de ponta a ponta;
  * demonstrar o servidor ao professor sem precisar de várias pessoas;
  * exercitar o caminho "impostor descoberto -> palpite" de forma determinística.

Uso (com o servidor rodando em outro terminal):
    python3 test_bots.py --players 4 --games 3
    python3 test_bots.py --players 5 --games 2 --vote-from-round 3
    python3 test_bots.py --players 4 --catch                # impostor sempre descoberto, erra o palpite
    python3 test_bots.py --players 4 --catch --guess-right  # impostor descoberto, acerta (EMPATE)

As opções --catch/--guess-right "trapaceiam" de propósito: os bots compartilham
entre si (em memória) quem é o impostor e qual é a palavra, o que um jogador
real jamais poderia fazer. É apenas um recurso de teste.
"""

import argparse
import json
import random
import socket
import threading
import time

PROTOCOL = "IMPOSTOR/1.0"
RESULTS = []                  # resultados de end_game vistos por bot
LOCK = threading.Lock()
SHARED = {"impostor": None, "word": None}   # só para o modo de teste "trapaceiro"


def bot(name, host, port, games, strategy, nplayers):
    s = socket.create_connection((host, port))
    f = s.makefile("r", encoding="utf-8", newline="\n")

    def send(type_, **data):
        s.sendall((json.dumps({"type": type_, "data": data}, ensure_ascii=False) + "\n").encode())

    send("player_join", name=name, protocol=PROTOCOL)
    role = {}
    played = 0
    for line in f:
        m = json.loads(line)
        t, d = m["type"], m["data"]
        if t == "join_rejected":
            print("[%s] REJEITADO: %s" % (name, d["reason"]))
            return
        if t == "lobby_update":
            # Fica "pronto" quando a sala tem pelo menos N jogadores (inclui humanos que
            # entrarem junto) - vale para a 1ª partida e para as revanches.
            me = [p for p in d["players"] if p["name"] == name]
            if len(d["players"]) >= nplayers and me and not me[0]["ready"]:
                send("player_ready")
        elif t == "role_assign":
            role = d
            with LOCK:
                if d["role"] == "IMPOSTOR":
                    SHARED["impostor"] = name
                else:
                    SHARED["word"] = d["word"]
        elif t == "turn_ask" and d["asker"] == name:
            send("send_question", text="%s: isso é comum no dia a dia?" % name)
        elif t == "turn_answer" and d["responder"] == name:
            send("send_answer", text="Depende, mas conheço bem (%s)." % name)
        elif t == "ask_continue":
            # Na 1ª oferta, escolhe 'continue' para testar rodada extra.
            send("send_continue_vote", choice="continue" if d["round"] < strategy["vote_from_round"] else "vote")
        elif t == "start_vote":
            others = [c for c in d["candidates"] if c != name]
            if strategy["catch"] and SHARED["impostor"] in others:
                target = SHARED["impostor"]          # todos votam no impostor
            else:
                target = random.choice(others)
            send("send_vote", target=target)
        elif t == "request_guess" and d["impostor"] == name:
            word = SHARED["word"].lower() if strategy["guess_right"] else "banana"
            send("send_guess", word=word)
        elif t == "end_game":
            with LOCK:
                RESULTS.append((name, d))
            played += 1
            if played >= games:
                send("player_quit")
                return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--players", type=int, default=4)
    ap.add_argument("--games", type=int, default=1)
    ap.add_argument("--vote-from-round", type=int, default=2,
                    help="rodada a partir da qual os bots votam 'vote'")
    ap.add_argument("--catch", action="store_true",
                    help="(teste) todos os civis votam no impostor")
    ap.add_argument("--guess-right", action="store_true",
                    help="(teste) o impostor acerta a palavra (usa --catch para chegar lá)")
    args = ap.parse_args()

    strategy = {"vote_from_round": args.vote_from_round,
                "catch": args.catch, "guess_right": args.guess_right}
    names = ["Bot%d" % i for i in range(1, args.players + 1)]
    threads = [threading.Thread(target=bot, args=(n, args.host, args.port, args.games, strategy, args.players))
               for n in names]
    for t in threads:
        t.start()
        time.sleep(0.05)
    for t in threads:
        t.join(timeout=120 * args.games)

    seen = {}
    for name, d in RESULTS:
        seen[(d["impostor"], d["word"], d["result"])] = d["message"]
    print("\n===== RESULTADOS =====")
    for (imp, word, res), msg in seen.items():
        print("impostor=%s palavra=%s resultado=%s\n   %s" % (imp, word, res, msg))
    print("Bots que concluíram: %d de %d (x%d jogos)" %
          (len(set(n for n, _ in RESULTS)), args.players, args.games))


if __name__ == "__main__":
    main()