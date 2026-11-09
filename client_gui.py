#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client_gui.py - Frontend gráfico (Tkinter + Pillow) do jogo IMPOSTOR.

Fala o MESMO protocolo IMPOSTOR/1.0 do client.py, só que desenha uma sala:
3 jogadores embaixo e 2 em cima, balões de pergunta/resposta sobre cada
jogador, faixa de avisos no topo, votos em tempo real e telas de
vitória / derrota / empate.

Requisitos: Python 3.8+, Pillow (pip install pillow). Tkinter já vem no Python
(no Linux: sudo apt install python3-tk).
Coloque ao lado deste arquivo o avatares_geometricos.py (ou a pasta
'avatares_geometricos' com os PNGs): as 10 skins vêm de lá.

Uso:  python3 client_gui.py [--host 127.0.0.1] [--port 5000] [--name Ana]
"""
import argparse
import json
import queue
import re
import socket
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageFont, ImageTk

PROTOCOL_VERSION = "IMPOSTOR/1.0"
W, H, BAR_H = 1000, 620, 96           # área da sala e barra de ações
BG, GOLD = "#14202b", "#f2c14e"

SKIN_NAMES = ["Darth Vader", "Ben 10", "Batman", "Capitão América",
              "Harry Potter", "Homem-Aranha", "Mulher-Maravilha", "Elsa",
              "Princesa Peach", "Arlequina"]
SKIN_FILES = ["01_darth_vader.png", "02_ben_10.png", "03_batman.png",
              "04_capitao_america.png", "05_harry_potter.png",
              "06_homem_aranha.png", "07_mulher_maravilha.png", "08_elsa.png",
              "09_princesa_peach.png", "10_arlequina.png"]

# Lugares na ordem de entrada: 3 embaixo (esq→dir), depois 2 em cima.
SEATS = [(190, 490, 112), (500, 490, 112), (810, 490, 112),
         (290, 225, 96), (710, 225, 96)]

# tipo de balão -> (cabeçalho, fundo, borda, cor do cabeçalho)
STYLE = {
    "q":      ("PERGUNTA",  "#ffffff", "#2d6cdf", "#2d6cdf"),
    "a":      ("RESPOSTA",  "#fff6cf", "#d19a00", "#9a6b00"),
    "vote":   ("VOTO",      "#ffe3e0", "#d1382e", "#a3251c"),
    "cont":   ("VOTO",      "#dff5e1", "#2e9d48", "#1e7a34"),
    "ballot": ("VOTOU EM",  "#ece6ff", "#6b4fd1", "#4b34a8"),
    "guess":  ("PALPITE",   "#fff6cf", "#d19a00", "#9a6b00"),
}

PROMPTS = {"ask": "Digite sua PERGUNTA para todos",
           "answer": "Digite sua RESPOSTA",
           "guess": "Qual é a PALAVRA SECRETA?"}


# --------------------------------------------------------------------------
# Imagens: skins e sala
# --------------------------------------------------------------------------
def _placeholder(i):
    im = Image.new("RGB", (512, 512), "#07090e")
    d = ImageDraw.Draw(im)
    d.ellipse((96, 96, 416, 416), fill="#3a4a6a")
    d.text((256, 256), SKIN_NAMES[i][0], fill="white", anchor="mm",
           font=ImageFont.load_default())
    return im


def load_skin_images():
    """Carrega os 10 PNGs; se não existirem, gera com avatares_geometricos.py."""
    here = Path(__file__).resolve().parent
    folder = here / "avatares_geometricos"
    if not all((folder / f).exists() for f in SKIN_FILES):
        try:
            sys.path.insert(0, str(here))
            import avatares_geometricos as av
            av.main()
        except Exception as exc:       # sem o script: usa marcadores simples
            print("Aviso: não consegui gerar as skins (%s)." % exc)
    imgs = []
    for i, f in enumerate(SKIN_FILES):
        p = folder / f
        imgs.append(Image.open(p).convert("RGB") if p.exists() else _placeholder(i))
    return imgs


def circle_avatar(img, d):
    big = d * 2
    im = img.resize((big, big), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    out = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out.resize((d, d), Image.LANCZOS)


def make_room():
    """Desenha a sala (parede, piso, janela, sofá, estante, tapete...)."""
    S = 2
    im = Image.new("RGB", (W * S, H * S), "#86c5b4")
    d = ImageDraw.Draw(im)
    R = lambda *b: [v * S for v in b]
    P = lambda pts: [(x * S, y * S) for x, y in pts]
    fy = 326                                              # linha do piso

    d.ellipse(R(-300, -420, 1300, 300), fill="#7dbcab")   # curva da parede
    d.rectangle(R(0, fy - 16, W, fy), fill="#2f7d78")     # rodapé
    d.rectangle(R(0, fy, W, H), fill="#d9a05d")           # piso
    for x in range(-40, W + 90, 90):
        d.rectangle(R(x, fy, x + 45, H), fill="#cf9250")
        d.line(P([(x, fy), (x, H)]), fill="#b9803f", width=S)

    # janela + cortinas
    d.rectangle(R(790, 52, 952, 58), fill="#2e5d66")
    d.rectangle(R(808, 64, 932, 232), fill="#f4f1e8")
    d.rectangle(R(814, 70, 926, 226), fill="#6fb0d3")
    d.polygon(P([(814, 70), (860, 70), (814, 150)]), fill="#8cc4e0")
    d.line(P([(870, 70), (870, 226)]), fill="#f4f1e8", width=5 * S)
    d.line(P([(814, 148), (926, 148)]), fill="#f4f1e8", width=5 * S)
    d.polygon(P([(790, 58), (832, 58), (824, 238), (794, 238)]), fill="#f2c777")
    d.polygon(P([(910, 58), (952, 58), (948, 238), (918, 238)]), fill="#f2c777")
    for x in (803, 812, 930, 940):
        d.line(P([(x, 62), (x, 234)]), fill="#e0aa55", width=S)

    # relógio
    d.ellipse(R(46, 46, 114, 114), fill="#f0b24e")
    d.ellipse(R(53, 53, 107, 107), fill="#f6f3ea")
    d.line(P([(80, 80), (80, 62)]), fill="#3b4a5a", width=3 * S)
    d.line(P([(80, 80), (94, 86)]), fill="#3b4a5a", width=3 * S)

    # estante: livros, quadro, vaso
    for y in (150, 224):
        d.rectangle(R(24, y, 270, y + 9), fill="#e5a95b")
        d.rectangle(R(24, y + 9, 270, y + 13), fill="#b97d36")
    for i, c in enumerate(["#5b8fb9", "#d9777d", "#e8c15a", "#6aa77b"]):
        d.rectangle(R(40 + i * 15, 110, 52 + i * 15, 150), fill=c)
    d.rectangle(R(190, 112, 230, 150), fill="#f4f1e8")
    d.polygon(P([(200, 140), (210, 120), (220, 140)]), fill="#58a06a")
    d.rectangle(R(44, 186, 84, 224), fill="#f4f1e8")
    d.rectangle(R(50, 192, 78, 218), fill="#d98d6a")
    d.rectangle(R(100, 186, 136, 224), fill="#f4f1e8")
    d.rectangle(R(106, 192, 130, 218), fill="#e8c15a")
    d.polygon(P([(170, 224), (160, 204), (172, 190), (184, 204), (178, 224)]), fill="#7fb7d6")

    # sofá
    d.rounded_rectangle(R(350, 205, 650, 290), 22 * S, fill="#a65e7a")
    d.rounded_rectangle(R(336, 250, 664, 312), 18 * S, fill="#b86c87")
    d.rounded_rectangle(R(326, 236, 366, 314), 14 * S, fill="#9c5472")
    d.rounded_rectangle(R(634, 236, 674, 314), 14 * S, fill="#9c5472")
    d.rounded_rectangle(R(372, 222, 432, 270), 10 * S, fill="#f0c767")
    d.rounded_rectangle(R(440, 226, 500, 272), 10 * S, fill="#59b4ad")
    d.rounded_rectangle(R(570, 222, 630, 270), 10 * S, fill="#f0c767")
    for x in (350, 640):
        d.polygon(P([(x, 312), (x + 16, 312), (x + 8, 326), (x + 2, 326)]), fill="#5a3b2a")

    # tapete, planta e abajur
    d.ellipse(R(110, 396, 890, 606), fill="#2f7f86")
    d.ellipse(R(180, 424, 820, 580), fill="#3b97a0")
    d.polygon(P([(936, 548), (984, 548), (976, 600), (944, 600)]), fill="#c9553f")
    for dx, dy, c in [(-26, -62, "#3f9b57"), (0, -86, "#4db06a"),
                      (26, -60, "#3f9b57"), (-12, -40, "#58bd77"),
                      (14, -42, "#58bd77")]:
        d.polygon(P([(960, 548), (960 + dx - 11, 548 + dy / 2), (960 + dx, 548 + dy),
                     (960 + dx + 11, 548 + dy / 2)]), fill=c)
    d.line(P([(36, 284), (36, 590)]), fill="#3a5a8c", width=4 * S)
    d.polygon(P([(10, 284), (62, 284), (50, 238), (22, 238)]), fill="#26346a")
    d.ellipse(R(12, 584, 60, 600), fill="#3a5a8c")
    return im.resize((W, H), Image.LANCZOS)


def rrect(c, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


# --------------------------------------------------------------------------
# Aplicação
# --------------------------------------------------------------------------
class App(tk.Tk):
    # eventos só informativos: não cancelam a ação pendente do jogador
    PASSIVE = {"info", "error", "pong", "lobby_update", "vote_progress",
               "continue_vote_cast"}

    def __init__(self, args):
        super().__init__()
        self.title("IMPOSTOR")
        self.geometry("%dx%d+0+0" % (W, H + BAR_H))
        self.resizable(True, True)
        self.bind("<F11>", self.toggle_full)
        self.bind("<Escape>", lambda _e: self.attributes("-fullscreen", False))
        self.bind("<Return>", self.key_enter)
        self.configure(bg=BG)
        fams = set(tkfont.families())
        self.fam = next((f for f in ("Segoe UI", "Helvetica Neue", "Ubuntu",
                                     "DejaVu Sans", "Arial") if f in fams),
                        "TkDefaultFont")
        self.f_ban = (self.fam, 15, "bold")
        self.f_sub = (self.fam, 10)
        self.f_bub = (self.fam, 10)
        self.f_hdr = (self.fam, 8, "bold")
        self.f_name = (self.fam, 11, "bold")
        self.fn_name = tkfont.Font(family=self.fam, size=11, weight="bold")

        self.skin_imgs = load_skin_images()
        self.photos = {}
        self.room = ImageTk.PhotoImage(make_room())
        self.sock = self.rfile = None
        self.inbox = queue.Queue()
        self.closing = False
        self.reset_state()
        self.sel_skin = 0
        self.build_login(args)
        self.build_game()
        self.show_login()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(40, self.pump)

    # ------------------------------------------------------------------
    def reset_state(self):
        self.me = ""
        self.players, self.skins, self.ready, self.gone = [], {}, set(), set()
        self.bubbles, self.active = {}, None
        self.role = self.word = None
        self.theme, self.turn = "", 0
        self.state = "WAIT_PLAYERS"
        self.pending, self.pend_token, self.last_pending = None, 0, None
        self.deadline = None
        self.candidates = []
        self.ballot, self.tally = {}, {}
        self.end = None
        self.want_ready = False
        self.welcomed = False
        self.peek = False
        self.banner = ("Conectando...", "")
        self.pause_until = 0.0
        self.blink, self.blink_t = False, 0.0
        self.max_players, self.min_players = 5, 3
        self.dirty, self.bar_key = True, None

    def photo(self, skin, dia):
        key = (skin, dia)
        if key not in self.photos:
            img = self.skin_imgs[skin % len(self.skin_imgs)]
            self.photos[key] = ImageTk.PhotoImage(circle_avatar(img, dia))
        return self.photos[key]

    # ------------------------------------------------------------------
    # Tela inicial: nome + skin
    # ------------------------------------------------------------------
    def build_login(self, args):
        f = self.login = tk.Frame(self, bg=BG)
        tk.Label(f, text="IMPOSTOR", font=(self.fam, 34, "bold"), fg=GOLD,
                 bg=BG).pack(pady=(18, 0))
        tk.Label(f, text="Escolha seu nome e sua skin", font=(self.fam, 12),
                 fg="#a9c4d9", bg=BG).pack()
        row = tk.Frame(f, bg=BG)
        row.pack(pady=12)
        self.v_name = tk.StringVar(value=args.name or "")
        self.v_host = tk.StringVar(value=args.host)
        self.v_port = tk.StringVar(value=str(args.port))
        for label, var, w in (("Nome", self.v_name, 16), ("Servidor", self.v_host, 16),
                              ("Porta", self.v_port, 6)):
            tk.Label(row, text=label, fg="white", bg=BG, font=(self.fam, 11)
                     ).pack(side="left", padx=(12, 4))
            e = tk.Entry(row, textvariable=var, width=w, font=(self.fam, 12))
            e.pack(side="left")
            if label == "Nome":
                e.bind("<Return>", lambda _e: self.connect())
                self.e_name = e
        grid = tk.Frame(f, bg=BG)
        grid.pack(pady=6)
        self.cells = []
        self.thumbs = [ImageTk.PhotoImage(circle_avatar(im, 84)) for im in self.skin_imgs]
        for i, nm in enumerate(SKIN_NAMES):
            cell = tk.Frame(grid, bg="#1d2d3c", highlightthickness=3,
                            highlightbackground="#1d2d3c", padx=6, pady=4)
            cell.grid(row=i // 5, column=i % 5, padx=7, pady=7)
            lb1 = tk.Label(cell, image=self.thumbs[i], bg="#1d2d3c")
            lb2 = tk.Label(cell, text=nm, fg="white", bg="#1d2d3c", font=(self.fam, 9))
            lb1.pack(); lb2.pack()
            for w in (cell, lb1, lb2):
                w.bind("<Button-1>", lambda _e, k=i: self.select_skin(k))
            self.cells.append(cell)
        self.select_skin(0)
        tk.Label(f, text="Jogando em outra máquina? Em 'Servidor' coloque o IP de quem rodou o server.py.",
                 fg="#7f98ad", bg=BG, font=(self.fam, 9)).pack()
        self.lbl_login = tk.Label(f, text="", fg="#ff8a80", bg=BG, font=(self.fam, 11))
        self.lbl_login.pack(pady=(6, 0))
        tk.Button(f, text="ENTRAR NA SALA", font=(self.fam, 14, "bold"), bg="#46c35f",
                  fg="white", activebackground="#3aa850", relief="flat", padx=24, pady=8,
                  command=self.connect).pack(pady=10)

    def select_skin(self, i):
        self.sel_skin = i
        for k, cell in enumerate(self.cells):
            cell.configure(highlightbackground=GOLD if k == i else "#1d2d3c")

    def show_login(self, msg=""):
        self.game.pack_forget()
        self.login.pack(fill="both", expand=True)
        self.lbl_login.configure(text=msg)
        self.e_name.focus_set()

    def connect(self):
        name = self.v_name.get().strip()
        if not re.match(r"^[\w\-]{1,15}$", name):
            self.lbl_login.configure(text="Nome inválido: 1 a 15 letras, números, _ ou -.")
            return
        try:
            port = int(self.v_port.get())
            sock = socket.create_connection((self.v_host.get().strip(), port), timeout=6)
        except (ValueError, OSError) as exc:
            self.lbl_login.configure(text="Não foi possível conectar: %s" % exc)
            return
        sock.settimeout(None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.reset_state()
        self.me = name
        self.sock = sock
        self.rfile = sock.makefile("r", encoding="utf-8", newline="\n")
        self.inbox = queue.Queue()
        threading.Thread(target=self.reader, args=(self.rfile, self.inbox),
                         daemon=True).start()
        self.send("player_join", name=name, protocol=PROTOCOL_VERSION, skin=self.sel_skin)
        self.lbl_login.configure(text="Conectando...", fg="#a9c4d9")

    # ------------------------------------------------------------------
    # Tela do jogo: canvas (sala) + barra de ações
    # ------------------------------------------------------------------
    def build_game(self):
        g = self.game = tk.Frame(self, bg=BG)
        self.canvas = tk.Canvas(g, width=W, height=H, highlightthickness=0, bg=BG)
        # a barra é empacotada primeiro: assim ela NUNCA fica escondida em telas pequenas
        self.bar = tk.Frame(g, bg="#0f1923", height=BAR_H)
        self.bar.pack(side="bottom", fill="x")
        self.bar.pack_propagate(False)
        self.canvas.pack(side="top", expand=True)
        self.canvas.create_image(0, 0, image=self.room, anchor="nw", tags="bg")
        self.canvas.bind("<Button-1>", self.on_click)
        btn = tk.Button(self.canvas, text="Ver a palavra (segure)", font=(self.fam, 9, "bold"),
                        bg="#26394d", fg="white", relief="flat", padx=8, pady=4)
        btn.bind("<ButtonPress-1>", lambda _e: self.set_peek(True))
        btn.bind("<ButtonRelease-1>", lambda _e: self.set_peek(False))
        self.peek_win = self.canvas.create_window(W - 85, 30, window=btn, state="hidden")

    def toggle_full(self, _e=None):
        self.attributes("-fullscreen", not self.attributes("-fullscreen"))

    def key_enter(self, _e=None):
        """Enter no lobby = 'Estou pronto' (atalho do botão verde)."""
        if (self.sock and self.pending is None and self.state == "WAIT_PLAYERS"
                and self.end is None and self.me and self.me not in self.ready
                and self.game.winfo_ismapped()):
            self.send("player_ready")

    def set_peek(self, v):
        self.peek = v
        self.dirty = True

    # ------------------------------------------------------------------
    # Rede
    # ------------------------------------------------------------------
    @staticmethod
    def reader(rfile, inbox):
        try:
            for line in rfile:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    inbox.put((msg["type"], msg.get("data", {})))
                except (ValueError, KeyError, TypeError):
                    continue
        except (OSError, ValueError):
            pass
        inbox.put(("__closed__", {}))

    def send(self, type_, **data):
        try:
            payload = json.dumps({"type": type_, "data": data}, ensure_ascii=False) + "\n"
            self.sock.sendall(payload.encode("utf-8"))
        except (OSError, AttributeError):
            self.to_login("Conexão perdida.")

    def close_socket(self):
        try:
            self.sock.close()
        except (OSError, AttributeError):
            pass
        self.sock = None

    def to_login(self, msg):
        if self.sock is None and self.login.winfo_ismapped():
            return
        self.close_socket()
        self.reset_state()
        self.show_login(msg)

    def on_close(self):
        if self.sock:
            self.send("player_quit")
        self.close_socket()
        self.destroy()

    # ------------------------------------------------------------------
    # Laço principal: consome eventos (com pausas para dar tempo de ler)
    # ------------------------------------------------------------------
    def pump(self):
        try:
            self._pump_once()
        except Exception:                      # nunca deixa o laço morrer em silêncio
            import traceback
            traceback.print_exc()
        self.after(40, self.pump)

    def _pump_once(self):
        now = time.monotonic()
        try:
            if now >= self.pause_until:
                while True:
                    t, d = self.inbox.get_nowait()
                    self.handle(t, d)
                    if self.pause_until > time.monotonic():
                        break
        except queue.Empty:
            pass
        if self.active and now - self.blink_t > 0.5:
            self.blink, self.blink_t, self.dirty = not self.blink, now, True
        if (self.want_ready and self.state == "WAIT_PLAYERS" and self.end is None
                and self.me not in self.ready and self.sock):
            self.want_ready = False
            self.send("player_ready")
        if self.game.winfo_ismapped():
            self.sync_bar()
            if self.dirty:
                self.dirty = False
                self.redraw()
            if self.deadline and self.pending and hasattr(self, "timer_lbl"):
                left = max(0, int(self.deadline - now))
                self.timer_lbl.configure(text="Tempo: %ds" % left)

    def pause(self, ms):
        self.pause_until = time.monotonic() + ms / 1000.0

    def say(self, main, sub=""):
        self.banner = (main, sub)

    def handle(self, t, d):
        if t == "__closed__":
            if not self.closing:
                if self.welcomed:
                    self.to_login("Conexão com o servidor encerrada.")
                else:
                    self.to_login("O servidor fechou a conexão antes de aceitar você. "
                                  "Veja o erro no terminal do server.py ou teste outra porta "
                                  "(ex.: 5050, nos dois lados).")
            return
        if t not in self.PASSIVE and self.pending:
            self.pending = None
        fn = getattr(self, "on_" + t, None)
        if fn:
            fn(d)
        self.dirty = True

    def set_pending(self, kind, timeout=None):
        self.pending = kind
        self.last_pending = (kind, timeout)
        self.pend_token += 1
        self.deadline = time.monotonic() + timeout if timeout else None

    # ------------------------------------------------------------------
    # Eventos do servidor
    # ------------------------------------------------------------------
    def on_welcome(self, d):
        self.welcomed = True
        self.state = "WAIT_PLAYERS"
        self.min_players, self.max_players = d["min_players"], d["max_players"]
        got = d.get("skin", self.sel_skin)
        self.skins[self.me] = got
        sub = ""
        if got != self.sel_skin:
            sub = "A skin escolhida já estava em uso: você ficou com %s." % SKIN_NAMES[got]
        self.say("Você entrou na sala!", sub)
        self.login.pack_forget()
        self.game.pack(fill="both", expand=True)

    def on_join_rejected(self, d):
        self.close_socket()
        self.show_login("Entrada recusada: %s" % d["reason"])

    def on_lobby_update(self, d):
        self.players = [p["name"] for p in d["players"]]
        self.skins = {p["name"]: p.get("skin", 0) for p in d["players"]}
        self.ready = {p["name"] for p in d["players"] if p["ready"]}
        self.min_players, self.max_players = d["min_players"], d["max_players"]
        if self.state == "WAIT_PLAYERS":
            n = len(self.players)
            main = "Aguardando jogadores (%d/%d)" % (n, self.max_players)
            if n >= self.min_players and len(self.ready) < n:
                main = "Aguardando todos ficarem prontos (%d/%d)" % (len(self.ready), n)
            self.say(main, d["notice"])

    def on_state_change(self, d):
        self.state = d["state"]
        if self.state == "WAIT_PLAYERS":
            self.bubbles, self.active, self.peek = {}, None, False

    def on_start_game(self, d):
        self.players = d["players"]
        self.skins.update(d.get("skins", {}))
        self.theme, self.max_turns = d["theme"], d["max_turns"]
        self.bubbles, self.active, self.ballot, self.tally = {}, None, {}, {}
        self.end = None
        self.say("A partida começou!", "Tema: %s" % self.theme)
        self.pause(2200)

    def on_role_assign(self, d):
        self.role, self.word, self.theme = d["role"], d["word"], d["theme"]
        if self.role == "IMPOSTOR":
            self.say("VOCÊ É O IMPOSTOR!",
                     "Tema: %s  |  Você não sabe a palavra. Disfarce-se!" % self.theme)
        else:
            self.say("A palavra do jogo é: %s" % self.word,
                     "Tema: %s  |  Descubra quem não a conhece (sem entregar de bandeja)" % self.theme)
        self.pause(5500)

    def on_turn_start(self, d):
        self.turn = d["turn"]
        self.say("Turno %d" % d["turn"], "Ordem das perguntas: " + " > ".join(d["order"]))
        self.pause(1500)

    def on_ask_prompt(self, d):
        self.bubbles, self.active = {}, d["asker"]
        if d["asker"] == self.me:
            self.say("Sua vez de perguntar!", "Faça UMA pergunta sobre a palavra para todos.")
            self.set_pending("ask", d.get("timeout"))
        else:
            self.say("%s irá perguntar" % d["asker"],
                     "Pergunta %d/%d do turno %d" % (d["step"], d["steps"], d["turn"]))

    def on_broadcast_question(self, d):
        self.bubbles[d["asker"]] = ("q", d["text"])
        self.say("%s perguntou" % d["asker"], "Agora todos respondem, um de cada vez.")
        self.pause(900)

    def on_answer_prompt(self, d):
        self.active = d["responder"]
        if d["responder"] == self.me:
            self.say("Sua vez de responder!", 'Pergunta de %s: "%s"' % (d["asker"], d["question"]))
            self.set_pending("answer", d.get("timeout"))
        else:
            self.say("%s irá responder" % d["responder"],
                     "Resposta %d/%d" % (d["position"], d["total"]))

    def on_broadcast_answer(self, d):
        self.bubbles[d["responder"]] = ("a", d["text"])
        self.pause(900)

    def on_action_timeout(self, d):
        self.say("%s não agiu a tempo" % d["player"], "O passo (%s) foi pulado." % d["action"])
        self.pause(1500)

    def on_turn_end(self, d):
        self.active = None
        self.say("Fim do turno %d" % d["turn"], "Todos já fizeram sua pergunta.")
        self.pause(1500)

    def on_ask_continue(self, d):
        self.bubbles, self.active = {}, None
        self.say("Todos devem votar!", "Continuar (outro turno) ou Votação do impostor?")
        self.set_pending("continue", d.get("timeout"))

    def on_continue_vote_cast(self, d):
        for n in d["voters_vote"]:
            self.bubbles[n] = ("vote", "VOTAÇÃO")
        for n in d["voters_continue"]:
            self.bubbles[n] = ("cont", "CONTINUAR")
        sub = "VOTAÇÃO %d  ·  CONTINUAR %d" % (len(d["voters_vote"]), len(d["voters_continue"]))
        if d["waiting"]:
            sub += "  ·  faltam: " + ", ".join(d["waiting"])
        self.say("Todos devem votar!", sub)

    def on_continue_result(self, d):
        for n in d["voters_vote"]:
            self.bubbles[n] = ("vote", "VOTAÇÃO")
        for n in d["voters_continue"]:
            self.bubbles[n] = ("cont", "CONTINUAR")
        self.say("Vamos para a votação do impostor!" if d["decision"] == "vote"
                 else "Vamos jogar outro turno!",
                 "VOTAÇÃO %d  ·  CONTINUAR %d" % (d["votes_vote"], d["votes_continue"]))
        self.pause(3200)

    def on_start_vote(self, d):
        self.bubbles, self.active = {}, None
        self.candidates = [c for c in d["candidates"] if c != self.me]
        self.say("Todos devem votar: quem é o Impostor?",
                 "Clique em um jogador ou use os botões abaixo.")
        self.set_pending("vote", d.get("timeout"))

    def on_vote_progress(self, d):
        self.say("Todos devem votar: quem é o Impostor?",
                 "Votos recebidos: %d/%d" % (d["received"], d["total"]))

    def on_vote_result(self, d):
        self.ballot, self.tally = d["ballot"], d["tally"]
        self.bubbles = {v: ("ballot", t) for v, t in d["ballot"].items()}
        self.say("Votação encerrada!",
                 "Empate: ninguém foi o mais votado." if d["tie"]
                 else "Mais votado: %s" % d["accused"])
        self.pause(4500)

    def on_request_guess(self, d):
        self.bubbles, self.active = {}, d["impostor"]
        if d["impostor"] == self.me:
            self.say("Você foi descoberto! Adivinhe a palavra", "Tema: %s" % d["theme"])
            self.set_pending("guess", d.get("timeout"))
        else:
            self.say("%s foi descoberto!" % d["impostor"], "Ele tenta adivinhar a palavra...")

    def on_guess_result(self, d):
        self.bubbles[d["impostor"]] = ("guess", d["guess"] or "(sem palpite)")
        self.say("ACERTOU!" if d["correct"] else "ERROU!", "Palpite de %s" % d["impostor"])
        self.pause(2800)

    def on_end_game(self, d):
        self.end = d
        self.active = None
        self.show_end()

    def on_info(self, d):
        m = d["message"]
        if m.endswith(" desconectou-se."):
            self.gone.add(m[:-len(" desconectou-se.")])
        self.say(m)
        self.pause(1200)

    def on_error(self, d):
        self.say("Aviso do servidor", d["message"])
        if d["code"] == "INVALID_INPUT" and self.last_pending and self.pending is None:
            kind, timeout = self.last_pending
            self.set_pending(kind, None)
            if kind == "vote":
                self.candidates = [c for c in self.players if c != self.me]

    # ------------------------------------------------------------------
    # Entrada do jogador
    # ------------------------------------------------------------------
    def submit_text(self, var):
        text = var.get().strip()
        if not text or not self.pending:
            return
        kind = self.pending
        self.pending = None
        self.pend_token += 1
        ev = {"ask": ("send_question", "text"), "answer": ("send_answer", "text"),
              "guess": ("send_guess", "word")}[kind]
        self.send(ev[0], **{ev[1]: text})
        self.say("Enviado!", "Aguarde os outros jogadores.")

    def choose_continue(self, choice):
        if self.pending != "continue":
            return
        self.pending = None
        self.pend_token += 1
        self.send("send_continue_vote", choice=choice)

    def cast_vote(self, target):
        if self.pending != "vote":
            return
        self.pending = None
        self.pend_token += 1
        self.bubbles[self.me] = ("ballot", target)     # só você vê o seu voto
        self.send("send_vote", target=target)
        self.say("Voto enviado!", "Aguardando os demais...")
        self.dirty = True

    def on_click(self, ev):
        if self.pending != "vote":
            return
        for i, name in enumerate(self.players[:5]):
            x, y, dia = SEATS[i]
            if name != self.me and (ev.x - x) ** 2 + (ev.y - y) ** 2 <= (dia // 2) ** 2:
                self.cast_vote(name)
                return

    # ------------------------------------------------------------------
    # Barra de ações (embaixo)
    # ------------------------------------------------------------------
    def sync_bar(self):
        key = (self.pending, self.pend_token, self.state,
               self.me in self.ready, self.end is None)
        if key != self.bar_key:
            self.bar_key = key
            self.render_bar()

    def render_bar(self):
        for w in self.bar.winfo_children():
            w.destroy()
        mode = self.pending
        if mode is None and self.state == "WAIT_PLAYERS" and self.end is None:
            mode = "ready"
        box = tk.Frame(self.bar, bg="#0f1923")
        box.pack(side="left", expand=True, fill="both", padx=14)
        self.timer_lbl = tk.Label(self.bar, text="", fg=GOLD, bg="#0f1923",
                                  font=(self.fam, 12, "bold"))
        self.timer_lbl.pack(side="right", padx=16)
        lab = lambda t: tk.Label(box, text=t, fg="white", bg="#0f1923",
                                 font=(self.fam, 12, "bold"))
        btn = lambda t, bg, cmd: tk.Button(box, text=t, bg=bg, fg="white", relief="flat",
                                           activebackground=bg, font=(self.fam, 12, "bold"),
                                           padx=16, pady=8, command=cmd)
        if mode == "ready":
            if self.me in self.ready:
                lab("Você está pronto. Aguardando os outros jogadores...").pack(pady=30)
            else:
                lab("Quando todos estiverem na sala, clique em:").pack(side="left", padx=10)
                btn("ESTOU PRONTO", "#46c35f", lambda: self.send("player_ready")
                    ).pack(side="left", pady=24)
        elif mode in PROMPTS:
            var = tk.StringVar()
            var.trace_add("write", lambda *_: var.set(var.get()[:200]) if len(var.get()) > 200 else None)
            lab(PROMPTS[mode] + ":").pack(side="left", padx=(0, 10))
            e = tk.Entry(box, textvariable=var, font=(self.fam, 13), width=48)
            e.pack(side="left", ipady=5)
            e.bind("<Return>", lambda _e: self.submit_text(var))
            e.focus_set()
            btn("ENVIAR", "#2d6cdf", lambda: self.submit_text(var)).pack(side="left", padx=10)
        elif mode == "continue":
            lab("Como vocês querem seguir?").pack(side="left", padx=(0, 14))
            btn("VOTAÇÃO (votar no impostor)", "#d1382e",
                lambda: self.choose_continue("vote")).pack(side="left", padx=6, pady=24)
            btn("CONTINUAR (outro turno)", "#2e9d48",
                lambda: self.choose_continue("continue")).pack(side="left", padx=6)
        elif mode == "vote":
            lab("Quem é o Impostor?").pack(side="left", padx=(0, 14))
            for name in self.candidates:
                btn(name, "#6b4fd1", lambda n=name: self.cast_vote(n)
                    ).pack(side="left", padx=5, pady=24)
        else:
            lab("Aguarde: ainda não é a sua vez.").pack(pady=30)

    # ------------------------------------------------------------------
    # Desenho
    # ------------------------------------------------------------------
    def redraw(self):
        c = self.canvas
        c.delete("dyn")
        self.draw_players()
        self.draw_bubbles()
        self.draw_chips()
        self.draw_banner()
        if self.end:
            self.draw_end()
        in_game = self.state in ("IN_GAME", "DECIDING", "VOTING", "GUESSING")
        c.itemconfigure(self.peek_win,
                        state="normal" if (self.role == "CIVILIAN" and in_game
                                           and not self.end) else "hidden")

    def draw_players(self):
        c = self.canvas
        for i, name in enumerate(self.players[:5]):
            x, y, dia = SEATS[i]
            r = dia // 2
            gone = name in self.gone
            ring = "#6fd3ff" if name == self.me else "#f4f1e8"
            if name == self.active:
                ring = "#ffd23f" if self.blink else "#ff9f1c"
            c.create_oval(x - r - 6, y - r - 6, x + r + 6, y + r + 6, fill=ring,
                          outline="", tags="dyn")
            c.create_image(x, y, image=self.photo(self.skins.get(name, 0), dia), tags="dyn")
            if gone:
                c.create_oval(x - r, y - r, x + r, y + r, fill="black", stipple="gray50",
                              outline="", tags="dyn")
            label = name + (" (você)" if name == self.me else "")
            if name in self.ready and self.state == "WAIT_PLAYERS":
                label = "✔ " + label
            if gone:
                label += " (saiu)"
            w = self.fn_name.measure(label) + 26
            rrect(c, x - w / 2, y + r + 9, x + w / 2, y + r + 33, 10,
                  fill="#555e66" if gone else "#1b2a38",
                  outline=GOLD if name == self.active else "#0d1620", width=2, tags="dyn")
            c.create_text(x, y + r + 21, text=label, font=self.f_name,
                          fill="#bbb" if gone else "white", tags="dyn")

    def draw_bubbles(self):
        for name, (kind, text) in self.bubbles.items():
            if name not in self.players[:5]:
                continue
            x, y, dia = SEATS[self.players.index(name)]
            maxh = 92 if y < 300 else 100
            hdr, fill, edge, hcol = STYLE[kind]
            self.bubble(x, y - dia // 2 - 12, hdr, text, fill, edge, hcol, 250, maxh)

    def bubble(self, cx, bottom, hdr, text, fill, edge, hcol, maxw, maxh):
        c = self.canvas
        body = text
        while True:
            t = c.create_text(0, 0, text=body, font=self.f_bub, width=maxw - 20, anchor="nw")
            bb = c.bbox(t)
            c.delete(t)
            w, h = bb[2] - bb[0], bb[3] - bb[1]
            if h + 30 <= maxh or len(body) <= 12:
                break
            body = body[:int(len(body) * 0.85)].rstrip() + "…"
        bw, bh = max(w, 70) + 20, h + 30
        x0 = min(max(cx - bw / 2, 8), W - 8 - bw)
        y1 = bottom
        y0 = y1 - bh
        rrect(c, x0, y0, x0 + bw, y1, 12, fill=fill, outline=edge, width=2, tags="dyn")
        tx = min(max(cx, x0 + 18), x0 + bw - 18)
        c.create_polygon(tx - 8, y1 - 1, tx + 8, y1 - 1, cx, y1 + 11, fill=fill,
                         outline=edge, width=2, tags="dyn")
        c.create_line(tx - 7, y1 - 1, tx + 7, y1 - 1, fill=fill, width=3, tags="dyn")
        c.create_text(x0 + 10, y0 + 7, text=hdr, font=self.f_hdr, fill=hcol, anchor="nw",
                      tags="dyn")
        c.create_text(x0 + 10, y0 + 22, text=body, font=self.f_bub, fill="#1b2a38",
                      width=maxw - 20, anchor="nw", tags="dyn")

    def draw_chips(self):
        c = self.canvas
        if self.theme and self.state != "WAIT_PLAYERS":
            txt = "TEMA: %s%s" % (self.theme, ("   ·   Turno %d" % self.turn) if self.turn else "")
            w = self.fn_name.measure(txt) + 24
            rrect(c, W / 2 - w / 2, 588, W / 2 + w / 2, 614, 10, fill="#1b2a38",
                  outline="#0d1620", tags="dyn")
            c.create_text(W / 2, 601, text=txt, font=self.f_name, fill=GOLD, tags="dyn")

    def draw_banner(self):
        c = self.canvas
        main, sub = self.banner
        if self.peek and self.word:
            main, sub = "A palavra do jogo é: %s" % self.word, "Tema: %s" % self.theme
        t1 = c.create_text(W / 2, 20, text=main, font=self.f_ban, fill="white", width=660,
                           anchor="n", tags="dyn")
        b1 = c.bbox(t1)
        bottom, left, right = b1[3], b1[0], b1[2]
        if sub:
            t2 = c.create_text(W / 2, b1[3] + 3, text=sub, font=self.f_sub, fill="#a9c4d9",
                               width=660, anchor="n", tags="dyn")
            b2 = c.bbox(t2)
            bottom, left, right = b2[3], min(left, b2[0]), max(right, b2[2])
        bg = rrect(c, left - 20, 8, right + 20, bottom + 9, 16, fill="#14202b",
                   outline=GOLD, width=2, tags="dyn")
        c.tag_lower(bg, t1)

    # ------------------------------------------------------------------
    # Tela final: vitória / derrota / empate + quem votou em quem
    # ------------------------------------------------------------------
    def show_end(self):
        c = self.canvas
        c.delete("endwin")
        mk = lambda t, bg, cmd: tk.Button(c, text=t, bg=bg, fg="white", relief="flat",
                                          activebackground=bg, font=(self.fam, 13, "bold"),
                                          padx=18, pady=8, command=cmd)
        c.create_window(390, 538, window=mk("JOGAR DE NOVO", "#46c35f",
                                            lambda: self.close_end(True)), tags="endwin")
        c.create_window(610, 538, window=mk("SAIR", "#d1382e", self.on_close), tags="endwin")

    def close_end(self, again):
        self.canvas.delete("endwin")
        self.end = None
        self.want_ready = again
        self.dirty = True

    def draw_end(self):
        c, d = self.canvas, self.end
        res, imp = d["result"], self.role == "IMPOSTOR"
        if res == "DRAW":
            title, col = "EMPATE", "#e0b23c"
        elif res in ("IMPOSTOR_WINS", "IMPOSTOR_LOSES"):
            win = imp if res == "IMPOSTOR_WINS" else not imp
            title, col = ("VITÓRIA!", "#46c35f") if win else ("DERROTA", "#e5484d")
        else:
            title, col = "PARTIDA ENCERRADA", "#9aa7b4"
        c.create_rectangle(0, 0, W, H, fill="black", stipple="gray50", outline="", tags="dyn")
        rrect(c, 110, 30, 890, 590, 26, fill=BG, outline=col, width=4, tags="dyn")
        c.create_text(500, 92, text=title, font=(self.fam, 44, "bold"), fill=col, tags="dyn")
        c.create_text(500, 150, text=d["message"], width=680, font=(self.fam, 12),
                      fill="#dfe9f2", tags="dyn")
        c.create_text(500, 198, font=(self.fam, 12, "bold"), fill=GOLD, tags="dyn",
                      text="Impostor: %s   ·   Tema: %s   ·   Palavra: %s"
                           % (d["impostor"], d["theme"], d["word"]))
        c.create_text(500, 238, text="Quem votou em quem", font=(self.fam, 13, "bold"),
                      fill="white", tags="dyn")
        if self.ballot:
            for k, (v, t) in enumerate(self.ballot.items()):
                vv = v + (" (Impostor)" if v == d["impostor"] else "")
                tt = t + (" (Impostor)" if t == d["impostor"] else "")
                c.create_text(500, 272 + k * 28, text="%s   →   %s" % (vv, tt),
                              font=(self.fam, 12), tags="dyn",
                              fill="#8be59a" if t == d["impostor"] else "#ff9a9a")
            if self.tally:
                c.create_text(500, 424, tags="dyn", font=(self.fam, 11), fill="#a9c4d9",
                              text="Contagem: " + ", ".join("%s = %d" % kv
                                                           for kv in self.tally.items()))
        else:
            c.create_text(500, 290, text="Não houve votação nesta partida.",
                          font=(self.fam, 12), fill="#a9c4d9", tags="dyn")
        if d.get("guess"):
            c.create_text(500, 462, text="Palpite do Impostor: %s" % d["guess"],
                          font=(self.fam, 11), fill="#a9c4d9", tags="dyn")


def main():
    ap = argparse.ArgumentParser(description="Frontend gráfico do jogo IMPOSTOR")
    ap.add_argument("--host", default="127.0.0.1", help="IP/host do servidor")
    ap.add_argument("--port", type=int, default=5000, help="porta do servidor")
    ap.add_argument("--name", help="seu apelido (1-15 letras/números/_/-)")
    App(ap.parse_args()).mainloop()


if __name__ == "__main__":
    main()