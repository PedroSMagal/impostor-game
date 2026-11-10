#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
 server.py - Servidor do jogo multiplayer "IMPOSTOR" (Cliente-Servidor / TCP)
==============================================================================

Arquitetura
-----------
* Um socket TCP "de escuta" (accept loop) na thread principal.
* UMA THREAD POR CLIENTE (handle_client): lê as mensagens do jogador, valida
  o protocolo e as encaminha ao jogo.
* UMA THREAD DE PARTIDA (_run_game): é o "árbitro". Ela executa a máquina de
  estados do jogo (turnos -> decisão -> votação -> palpite -> fim) e é a
  única que decide de quem é a vez. As threads de cliente apenas depositam
  eventos numa fila (queue.Queue); a thread de partida consome essa fila.
  Assim, não há disputa entre threads pela lógica do jogo.
* Estado compartilhado (lista de jogadores, estado atual) protegido por RLock.

Protocolo de aplicação (resumo; detalhes no readme.md)
-----------------------------------------------------
Mensagens JSON em UTF-8, uma por linha (delimitador '\\n'):
    {"type": "<evento>", "data": {...}}

Uso:
    python3 server.py [--host 0.0.0.0] [--port 5000] [--min-turns 1]
                      [--max-turns 5] [--turn-timeout 60] [--vote-timeout 45]

Somente biblioteca padrão (socket, threading, queue, json, ...).
"""

import argparse
import json
import logging
import queue
import random
import re
import socket
import threading
import time
import unicodedata
from collections import Counter

# --------------------------------------------------------------------------
# Constantes do protocolo / regras de negócio
# --------------------------------------------------------------------------
PROTOCOL_VERSION = "IMPOSTOR/1.0"
MIN_PLAYERS = 3                  # mínimo para iniciar a partida
MAX_PLAYERS = 5                  # capacidade da sala
MAX_LINE = 4096                  # tamanho máximo de uma mensagem (caracteres)
MAX_TEXT = 200                   # tamanho máximo de pergunta/resposta/palpite
HANDSHAKE_TIMEOUT = 30           # segundos para o cliente enviar player_join
NAME_RE = re.compile(r"^[\w\-]{1,15}$")   # nomes: letras/dígitos/_/-, 1 a 15
MAX_SKIN_LEN = 40                # tamanho máximo do nome da skin (nome do arquivo da imagem)

# Estados do servidor (máquina de estados da sala)
WAIT_PLAYERS = "WAIT_PLAYERS"   # lobby: aguardando jogadores e "pronto"
IN_GAME = "IN_GAME"             # turno: cada jogador pergunta a todos, e todos respondem
DECIDING = "DECIDING"           # fim de CADA turno: "Continuar" ou "Votação"? (ao vivo)
VOTING = "VOTING"               # cada jogador vota no suspeito
GUESSING = "GUESSING"           # impostor descoberto tenta adivinhar a palavra
END_GAME = "END_GAME"           # resultado final

# Temas e palavras secretas (o servidor sorteia um tema e uma palavra)
THEMES = {
    "COMIDAS": [
        "HAMBÚRGUER DE SIRI", "LASANHA", "CERVEJA AMANTEIGADA",
        "ESPINAFRE", "COGUMELO", "BISCOITO SCOOBY",
        "ROSQUINHA", "MACARRÃO COM SALSICHA", "SEMENTES DOS DEUSES",
        "TORTA DE MAÇÃ", "POÇÃO POLISSUCO",
        "SANDUÍCHE DE MORTADELA", "CHURROS", "HAMBÚRGUER KRUSTY",
        "AMBROSIA DOS DEUSES", "RAMEN", "FRUTA DO DIABO",
        "MAÇÃ DOURADA", "QUEIJO SUÍÇO", "SORVETE NAPOLITANO",
        "HIDROMEL", "RAÇÃO HUMANA",
        "PÃO DE QUEIJO MÁGICO", "COXINHA DE RODOVIÁRIA", "ACARAJÉ COM PIMENTA"
    ],
    
    "ANIMAIS E PETS": [
        "SCOOBY-DOO", "PIKACHU", "GARFIELD", "CORAGEM", 
        "PERRY O ORNITORRINCO", "EDWIGES", "YOSHI", "PICA-PAU",
        "SONIC", "BURRO", "BANGUELA", "PUMBA",
        "TIMÃO", "APPA", "MOMO", "RAPOSA DE NOVE CAUDAS",
        "GATO DE BOTAS", "TOM", "JERRY", "PERNALONGA",
        "PATOLINO", "TOTORO", "REX", "SNOOPY",
        "BRIAN GRIFFIN", "BOJACK HORSEMAN", "DINO", 
        "CACHORRO", "VIRA-LATA CARAMELO", "GALO CEGO"
    ],
    
    "PROFISSÕES": [
        "CAÇADOR DE RECOMPENSAS", "PROFESSOR DE DEFESA CONTRA AS TREVAS", "ENCANADOR",
        "ENTREGADOR DE PIZZA", "MESTRE POKÉMON", "MESTRE JEDI", "CAÇA-FANTASMAS",
        "FEITICEIRO", "ALQUIMISTA FEDERAL", "DOMINADOR DE ELEMENTOS",
        "FABRICANTE DE CRISTAL", "PILOTO DE ROBO GIGANTE", "NINJA",
        "PRESIDENTE", "GUARDA", "FUNCIONÁRIO DE ESCRITÓRIO",
        "SENHOR DO TEMPO", "XERIFE", "CAÇADOR DE MONSTROS",
        "AGENTE SECRETO", "SEMIDEUS", "OPERADOR",
        "PIRATA", "GUARDIÃO DA GALÁXIA", "VINGADOR",
        "MOTORISTA DE FUGA", "DETETIVE SOBRENATURAL", "AGIOTA",
        "TESTADOR DE COLCHÕES", "JUIZ DE RINHA DE GALO"
    ],
    
    "LUGARES": [
        "FENDA DO BIQUÍNI", "ASILO ARKHAM", "VILA",
        "HOGWARTS", "ACAMPAMENTO MEIO-SANGUE", "ESTRELA DA MORTE", "BAIRRO DO LIMOEIRO",
        "MORDOR", "WAKANDA", "CENTRAL PERK",
        "TAVERNA", "ESCRITÓRIO", "VILA DA FOLHA",
        "NAÇÃO DO FOGO", "ILHA DESERTA", "MUNDO INVERTIDO",
        "BATECAVERNA", "FORTALEZA DA SOLIDÃO", "TARDIS",
        "PORTO REAL", "SPRINGFIELD", "SIRI CASCUDO",
        "CONDADO", "PLANETA NAMEKUSEI", "ASGARD",
        "MONTE OLIMPO", "ESCOLA DE MAGIA", "CIDADE DE JACKSON",
        "SEATTLE PÓS-APOCALÍPTICA", "SALA PRECISA"
    ],
    
    "OBJETOS": [
        "ANEL DO HOBBIT", "VARINHA DAS VARINHAS", "DEATH NOTE",
        "ESCUDO", "MJOLNIR", "POKÉBOLA",
        "CANIVETE", "SHURIKEN", "CHAPÉU SELETOR",
        "ESPADA DE DIAMANTE", "CAPA DE INVISIBILIDADE", "PÍLULA VERMELHA",
        "COROA", "LÂMPADA MÁGICA", "ESFERAS DO DRAGÃO",
        "TAPETE MÁGICO", "ARMADURA", "LAÇO DA VERDADE",
        "MÁSCARA", "TRIDENTE", "OMNITRIX",
        "CUBO MÁGICO", "ESPELHO MÁGICO", "BATARANG",
        "SABRE DE LUZ", "POKEDEX", "DADO DE VINTE LADOS",
        "VIRA-TEMPO", "MANOPLA DO INFINITO", "BOMBA DE ABÓBORA"
    ],
    
    "VEÍCULOS": [
        "BATMÓVEL", "DELOREAN", "MILLENNIUM FALCON", 
        "VASSOURA VOADORA", "MÁQUINA DE MISTÉRIO",
        "X-WING", "TIE FIGHTER", "PÉROLA NEGRA",
        "CARRO DE PEDRA", "NAVE ESPACIAL", "EXPRESSO",
        "CARRUAGEM", "CARRO VERDE",
        "CAMINHÃO", "HOVERBOARD",
        "CAVALO", "TRENÓ",
        "KOMBI", "UNO COM ESCADA NO TETO", "CELTA REBAIXADO",
        "CARROÇA", "MOTO EM CHAMAS", "FOGUETE"
    ],

    "VILÕES E ANTAGONISTAS": [
        "DARTH VADER", "VOLDEMORT", "CORINGA", "THANOS", "SAURON",
        "FREEZA", "BOWSER", "DOUTOR EGGMAN", 
        "SUKUNA", "PLANKTON", "MACACO LOUCO",
        "EQUIPE ROCKET", "SCAR", "SENHOR BURNS", "DUENDE VERDE",
        "MAGNETO", "LOKI", "REI DA NOITE", "AGENTE SMITH",
        "EXTERMINADOR", "FREDDY KRUEGER", "JASON VOORHEES",
        "PENNYWISE", "VILGAX", "CELL",
        "OROCHIMARU", "LÍDER DOS VAGALUMES", "KRONOS",
        "DARTH MAUL", "MADRASTA MÁ"
    ],

    "SUPERPODERES": [
        "VOAR", "DOMINAR A ÁGUA", "DOMINAR O FOGO", 
        "SOLTAR TEIA", "VISÃO DE CALOR", "FATOR DE CURA",
        "SUPER VELOCIDADE", "FICAR INVISÍVEL", "LER MENTES",
        "TELETRANSPORTE", "SUPER FORÇA", "MAGIA",
        "CONTROLE DO MAGNETISMO", "CLONAGEM DAS SOMBRAS", "RASENGAN",
        "EXPANSÃO DE DOMÍNIO", "SENTIDO ARANHA", "BAFO CONGELANTE",
        "ELASTICIDADE", "FORMA TITÃ", "INVOCAR PATRONO",
        "MUDAR DE FORMA", "RESPIRAR EMBAIXO D'ÁGUA", "CONTROLE DO CLIMA",
        "ENCOLHER", "FICAR GIGANTE", "PARAR O TEMPO",
        "HIPNOSE", "FALAR COM MÁQUINAS", "SER BILIONÁRIO"
    ],

    "MONSTROS E CRIATURAS": [
        "ESTALADOR", "DEMOGORGON", "XENOMORFO",
        "PREDADOR", "GODZILLA", "KING KONG", "VAMPIRO",
        "LOBISOMEM", "MÚMIA", "MONSTRO DE FRANKENSTEIN", "CENTAURO",
        "MINOTAURO", "CICLOPE", "DRAGÃO", "BASILISCO",
        "DEMENTADOR", "CHUPACABRA", "MONSTRO DO LAGO NESS", "PÉ GRANDE",
        "GREMLIN", "SLENDERMAN", "SIREN HEAD", "TIRANOSSAURO REX",
        "TITÃ COLOSSAL", "CÉRBERO", "KRAKEN",
        "BALROG", "SEREIA MALIGNA", "ZUMBI", "GÁRGULA"
    ],

    "MEMES DA INTERNET": [
        "GRÁVIDA DE TAUBATÉ", "LUVA DE PEDREIRO", "CANETA AZUL", 
        "NAZARÉ CONFUSA", "ET BILU", "RONALDINHO GAÚCHO",
        "BAMBU", "MENINA FANTASMA DO ELEVADOR", "DOLLYNHO",
        "ZÉ GOTINHA BOMBADO", "CAZE REAGINDO", "JOELMA BATENDO CABELO",
        "FAUSTÃO DIZENDO ERROU", "VAMPETAÇO", "INÊS BRASIL",
        "CHOQUE DE CULTURA", "GLÓRIA PIRES NO OSCAR", "CAPIVARA",
        "E O PIX NADA AINDA", "FADA DO DENTE CALOTEIRA", "GRETCHEN",
        "VELHO DA LANCHA", "SÓSIA DO NEYMAR", "CACHORRO CARAMELO SORRINDO",
        "BOM DIA DE GRUPO DE FAMÍLIA", "ÁUDIO DO GEMIDÃO", "MENINO DO ACRE",
        "REI DO CAMAROTE", "MEME DO CAIXÃO", "CABELEIREIRA LEILA"
    ]
}

log = logging.getLogger("impostor.server")


# --------------------------------------------------------------------------
# Funções utilitárias
# --------------------------------------------------------------------------
def normalize(text):
    """Normaliza texto para comparar palpites: sem acento, minúsculo,
    espaços colapsados. 'FEIJÃO' == 'feijao' == '  Feijão '."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.casefold().split())


class BadMessage(Exception):
    """Mensagem recebida fora do formato do protocolo (recuperável)."""


class GameAborted(Exception):
    """Partida precisa ser encerrada (ex.: jogadores insuficientes)."""


# --------------------------------------------------------------------------
# Representação de um jogador conectado
# --------------------------------------------------------------------------
class Player:
    """Encapsula o socket de um cliente.

    send() é thread-safe: várias threads (partida, handlers de outros
    clientes) podem enviar ao mesmo jogador; o _send_lock garante que as
    linhas JSON não se misturem no fluxo TCP.
    """

    def __init__(self, conn, addr):
        self.conn = conn
        self.addr = addr
        self.name = None
        self.ready = False
        self.skin = ""                   # nome da skin (usado só pelo frontend gráfico)
        self.connected = True
        self._send_lock = threading.Lock()
        # makefile() devolve um objeto de arquivo que facilita ler "uma linha
        # por mensagem" (framing por '\n') sobre o fluxo de bytes do TCP.
        self.rfile = conn.makefile("r", encoding="utf-8", newline="\n")

    def send(self, type_, **data):
        """Envia um evento ao cliente. Retorna False se a conexão falhou."""
        if not self.connected:
            return False
        payload = json.dumps({"type": type_, "data": data},
                             ensure_ascii=False) + "\n"
        try:
            with self._send_lock:
                self.conn.sendall(payload.encode("utf-8"))
            return True
        except OSError:
            self.close()
            return False

    def close(self):
        self.connected = False
        try:
            # shutdown() acorda a thread bloqueada em readline() (recebe EOF).
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.conn.close()
        except OSError:
            pass

    def __repr__(self):
        return "<%s %s:%s>" % (self.name or "?", self.addr[0], self.addr[1])


class GameContext:
    """Dados de UMA partida (sorteios, participantes e fila de eventos)."""

    def __init__(self, participants, theme, word, impostor, events):
        self.participants = participants   # jogadores no início da partida
        self.theme = theme
        self.word = word
        self.impostor = impostor
        self.events = events               # queue.Queue de (player, type, data)

    def active(self):
        """Participantes que continuam conectados."""
        return [p for p in self.participants if p.connected]


# --------------------------------------------------------------------------
# Servidor
# --------------------------------------------------------------------------
class GameServer:
    def __init__(self, host, port, min_turns, max_turns,
                 turn_timeout, vote_timeout):
        self.host = host
        self.port = port
        self.min_turns = max(1, min_turns)            # turnos antes da 1ª decisão "Continuar x Votação"
        self.max_turns = max(max_turns, self.min_turns)  # ao atingir, votação é obrigatória
        self.turn_timeout = turn_timeout
        self.vote_timeout = vote_timeout

        self.lock = threading.RLock()      # protege players/state/events
        self.players = []                  # jogadores com nome (na sala)
        self.state = WAIT_PLAYERS
        self.events = queue.Queue()        # fila da partida em andamento

    # ------------------------------------------------------------------
    # Camada de rede: accept loop
    # ------------------------------------------------------------------
    def serve_forever(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # TCP
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(MAX_PLAYERS * 2)
        log.info("Servidor IMPOSTOR (%s) ouvindo em %s:%d",
                 PROTOCOL_VERSION, self.host, self.port)
        try:
            while True:
                conn, addr = srv.accept()
                # TCP_NODELAY: mensagens pequenas e interativas não devem
                # esperar o algoritmo de Nagle.  KEEPALIVE: detecta pares mortos.
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                log.info("Nova conexão de %s:%d", *addr)
                threading.Thread(target=self.handle_client,
                                 args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            log.info("Encerrando servidor (Ctrl+C).")
        finally:
            srv.close()

    # ------------------------------------------------------------------
    # Thread de cada cliente
    # ------------------------------------------------------------------
    def handle_client(self, conn, addr):
        player = Player(conn, addr)
        try:
            conn.settimeout(HANDSHAKE_TIMEOUT)      # só durante o handshake
            if not self._handshake(player):
                return
            conn.settimeout(None)
            self._client_loop(player)
        except OSError as exc:      # inclui socket.timeout e resets de conexão
            log.info("Conexão de %r encerrada: %s", player, exc)
        finally:
            self._remove_player(player)

    @staticmethod
    def _read_message(player):
        """Lê UMA mensagem (uma linha JSON).
        Retorna (type, data); None em EOF; BadMessage se malformada."""
        line = player.rfile.readline(MAX_LINE + 1)
        if not line:
            return None
        if len(line) > MAX_LINE:
            raise ConnectionError("mensagem maior que %d caracteres" % MAX_LINE)
        line = line.strip()
        if not line:
            raise BadMessage("linha vazia")
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            raise BadMessage("JSON inválido")
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            raise BadMessage("campo 'type' ausente")
        data = msg.get("data", {})
        if not isinstance(data, dict):
            raise BadMessage("campo 'data' deve ser um objeto")
        return msg["type"], data

    def _handshake(self, player):
        """Primeira mensagem obrigatória: player_join (versão + nome)."""
        def reject(reason):
            player.send("join_rejected", reason=reason)
            log.info("Conexão %r rejeitada: %s", player, reason)
            return False

        try:
            msg = self._read_message(player)
        except BadMessage:
            return reject("A primeira mensagem deve ser um 'player_join' válido.")
        if msg is None:
            return False
        type_, data = msg
        if type_ == "skins_query":
            # Consulta rápida do frontend gráfico (tela de escolha de skin): responde e
            # fecha a conexão, sem entrar na sala.
            with self.lock:
                taken = [p.skin for p in self.players if p.skin]
            player.send("skins_taken", skins=taken)
            return False
        if type_ != "player_join":
            return reject("A primeira mensagem deve ser 'player_join'.")
        if data.get("protocol") != PROTOCOL_VERSION:
            return reject("Versão de protocolo incompatível (esperado %s)."
                          % PROTOCOL_VERSION)
        name = str(data.get("name", "")).strip()
        if not NAME_RE.match(name):
            return reject("Nome inválido: use 1 a 15 letras, números, '_' ou '-'.")

        with self.lock:
            if self.state != WAIT_PLAYERS:
                return reject("Há uma partida em andamento. Tente mais tarde.")
            if len(self.players) >= MAX_PLAYERS:
                return reject("Sala cheia (%d/%d)." % (MAX_PLAYERS, MAX_PLAYERS))
            if any(p.name.casefold() == name.casefold() for p in self.players):
                return reject("Já existe um jogador com esse nome.")
            # Skin é OPCIONAL (clientes de terminal não enviam): é o NOME do arquivo
            # de imagem escolhido no frontend gráfico. Duas pessoas não podem usar a mesma.
            skin = " ".join(str(data.get("skin", "")).split())[:MAX_SKIN_LEN]
            if skin and any(p.skin.casefold() == skin.casefold() for p in self.players):
                return reject("A skin '%s' já está em uso. Escolha outra." % skin)
            player.name = name
            player.skin = skin
            self.players.append(player)
            player.send("welcome", name=name, protocol=PROTOCOL_VERSION,
                        min_players=MIN_PLAYERS, max_players=MAX_PLAYERS, skin=skin)
            self._broadcast_lobby("%s entrou na sala." % name)
        log.info("%s entrou (%d/%d)", name, len(self.players), MAX_PLAYERS)
        return True

    def _client_loop(self, player):
        """Laço principal de leitura de um jogador já aceito."""
        while player.connected:
            try:
                msg = self._read_message(player)
            except BadMessage as exc:
                player.send("error", code="BAD_MESSAGE", message=str(exc))
                continue
            if msg is None:             # cliente fechou a conexão (EOF)
                return
            type_, data = msg

            if type_ == "ping":                   # medição de latência
                player.send("pong", t=data.get("t"))
            elif type_ == "player_quit":
                return
            elif type_ == "player_ready":
                self._on_ready(player)
            else:
                # Eventos de jogo: entregues à thread da partida.
                with self.lock:
                    in_lobby = self.state == WAIT_PLAYERS
                    if not in_lobby:
                        self.events.put((player, type_, data))
                if in_lobby:
                    player.send("error", code="INVALID_STATE",
                                message="Evento '%s' não é válido no lobby." % type_)

    # ------------------------------------------------------------------
    # Lobby
    # ------------------------------------------------------------------
    def _on_ready(self, player):
        with self.lock:
            if self.state != WAIT_PLAYERS:
                player.send("error", code="INVALID_STATE",
                            message="A partida já começou.")
                return
            player.ready = True
            self._broadcast_lobby("%s está pronto." % player.name)
            self._maybe_start()

    def _maybe_start(self):
        """Inicia a partida se houver >= MIN_PLAYERS e todos estiverem prontos."""
        with self.lock:
            if self.state != WAIT_PLAYERS:
                return
            if len(self.players) < MIN_PLAYERS:
                return
            if not all(p.ready for p in self.players):
                return
            self.state = IN_GAME
            self.events = queue.Queue()     # fila nova => sem lixo de partidas antigas
            participants = list(self.players)
            events = self.events
        log.info("Todos prontos (%d). Iniciando partida.", len(participants))
        threading.Thread(target=self._run_game, args=(participants, events),
                         daemon=True).start()

    def _broadcast_lobby(self, notice):
        with self.lock:
            self.broadcast(
                "lobby_update",
                players=[{"name": p.name, "ready": p.ready, "skin": p.skin}
                     for p in self.players],
                notice=notice, min_players=MIN_PLAYERS, max_players=MAX_PLAYERS)

    def _remove_player(self, player):
        """Chamado quando a thread do cliente termina (saída ou queda)."""
        player.close()
        with self.lock:
            if player not in self.players:
                return
            self.players.remove(player)
            log.info("%s saiu (%d/%d)", player.name, len(self.players), MAX_PLAYERS)
            if self.state == WAIT_PLAYERS:
                self._broadcast_lobby("%s saiu da sala." % player.name)
                self._maybe_start()
            else:
                self.broadcast("info", message="%s desconectou-se." % player.name)
                self.events.put((player, "__disconnect__", {}))   # acorda o árbitro

    # ------------------------------------------------------------------
    # Envio
    # ------------------------------------------------------------------
    def broadcast(self, type_, **data):
        """Retransmite um evento a todos os jogadores da sala."""
        with self.lock:
            targets = list(self.players)
        for p in targets:
            p.send(type_, **data)

    def _set_state(self, state):
        with self.lock:
            self.state = state
        log.info("Estado -> %s", state)
        self.broadcast("state_change", state=state)

    # ==================================================================
    # Thread da PARTIDA (árbitro)
    # ==================================================================
    def _run_game(self, participants, events):
        theme = random.choice(list(THEMES))
        word = random.choice(THEMES[theme])
        impostor = random.choice(participants)           # sorteio do Impostor
        ctx = GameContext(participants, theme, word, impostor, events)
        log.info("Partida: tema=%s palavra=%s impostor=%s",
                 theme, word, impostor.name)
        try:
            result, message, extra = self._play(ctx)
        except GameAborted as exc:
            result, message, extra = "ABORTED", str(exc), {}
        except Exception:                                  # defesa final
            log.exception("Erro inesperado na partida")
            result, message, extra = "ABORTED", "Erro interno do servidor.", {}

        self._set_state(END_GAME)
        self.broadcast("end_game", result=result, message=message,
                       impostor=impostor.name, theme=theme, word=word, **extra)
        log.info("Fim da partida: %s", result)
        self._back_to_lobby()

    def _back_to_lobby(self):
        """Depois do fim, a sala volta ao lobby para uma nova partida."""
        time.sleep(1.0)
        with self.lock:
            for p in self.players:
                p.ready = False
            self.events = queue.Queue()
        self._set_state(WAIT_PLAYERS)
        self._broadcast_lobby("Nova partida: digitem 'pronto' quando quiserem jogar de novo.")

    # ------------------------------------------------------------------
    def _play(self, ctx):
        """Fluxo completo da partida. Retorna (resultado, mensagem, extras)."""
        self._set_state(IN_GAME)
        self.broadcast("start_game", players=[p.name for p in ctx.participants],
                       skins={p.name: p.skin for p in ctx.participants},
                       theme=ctx.theme, min_turns=self.min_turns,
                       max_turns=self.max_turns, turn_timeout=self.turn_timeout)
        # Papéis são enviados individualmente: o Impostor NÃO recebe a palavra.
        for p in ctx.participants:
            if p is ctx.impostor:
                p.send("role_assign", role="IMPOSTOR", theme=ctx.theme, word=None)
            else:
                p.send("role_assign", role="CIVILIAN", theme=ctx.theme, word=ctx.word)

        # Um TURNO termina quando TODOS os jogadores já fizeram sua pergunta.
        # Nesse momento abre-se a decisão ao vivo: "Continuar" x "Votação".
        turn_no = 0
        while True:
            turn_no += 1
            self._play_turn(ctx, turn_no)
            if turn_no >= self.max_turns:
                self.broadcast("info", message="Limite de turnos atingido: votação do impostor obrigatória.")
                break
            if turn_no >= self.min_turns:
                if self._ask_continue(ctx, turn_no) == "vote":
                    break
        return self._voting_phase(ctx)

    def _check(self, ctx):
        """Aborta a partida se a sala ficou inviável."""
        if not ctx.impostor.connected:
            raise GameAborted("O Impostor desconectou-se. Partida encerrada.")
        if len(ctx.active()) < MIN_PLAYERS:
            raise GameAborted("Jogadores insuficientes para continuar (mínimo %d)." % MIN_PLAYERS)

    @staticmethod
    def _drain(ctx):
        """Descarta mensagens antigas (atrasadas) antes de abrir um novo turno."""
        while True:
            try:
                ctx.events.get_nowait()
            except queue.Empty:
                return

    def _collect(self, ctx, players, msg_type, timeout, validate=None, on_accept=None):
        """Espera o evento `msg_type` de cada jogador em `players`.

        * Eventos de quem não deve falar agora => erro NOT_YOUR_TURN
          (é assim que o servidor IMPÕE a ordem dos turnos).
        * Retorna {player: data} dos que responderam dentro do prazo.
        """
        pending = set(players)
        total = len(pending)
        results = {}
        deadline = time.monotonic() + timeout
        while True:
            pending = {p for p in pending if p.connected}
            if not pending:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                player, type_, data = ctx.events.get(timeout=remaining)
            except queue.Empty:
                break
            if type_ == "__disconnect__":
                continue
            if player not in pending:
                player.send("error", code="NOT_YOUR_TURN",
                            message="Não é a sua vez de agir (ou você já agiu).")
                continue
            if type_ != msg_type:
                player.send("error", code="UNEXPECTED_EVENT",
                            message="Evento '%s' inesperado; o servidor espera '%s'."
                                    % (type_, msg_type))
                continue
            if validate:
                problem = validate(player, data)
                if problem:
                    player.send("error", code="INVALID_INPUT", message=problem)
                    continue
            results[player] = data
            pending.discard(player)
            if on_accept:
                on_accept(player, data, len(results), total)
        return results

    @staticmethod
    def _validate_text(player, data):
        text = str(data.get("text", "")).strip()
        if not text:
            return "O texto não pode ser vazio."
        data["text"] = text[:MAX_TEXT]
        return None

    # ------------------------------------------------------------------
    # Fase de turnos
    # ------------------------------------------------------------------
    def _play_turn(self, ctx, turn_no):
        """Um TURNO: TODOS os jogadores fazem uma pergunta, um de cada vez.

        O servidor embaralha a ordem de quem pergunta. Para cada perguntador:
          1. ele envia UMA pergunta (send_question);
          2. o servidor a retransmite a toda a sala (broadcast_question);
          3. TODOS os demais respondem, UM DE CADA VEZ, numa ordem sorteada;
             cada resposta é retransmitida na hora (broadcast_answer). Quem
             responde por último já viu as anteriores - é aí que o Impostor
             tenta se camuflar.
        O turno só termina (turn_end) quando todos já perguntaram; então o
        chamador abre a decisão "Continuar" x "Votação" (_ask_continue).
        """
        self._check(ctx)
        order = ctx.active()
        random.shuffle(order)
        steps = len(order)
        self.broadcast("turn_start", turn=turn_no, order=[p.name for p in order])

        for step, asker in enumerate(order, 1):
            self._check(ctx)
            if not asker.connected:
                self.broadcast("info", message="Pergunta de %s pulada (jogador ausente)." % asker.name)
                continue
            responders = [p for p in ctx.active() if p is not asker]
            random.shuffle(responders)

            # --- 1) pergunta (do perguntador para TODOS) ---
            self._drain(ctx)
            self.broadcast("ask_prompt", turn=turn_no, step=step, steps=steps,
                           asker=asker.name, responders=[p.name for p in responders],
                           timeout=self.turn_timeout)
            got = self._collect(ctx, [asker], "send_question",
                                self.turn_timeout, self._validate_text)
            if asker not in got:
                self.broadcast("action_timeout", player=asker.name, action="pergunta",
                               reason="timeout" if asker.connected else "disconnected")
                continue
            question = got[asker]["text"]
            self.broadcast("broadcast_question", turn=turn_no, step=step,
                           asker=asker.name, text=question)

            # --- 2) respostas (uma por vez, em ordem definida pelo servidor) ---
            for pos, responder in enumerate(responders, 1):
                self._check(ctx)
                if not responder.connected:
                    self.broadcast("info", message="%s está ausente e não responderá." % responder.name)
                    continue
                self._drain(ctx)
                self.broadcast("answer_prompt", turn=turn_no, asker=asker.name,
                               responder=responder.name, question=question,
                               position=pos, total=len(responders),
                               timeout=self.turn_timeout)
                got = self._collect(ctx, [responder], "send_answer",
                                    self.turn_timeout, self._validate_text)
                if responder not in got:
                    self.broadcast("action_timeout", player=responder.name, action="resposta",
                                   reason="timeout" if responder.connected else "disconnected")
                    continue
                self.broadcast("broadcast_answer", turn=turn_no, asker=asker.name,
                               responder=responder.name, question=question,
                               text=got[responder]["text"])
        self.broadcast("turn_end", turn=turn_no)

    # ------------------------------------------------------------------
    # Decisão ao fim de CADA turno: "Continuar" (outro turno) x "Votação"
    # ------------------------------------------------------------------
    def _ask_continue(self, ctx, turn_no):
        self._set_state(DECIDING)
        voters = ctx.active()
        tally = {"vote": [], "continue": []}      # quem votou em quê (público)

        def validate(player, data):
            choice = str(data.get("choice", "")).strip().lower()
            if choice not in ("vote", "continue"):
                return "choice deve ser 'vote' ou 'continue'."
            data["choice"] = choice
            return None

        def on_vote(player, data, done, total):
            """TEMPO REAL: a cada voto aceito, toda a sala vê quem votou em
            Votação, quem votou em Continuar e quem ainda falta votar."""
            tally[data["choice"]].append(player.name)
            voted = set(tally["vote"]) | set(tally["continue"])
            waiting = [p.name for p in voters if p.connected and p.name not in voted]
            self.broadcast("continue_vote_cast", voter=player.name, choice=data["choice"],
                           voters_vote=list(tally["vote"]),
                           voters_continue=list(tally["continue"]),
                           waiting=waiting, received=done, total=total)

        self._drain(ctx)
        self.broadcast("ask_continue", turn=turn_no, options=["vote", "continue"],
                       message="Fim do turno %d: todos já fizeram sua pergunta. "
                               "Desejam continuar (outro turno) ou ir para a Votação do impostor?" % turn_no,
                       timeout=self.vote_timeout)
        self._collect(ctx, voters, "send_continue_vote", self.vote_timeout, validate, on_vote)
        self._check(ctx)

        n_vote, n_cont = len(tally["vote"]), len(tally["continue"])
        voted = set(tally["vote"]) | set(tally["continue"])
        abstained = [p.name for p in voters if p.name not in voted]
        total = len(ctx.active())
        # Maioria ABSOLUTA dos jogadores presentes é exigida para ir à Votação.
        # Empate ou omissão => jogam outro turno.
        decision = "vote" if n_vote > total / 2 else "continue"
        self.broadcast("continue_result", votes_vote=n_vote, votes_continue=n_cont,
                       abstentions=len(abstained), voters_vote=tally["vote"],
                       voters_continue=tally["continue"], abstained=abstained,
                       decision=decision)
        if decision == "continue":
            self._set_state(IN_GAME)
        return decision

    # ------------------------------------------------------------------
    # Votação
    # ------------------------------------------------------------------
    def _voting_phase(self, ctx):
        self._set_state(VOTING)
        voters = ctx.active()
        by_name = {p.name.casefold(): p for p in voters}

        def validate(voter, data):
            target = by_name.get(str(data.get("target", "")).strip().casefold())
            if target is None:
                return "Jogador inexistente. Opções: %s." % ", ".join(p.name for p in voters)
            if target is voter:
                return "Você não pode votar em si mesmo."
            data["_target"] = target
            return None

        def progress(player, data, done, total):   # não revela em quem cada um votou
            self.broadcast("vote_progress", received=done, total=total)

        self._drain(ctx)
        self.broadcast("start_vote", candidates=[p.name for p in voters],
                       timeout=self.vote_timeout)
        votes = self._collect(ctx, voters, "send_vote", self.vote_timeout,
                              validate, progress)
        self._check(ctx)

        ballot = {v.name: d["_target"].name for v, d in votes.items()}
        tally = Counter(ballot.values())
        accused, tie = None, True
        if tally:
            ranking = tally.most_common()
            leaders = [n for n, c in ranking if c == ranking[0][1]]
            if len(leaders) == 1:
                accused, tie = leaders[0], False
        caught = accused == ctx.impostor.name
        self.broadcast("vote_result", ballot=ballot, tally=dict(tally),
                       accused=accused, tie=tie, impostor_caught=caught)

        if not caught:
            motivo = ("Houve empate na votação: ninguém foi eliminado."
                      if tie else "%s era inocente!" % accused)
            return "IMPOSTOR_WINS", motivo + " O Impostor venceu!", {}
        return self._guess_phase(ctx)

    # ------------------------------------------------------------------
    # Tentativa de empate do Impostor
    # ------------------------------------------------------------------
    def _guess_phase(self, ctx):
        self._set_state(GUESSING)
        imp = ctx.impostor

        def validate(player, data):
            word = str(data.get("word", "")).strip()
            if not word:
                return "Informe uma palavra."
            data["word"] = word[:MAX_TEXT]
            return None

        self._drain(ctx)
        self.broadcast("request_guess", impostor=imp.name, theme=ctx.theme,
                       timeout=self.turn_timeout)
        got = self._collect(ctx, [imp], "send_guess", self.turn_timeout, validate)
        self._check(ctx)
        guess = got[imp]["word"] if imp in got else None
        correct = guess is not None and normalize(guess) == normalize(ctx.word)
        self.broadcast("guess_result", impostor=imp.name, guess=guess, correct=correct)
        if correct:
            return ("DRAW", "O Impostor foi descoberto, mas acertou a palavra secreta: EMPATE!",
                    {"guess": guess})
        return ("IMPOSTOR_LOSES", "O Impostor foi descoberto e errou a palavra: os jogadores venceram!",
                {"guess": guess})


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Servidor do jogo IMPOSTOR (TCP)")
    ap.add_argument("--host", default="0.0.0.0", help="interface de escuta (padrão 0.0.0.0)")
    ap.add_argument("--port", type=int, default=5000, help="porta TCP (padrão 5000)")
    ap.add_argument("--min-turns", type=int, default=1,
                    help="turnos completos antes da 1ª decisão 'Continuar x Votação' (padrão 1 = após todo turno)")
    ap.add_argument("--max-turns", type=int, default=5,
                    help="limite de turnos; ao atingi-lo a votação do impostor é obrigatória (padrão 5)")
    ap.add_argument("--turn-timeout", type=int, default=60,
                    help="segundos para perguntar/responder/palpitar (padrão 60)")
    ap.add_argument("--vote-timeout", type=int, default=45,
                    help="segundos para votar (padrão 45)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")
    GameServer(args.host, args.port, args.min_turns, args.max_turns,
               args.turn_timeout, args.vote_timeout).serve_forever()


if __name__ == "__main__":
    main()
