#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client_gui.py - Frontend gráfico (Tkinter + Pillow) do jogo IMPOSTOR.

Pastas ao lado deste arquivo:
    Images/        icones das skins (o NOME DO ARQUIVO é o nome da skin)
    background/    imagem de fundo da sala (qualquer .png/.jpg; usa a primeira)

Requisitos: Python 3.8+ e Pillow (pip install pillow).
Uso:  python3 client_gui.py [--host 127.0.0.1] [--port 5000] [--name Ana]
Atalhos: F11 tela cheia | Esc sai da tela cheia | Enter = "Estou pronto" no lobby
"""
import argparse
import json
import queue
import re
import socket
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageFont, ImageTk

PROTOCOL_VERSION = "IMPOSTOR/1.0"
DW, DH, BAR_H = 1000, 620, 96          # tamanho de "projeto" da sala e altura da barra
BG, GOLD = "#14202b", "#f2c14e"
BASE = Path(__file__).resolve().parent
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# Lugares na ordem de entrada: 3 embaixo (esq→dir), depois 2 em cima.
SEATS = [(190, 490, 112), (500, 490, 112), (810, 490, 112),
         (290, 225, 96), (710, 225, 96)]
SPOT = (500, 255, 150)                 # impostor descoberto: centro da tela

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

RULES = [
    ("h", "OBJETIVO"),
    ("t", "Jogo de dedução para 3 a 5 jogadores. Todos recebem um TEMA. Os jogadores "
          "normais também recebem a PALAVRA SECRETA. Um jogador sorteado é o IMPOSTOR: "
          "ele só conhece o tema e precisa se disfarçar."),
    ("h", "COMO É UM TURNO"),
    ("t", "1. Cada jogador, um de cada vez, faz UMA pergunta sobre a palavra para todos.\n"
          "2. Todos os outros respondem, um por vez. As respostas são públicas, então quem "
          "responde depois já viu as anteriores.\n"
          "3. Quando todos já perguntaram, o turno termina."),
    ("h", "FIM DO TURNO: CONTINUAR OU VOTAÇÃO"),
    ("t", "Todos votam, ao vivo e de forma pública: CONTINUAR (outro turno) ou VOTAÇÃO "
          "(votar no impostor). É preciso mais da metade dos jogadores para ir à votação; "
          "empate ou omissão = continuar. Ao atingir o limite de turnos (padrão: 5), a "
          "votação é obrigatória."),
    ("h", "VOTAÇÃO DO IMPOSTOR"),
    ("t", "Cada um escolhe quem acha que é o impostor (clique no avatar ou use os botões). "
          "Ninguém vota em si mesmo. Os votos só são revelados na apuração."),
    ("h", "RESULTADO"),
    ("t", "• O mais votado NÃO é o impostor: o IMPOSTOR VENCE.\n"
          "• Empate na votação (ou nenhum voto): ninguém é eliminado e o IMPOSTOR VENCE.\n"
          "• O mais votado É o impostor: ele ganha a CHANCE DE EMPATE e tenta adivinhar a "
          "palavra. Se acertar, EMPATE. Se errar, o impostor PERDE.\n"
          "• O palpite ignora acentos, maiúsculas e espaços extras."),
    ("h", "TEMPO"),
    ("t", "Há um limite de tempo para perguntar, responder e votar (padrão: 60 s para "
          "perguntar/responder e 45 s para votar). Se você não agir, seu passo é pulado."),
    ("h", "DICAS"),
    ("t", "• Jogador normal: mostre que conhece a palavra sem entregá-la de bandeja.\n"
          "• Impostor: observe as respostas anteriores e responda de forma vaga.\n"
          "• Botão \"Ver a palavra (segure)\" mostra de novo a palavra, só para você.\n"
          "• Botão \"Histórico\" lista todas as perguntas e respostas da partida.\n"
          "• F11 = tela cheia."),
]


# --------------------------------------------------------------------------
# Imagens externas (pastas Images/ e background/)
# --------------------------------------------------------------------------
def _placeholder(label):
    im = Image.new("RGB", (256, 256), "#07090e")
    d = ImageDraw.Draw(im)
    d.ellipse((24, 24, 232, 232), fill="#3a4a6a")
    d.text((128, 128), (label or "?")[:1].upper(), fill="white", anchor="mm",
           font=ImageFont.load_default())
    return im


def load_icons():
    """Lê todas as imagens da pasta Images/. Nome da skin = nome do arquivo (sem extensão)."""
    for folder in ("Images", "images"):
        d = BASE / folder
        if d.is_dir():
            files = sorted((p for p in d.iterdir() if p.suffix.lower() in IMG_EXTS),
                           key=lambda p: p.name.lower())
            icons, seen = [], set()
            for p in files:
                key = " ".join(p.stem.split())[:40]
                if key and key.casefold() not in seen:
                    seen.add(key.casefold())
                    icons.append((key, Image.open(p).convert("RGB")))
            if icons:
                return icons
    return []


def load_background():
    """Usa a primeira imagem da pasta background/ (ou uma cor lisa se não houver)."""
    for folder in ("background", "Background"):
        d = BASE / folder
        if d.is_dir():
            files = sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXTS)
            if files:
                return Image.open(files[0]).convert("RGB")
    print("Aviso: coloque uma imagem na pasta 'background' (ao lado do client_gui.py).")
    return Image.new("RGB", (DW, DH), "#7dbcab")


def cover(img, w, h):
    """Redimensiona a imagem para preencher w×h (corta o excesso, sem distorcer)."""
    r = max(w / img.width, h / img.height)
    im = img.resize((max(w, int(img.width * r) + 1), max(h, int(img.height * r) + 1)),
                    Image.LANCZOS)
    left, top = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((left, top, left + w, top + h))


def taken_avatar(img, d):
    """Versão escurecida do ícone com um X vermelho (skin indisponível)."""
    big = d * 4
    im = circle_avatar(img, big)
    shade = Image.new("RGBA", im.size, (0, 0, 0, 0))
    shade.putalpha(im.split()[3].point(lambda a: a * 160 // 255))
    im = Image.alpha_composite(im, shade)
    dr = ImageDraw.Draw(im)
    m = int(big * 0.2)
    wd = max(6, big // 9)
    dr.line((m, m, big - m, big - m), fill=(235, 60, 70, 255), width=wd)
    dr.line((big - m, m, m, big - m), fill=(235, 60, 70, 255), width=wd)
    return im.resize((d, d), Image.LANCZOS)


def circle_avatar(img, d):
    big = d * 2
    im = img.resize((big, big), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    out = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out.resize((d, d), Image.LANCZOS)


# --------------------------------------------------------------------------
# Canvas que desenha em coordenadas de projeto (1000x620) e escala sozinho
# --------------------------------------------------------------------------
def sfont(font, k):
    fam, size, *rest = font
    return (fam, -max(7, int(round(size * k * 4 / 3)))) + tuple(rest)


class SCanvas(tk.Canvas):
    def __init__(self, master, k, **kw):
        self.k = k
        super().__init__(master, width=int(DW * k), height=int(DH * k), **kw)

    def _sc(self, args):
        out = []
        for a in args:
            if isinstance(a, (list, tuple)):
                out.extend(v * self.k for v in a)
            else:
                out.append(a * self.k)
        return out

    def _kw(self, kw):
        kw = dict(kw)
        if "width" in kw:
            kw["width"] = max(1, kw["width"] * self.k)
        if "font" in kw:
            kw["font"] = sfont(kw["font"], self.k)
        return kw

    def bbox(self, *args):
        b = super().bbox(*args)
        return tuple(v / self.k for v in b) if b else b


def _mk(name):
    base = getattr(tk.Canvas, "create_" + name)

    def f(self, *a, **kw):
        return base(self, *self._sc(a), **self._kw(kw))
    return f


for _n in ("polygon", "oval", "rectangle", "line", "text", "image", "window"):
    setattr(SCanvas, "create_" + _n, _mk(_n))


def rrect(c, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


# --------------------------------------------------------------------------
# Aplicação
# --------------------------------------------------------------------------
class App(tk.Tk):
    PASSIVE = {"info", "error", "pong", "lobby_update", "vote_progress",
               "continue_vote_cast"}

    def __init__(self, args):
        super().__init__()
        self.title("IMPOSTOR")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.k = max(0.5, min(1.0, (sw - 30) / DW, (sh - 140 - BAR_H) / DH))
        gw, gh = int(DW * self.k), int(DH * self.k) + BAR_H
        self.geometry("%dx%d+%d+%d" % (gw, gh, max(0, (sw - gw) // 2),
                                       max(0, (sh - gh - 80) // 2)))
        self.minsize(520, 420)
        self.configure(bg=BG)
        self.bind("<F11>", self.toggle_full)
        self.bind("<Escape>", lambda _e: self.attributes("-fullscreen", False))
        self.bind("<Return>", self.key_enter)
        fams = set(tkfont.families())
        self.fam = next((f for f in ("Segoe UI", "Helvetica Neue", "Ubuntu",
                                     "DejaVu Sans", "Arial") if f in fams), "TkDefaultFont")
        self.f_ban = (self.fam, 15, "bold")
        self.f_sub = (self.fam, 10)
        self.f_bub = (self.fam, 10)
        self.f_hdr = (self.fam, 8, "bold")
        self.f_name = (self.fam, 11, "bold")
        self.fn_name = tkfont.Font(family=self.fam, weight="bold",
                                   size=-max(7, int(round(11 * self.k * 4 / 3))))

        self.icons = load_icons()
        self.no_icons = not self.icons
        if self.no_icons:
            self.icons = [("Padrão", _placeholder("P"))]
        self.icon_map = dict(self.icons)
        self.bg_src = load_background()
        self.photos = {}
        self.sock = self.rfile = None
        self.inbox = queue.Queue()
        self.hist_win = self.rules_win = None
        self.taken_cf, self.polling, self.taken_q = set(), False, queue.Queue()
        self.reset_state()
        self.sel_skin = self.icons[0][0]
        self.build_login(args)
        self.build_game()
        self.show_login()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(40, self.pump)
        self.after(400, self.poll_taken)

    # ------------------------------------------------------------------
    def reset_state(self):
        self.me = ""
        self.players, self.skins, self.ready, self.gone = [], {}, set(), set()
        self.bubbles, self.active, self.spot = {}, None, None
        self.role = self.word = None
        self.theme, self.turn = "", 0
        self.state = "WAIT_PLAYERS"
        self.pending, self.pend_token, self.last_pending = None, 0, None
        self.deadline = None
        self.candidates = []
        self.ballot, self.tally = {}, {}
        self.history = []
        self.resp_pos = self.resp_total = 0
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
        size = max(8, int(dia * self.k))
        key = (skin, size)
        if key not in self.photos:
            img = self.icon_map.get(skin) or _placeholder(skin)
            self.photos[key] = ImageTk.PhotoImage(circle_avatar(img, size))
        return self.photos[key]

    def mw(self, text):
        return self.fn_name.measure(text) / self.k

    # ------------------------------------------------------------------
    # Tela inicial
    # ------------------------------------------------------------------
    def build_login(self, args):
        f = self.login = tk.Frame(self, bg=BG)
        tk.Label(f, text="IMPOSTOR", font=(self.fam, 32, "bold"), fg=GOLD,
                 bg=BG).pack(pady=(14, 0))
        tk.Label(f, text="Escolha seu nome e sua skin", font=(self.fam, 12),
                 fg="#a9c4d9", bg=BG).pack()
        row = tk.Frame(f, bg=BG)
        row.pack(pady=10)
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
                self.e_name = e
        grid = tk.Frame(f, bg=BG)
        grid.pack(pady=4)
        self.cells = []
        self.thumbs = [ImageTk.PhotoImage(circle_avatar(im, 76)) for _n, im in self.icons]
        self.thumbs_x = [ImageTk.PhotoImage(taken_avatar(im, 76)) for _n, im in self.icons]
        for i, (nm, _im) in enumerate(self.icons):
            cell = tk.Frame(grid, bg="#1d2d3c", highlightthickness=3,
                            highlightbackground="#1d2d3c", padx=6, pady=3)
            cell.grid(row=i // 5, column=i % 5, padx=6, pady=6)
            lb1 = tk.Label(cell, image=self.thumbs[i], bg="#1d2d3c")
            lb2 = tk.Label(cell, text=nm, fg="white", bg="#1d2d3c", font=(self.fam, 9),
                           wraplength=90)
            lb1.pack()
            lb2.pack()
            for w in (cell, lb1, lb2):
                w.bind("<Button-1>", lambda _e, k=i: self.select_skin(k))
            self.cells.append((cell, lb1, lb2))
        self.select_skin(0)
        tk.Label(f, text="Jogando em outra máquina? Em 'Servidor' coloque o IP de quem "
                         "rodou o server.py.", fg="#7f98ad", bg=BG,
                 font=(self.fam, 9)).pack()
        self.lbl_login = tk.Label(f, text="", fg="#ff8a80", bg=BG, font=(self.fam, 11),
                                  wraplength=700)
        self.lbl_login.pack(pady=(4, 0))
        tk.Button(f, text="ENTRAR NA SALA", font=(self.fam, 14, "bold"), bg="#46c35f",
                  fg="white", activebackground="#3aa850", relief="flat", padx=24, pady=8,
                  command=self.connect).pack(pady=8)
        self.e_name.bind("<Return>", lambda _e: self.connect())

    def is_taken(self, i):
        return self.icons[i][0].casefold() in self.taken_cf

    def select_skin(self, i):
        if self.is_taken(i):
            return                          # skin em uso: não dá para escolher
        self.sel_skin = self.icons[i][0]
        self.refresh_cells()

    def refresh_cells(self):
        for k, (cell, lb1, lb2) in enumerate(self.cells):
            taken = self.is_taken(k)
            lb1.configure(image=self.thumbs_x[k] if taken else self.thumbs[k])
            lb2.configure(fg="#6f7c88" if taken else "white")
            for w in (cell, lb1, lb2):
                w.configure(cursor="arrow" if taken else "hand2")
            sel = (not taken) and self.icons[k][0] == self.sel_skin
            cell.configure(highlightbackground=GOLD if sel else "#1d2d3c")

    # --- consulta periódica das skins já usadas (só na tela inicial) ---
    def poll_taken(self):
        self.after(2500, self.poll_taken)
        try:
            while True:
                self.apply_taken(self.taken_q.get_nowait())
        except queue.Empty:
            pass
        if self.polling or not self.login.winfo_ismapped():
            return
        try:
            port = int(self.v_port.get())
        except ValueError:
            return
        self.polling = True
        threading.Thread(target=self._query_taken, args=(self.v_host.get().strip(), port),
                         daemon=True).start()

    def _query_taken(self, host, port):
        taken = None
        try:
            with socket.create_connection((host, port), timeout=2) as s:
                s.settimeout(2)
                s.sendall(b'{"type":"skins_query","data":{}}\n')
                msg = json.loads(s.makefile("r", encoding="utf-8").readline())
                if msg.get("type") == "skins_taken":
                    taken = list(msg["data"]["skins"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        self.taken_q.put(taken)
        self.polling = False

    def apply_taken(self, taken):
        new = {str(t).casefold() for t in (taken or [])}
        if new == self.taken_cf:
            return
        self.taken_cf = new
        for i, (nm, _im) in enumerate(self.icons):          # a skin escolhida foi tomada?
            if nm == self.sel_skin and self.is_taken(i):
                free = next((k for k in range(len(self.icons)) if not self.is_taken(k)), None)
                if free is None:
                    self.sel_skin = ""
                    self.lbl_login.configure(text="Todas as skins estão em uso.", fg="#ff8a80")
                else:
                    self.sel_skin = self.icons[free][0]
                    self.lbl_login.configure(
                        text="A skin '%s' foi escolhida por outro jogador. Selecionei '%s'."
                             % (nm, self.sel_skin), fg="#f2c14e")
        self.refresh_cells()

    def show_login(self, msg=""):
        self.game.pack_forget()
        self.login.pack(fill="both", expand=True)
        if not msg and self.no_icons:
            msg = "Nenhuma imagem encontrada na pasta 'Images' (ao lado do client_gui.py)."
        self.lbl_login.configure(text=msg, fg="#ff8a80")
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
    # Tela do jogo: sala (canvas) + barra de ações
    # ------------------------------------------------------------------
    def build_game(self):
        g = self.game = tk.Frame(self, bg=BG)
        # a barra é empacotada primeiro: assim ela NUNCA é cortada
        self.bar = tk.Frame(g, bg="#0f1923", height=BAR_H)
        self.bar.pack(side="bottom", fill="x")
        self.bar.pack_propagate(False)
        self.canvas = SCanvas(g, self.k, highlightthickness=0, bg=BG)
        self.canvas.pack(side="top", expand=True)
        self.bg_photo = None
        self.canvas.create_image(0, 0, image="", anchor="nw", tags="bg")
        self.build_bg()
        self.canvas.bind("<Button-1>", self.on_click)
        btn = tk.Button(self.canvas, text="Ver a palavra (segure)", font=(self.fam, 9, "bold"),
                        bg="#26394d", fg="white", relief="flat", padx=8, pady=4)
        btn.bind("<ButtonPress-1>", lambda _e: self.set_peek(True))
        btn.bind("<ButtonRelease-1>", lambda _e: self.set_peek(False))
        self.peek_win = self.canvas.create_window(DW - 85, 30, window=btn, state="hidden")
        g.bind("<Configure>", self.on_resize)

    def build_bg(self):
        img = cover(self.bg_src, int(DW * self.k), int(DH * self.k))
        self.bg_photo = ImageTk.PhotoImage(img)
        self.canvas.itemconfigure("bg", image=self.bg_photo)

    def on_resize(self, ev):
        if ev.widget is not self.game:
            return
        if getattr(self, "_rs", None):
            self.after_cancel(self._rs)
        self._rs = self.after(80, self.apply_scale)

    def apply_scale(self):
        self._rs = None
        w, h = self.game.winfo_width(), self.game.winfo_height() - BAR_H
        if w < 50 or h < 50:
            return
        k = max(0.4, min(2.0, w / DW, h / DH))
        if abs(k - self.k) < 0.01:
            return
        self.k = k
        self.canvas.k = k
        self.canvas.configure(width=int(DW * k), height=int(DH * k))
        self.fn_name.configure(size=-max(7, int(round(11 * k * 4 / 3))))
        self.photos.clear()
        self.build_bg()
        tk.Canvas.coords(self.canvas, self.peek_win, (DW - 85) * k, 30 * k)
        if self.end:
            self.show_end()
        self.dirty = True

    def toggle_full(self, _e=None):
        self.attributes("-fullscreen", not self.attributes("-fullscreen"))

    def key_enter(self, _e=None):
        """Enter no lobby = 'Estou pronto'."""
        if (self.sock and self.pending is None and self.state == "WAIT_PLAYERS"
                and self.end is None and self.me and self.me not in self.ready
                and self.game.winfo_ismapped()
                and not isinstance(self.focus_get(), tk.Entry)):
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
    # Laço principal
    # ------------------------------------------------------------------
    def pump(self):
        try:
            self._pump_once()
        except Exception:
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
        if (self.active or self.spot) and now - self.blink_t > 0.5:
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
            if self.welcomed:
                self.to_login("Conexão com o servidor encerrada.")
            else:
                self.to_login("O servidor fechou a conexão antes de aceitar você. "
                              "Veja o erro no terminal do server.py ou teste outra porta.")
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
        self.say("Você entrou na sala!", "Skin: %s" % got if got else "")
        self.login.pack_forget()
        self.game.pack(fill="both", expand=True)

    def on_join_rejected(self, d):
        self.close_socket()
        self.show_login("Entrada recusada: %s" % d["reason"])

    def on_lobby_update(self, d):
        self.players = [p["name"] for p in d["players"]]
        self.skins = {p["name"]: p.get("skin", "") for p in d["players"]}
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
            self.bubbles, self.active, self.spot, self.peek = {}, None, None, False

    def on_start_game(self, d):
        self.players = d["players"]
        self.skins.update(d.get("skins", {}))
        self.theme, self.max_turns = d["theme"], d["max_turns"]
        self.bubbles, self.active, self.spot = {}, None, None
        self.ballot, self.tally, self.history = {}, {}, []
        self.end = None
        self.refresh_history()
        self.say("A partida começou!", "Tema: %s" % self.theme)
        self.pause(2200)

    def on_role_assign(self, d):
        self.role, self.word, self.theme = d["role"], d["word"], d["theme"]
        if self.role == "IMPOSTOR":
            self.say("VOCÊ É O IMPOSTOR!",
                     "Tema: %s  |  Você não sabe a palavra. Disfarce-se!" % self.theme)
        else:
            self.say("A palavra do jogo é: %s" % self.word,
                     "Tema: %s  |  Descubra quem não a conhece (sem entregar de bandeja)"
                     % self.theme)
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
        self.history.append({"turn": self.turn, "asker": d["asker"], "q": d["text"],
                             "answers": []})
        self.refresh_history()
        self.say("%s perguntou" % d["asker"], "Agora todos respondem, um de cada vez.")
        self.pause(900)

    def on_answer_prompt(self, d):
        self.active = d["responder"]
        self.resp_pos, self.resp_total = d["position"], d["total"]
        if d["responder"] == self.me:
            self.say("Sua vez de responder!", 'Pergunta de %s: "%s"' % (d["asker"], d["question"]))
            self.set_pending("answer", d.get("timeout"))
        else:
            self.say("%s irá responder" % d["responder"],
                     "Resposta %d/%d" % (d["position"], d["total"]))

    def on_broadcast_answer(self, d):
        self.bubbles[d["responder"]] = ("a", d["text"])
        if self.history:
            self.history[-1]["answers"].append((d["responder"], d["text"]))
            self.refresh_history()
        last = self.resp_pos and self.resp_pos == self.resp_total
        self.say("%s respondeu" % d["responder"],
                 "Última resposta da pergunta: leia com calma." if last else "")
        self.pause(6000 if last else 1100)      # a última resposta fica bem mais tempo

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

    def _show_continue_votes(self, d):
        for n in d["voters_vote"]:
            self.bubbles[n] = ("vote", "VOTAÇÃO")
        for n in d["voters_continue"]:
            self.bubbles[n] = ("cont", "CONTINUAR")

    def on_continue_vote_cast(self, d):
        self._show_continue_votes(d)
        sub = "VOTAÇÃO %d  ·  CONTINUAR %d" % (len(d["voters_vote"]), len(d["voters_continue"]))
        if d["waiting"]:
            sub += "  ·  faltam: " + ", ".join(d["waiting"])
        self.say("Todos devem votar!", sub)

    def on_continue_result(self, d):
        self._show_continue_votes(d)
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
        self.bubbles, self.active, self.spot = {}, None, d["impostor"]
        if d["impostor"] == self.me:
            self.say("CHANCE DE EMPATE: adivinhe a palavra!", "Tema: %s" % d["theme"])
            self.set_pending("guess", d.get("timeout"))
        else:
            self.say("%s foi descoberto!" % d["impostor"],
                     "CHANCE DE EMPATE: ele tenta adivinhar a palavra...")

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
            kind, _timeout = self.last_pending
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
        px, py = ev.x / self.k, ev.y / self.k
        for i, name in enumerate(self.players[:5]):
            x, y, dia = SEATS[i]
            if name != self.me and (px - x) ** 2 + (py - y) ** 2 <= (dia // 2) ** 2:
                self.cast_vote(name)
                return

    # ------------------------------------------------------------------
    # Janelas: histórico e regras
    # ------------------------------------------------------------------
    def _text_window(self, title, size):
        w = tk.Toplevel(self)
        w.title(title)
        w.geometry(size)
        w.configure(bg=BG)
        fr = tk.Frame(w, bg=BG)
        fr.pack(fill="both", expand=True, padx=8, pady=8)
        sb = tk.Scrollbar(fr)
        sb.pack(side="right", fill="y")
        txt = tk.Text(fr, wrap="word", bg="#0f1923", fg="#e8eef4", relief="flat",
                      font=(self.fam, 11), padx=14, pady=10, yscrollcommand=sb.set,
                      state="disabled", cursor="arrow")
        txt.pack(side="left", fill="both", expand=True)
        sb.config(command=txt.yview)
        txt.tag_configure("h", font=(self.fam, 12, "bold"), foreground=GOLD,
                          spacing1=12, spacing3=4)
        txt.tag_configure("t", spacing3=4)
        txt.tag_configure("turn", font=(self.fam, 12, "bold"), foreground=GOLD,
                          spacing1=14, spacing3=4)
        txt.tag_configure("q", font=(self.fam, 11, "bold"), foreground="#7fb2ff",
                          spacing1=8)
        txt.tag_configure("who", font=(self.fam, 11, "bold"), foreground="#ffd27a",
                          lmargin1=22, lmargin2=34)
        txt.tag_configure("ans", lmargin1=22, lmargin2=34)
        txt.tag_configure("dim", foreground="#8fa3b5")
        return w, txt

    def open_rules(self):
        if self.rules_win and self.rules_win.winfo_exists():
            self.rules_win.lift()
            return
        w, txt = self._text_window("Regras do jogo", "620x600")
        self.rules_win = w
        txt.configure(state="normal")
        for tag, text in RULES:
            txt.insert("end", text + "\n", tag)
        txt.configure(state="disabled")

    def open_history(self):
        if self.hist_win and self.hist_win.winfo_exists():
            self.hist_win.lift()
            self.refresh_history()
            return
        self.hist_win, self.hist_txt = self._text_window("Histórico da partida", "560x560")
        self.refresh_history()

    def refresh_history(self):
        if not (self.hist_win and self.hist_win.winfo_exists()):
            return
        txt = self.hist_txt
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        if not self.history:
            txt.insert("end", "Nenhuma pergunta feita ainda.\n", "dim")
        last_turn = None
        for e in self.history:
            if e["turn"] != last_turn:
                last_turn = e["turn"]
                txt.insert("end", "TURNO %s\n" % e["turn"], "turn")
            txt.insert("end", "%s perguntou: %s\n" % (e["asker"], e["q"]), "q")
            for who, ans in e["answers"]:
                txt.insert("end", who + ": ", "who")
                txt.insert("end", ans + "\n", "ans")
        txt.configure(state="disabled")
        txt.see("end")

    # ------------------------------------------------------------------
    # Barra de ações
    # ------------------------------------------------------------------
    def sync_bar(self):
        key = (self.pending, self.pend_token, self.state, self.me in self.ready,
               self.end is None)
        if key != self.bar_key:
            self.bar_key = key
            self.render_bar()

    def render_bar(self):
        for w in self.bar.winfo_children():
            w.destroy()
        mode = self.pending
        if mode is None and self.state == "WAIT_PLAYERS" and self.end is None:
            mode = "ready"
        side = tk.Frame(self.bar, bg="#0f1923")
        side.pack(side="right", padx=(4, 10))
        small = lambda t, cmd: tk.Button(side, text=t, command=cmd, bg="#26394d", fg="white",
                                         relief="flat", activebackground="#33506b",
                                         font=(self.fam, 10, "bold"), width=11, pady=2)
        small("Regras", self.open_rules).pack(pady=(14, 3))
        small("Histórico", self.open_history).pack()
        self.timer_lbl = tk.Label(self.bar, text="", fg=GOLD, bg="#0f1923",
                                  font=(self.fam, 11, "bold"))
        self.timer_lbl.pack(side="right", padx=6)
        box = tk.Frame(self.bar, bg="#0f1923")
        box.pack(side="left", expand=True, fill="both", padx=12)
        lab = lambda t: tk.Label(box, text=t, fg="white", bg="#0f1923",
                                 font=(self.fam, 11, "bold"))
        btn = lambda t, bg, cmd: tk.Button(box, text=t, bg=bg, fg="white", relief="flat",
                                           activebackground=bg, font=(self.fam, 11, "bold"),
                                           padx=14, pady=7, command=cmd)
        if mode == "ready":
            if self.me in self.ready:
                lab("Você está pronto. Aguardando os outros jogadores...").pack(pady=30)
            else:
                lab("Quando todos estiverem na sala, clique em:").pack(side="left", padx=8)
                btn("ESTOU PRONTO", "#46c35f", lambda: self.send("player_ready")
                    ).pack(side="left", pady=24)
        elif mode in PROMPTS:
            var = tk.StringVar()
            var.trace_add("write", lambda *_: var.set(var.get()[:200])
                          if len(var.get()) > 200 else None)
            lab(PROMPTS[mode] + ":").pack(side="left", padx=(0, 8))
            e = tk.Entry(box, textvariable=var, font=(self.fam, 12), width=34)
            e.pack(side="left", ipady=5)
            e.bind("<Return>", lambda _e: self.submit_text(var))
            e.focus_set()
            btn("ENVIAR", "#2d6cdf", lambda: self.submit_text(var)).pack(side="left", padx=8)
        elif mode == "continue":
            lab("Como seguir?").pack(side="left", padx=(0, 10))
            btn("VOTAÇÃO (votar no impostor)", "#d1382e",
                lambda: self.choose_continue("vote")).pack(side="left", padx=5, pady=24)
            btn("CONTINUAR (outro turno)", "#2e9d48",
                lambda: self.choose_continue("continue")).pack(side="left", padx=5)
        elif mode == "vote":
            lab("Quem é o Impostor?").pack(side="left", padx=(0, 10))
            for name in self.candidates:
                btn(name, "#6b4fd1", lambda n=name: self.cast_vote(n)
                    ).pack(side="left", padx=4, pady=24)
        else:
            lab("Aguarde: ainda não é a sua vez.").pack(pady=30)

    # ------------------------------------------------------------------
    # Desenho (tudo em coordenadas de projeto 1000x620)
    # ------------------------------------------------------------------
    def redraw(self):
        c = self.canvas
        c.delete("dyn")
        self.draw_players()
        if self.spot and self.spot in self.players:
            self.draw_spot()
        self.draw_bubbles()
        self.draw_chips()
        self.draw_banner()
        if self.end:
            self.draw_end()
        in_game = self.state in ("IN_GAME", "DECIDING", "VOTING", "GUESSING")
        c.itemconfigure(self.peek_win,
                        state="normal" if (self.role == "CIVILIAN" and in_game
                                           and not self.end) else "hidden")

    def draw_one(self, name, x, y, dia, ring_extra=None):
        c = self.canvas
        r = dia // 2
        gone = name in self.gone
        ring = "#6fd3ff" if name == self.me else "#f4f1e8"
        if name == self.active or ring_extra:
            ring = ring_extra or ("#ffd23f" if self.blink else "#ff9f1c")
        c.create_oval(x - r - 6, y - r - 6, x + r + 6, y + r + 6, fill=ring, outline="",
                      tags="dyn")
        c.create_image(x, y, image=self.photo(self.skins.get(name, ""), dia), tags="dyn")
        if gone:
            c.create_oval(x - r, y - r, x + r, y + r, fill="black", stipple="gray50",
                          outline="", tags="dyn")
        label = name + (" (você)" if name == self.me else "")
        if name in self.ready and self.state == "WAIT_PLAYERS":
            label = "✔ " + label
        if gone:
            label += " (saiu)"
        w = self.mw(label) + 26
        hot = name == self.active or ring_extra
        rrect(c, x - w / 2, y + r + 9, x + w / 2, y + r + 33, 10,
              fill="#555e66" if gone else "#1b2a38",
              outline=GOLD if hot else "#0d1620", width=2, tags="dyn")
        c.create_text(x, y + r + 21, text=label, font=self.f_name,
                      fill="#bbb" if gone else "white", tags="dyn")

    def draw_players(self):
        for i, name in enumerate(self.players[:5]):
            if name == self.spot:
                continue                    # o impostor descoberto vai para o centro
            x, y, dia = SEATS[i]
            self.draw_one(name, x, y, dia)

    def draw_spot(self):
        c = self.canvas
        c.create_rectangle(0, 0, DW, DH, fill="black", stipple="gray50", outline="",
                           tags="dyn")
        x, y, dia = SPOT
        self.draw_one(self.spot, x, y, dia, ring_extra="#ff4d5a" if self.blink else "#ffd23f")
        rrect(c, 250, 372, 750, 420, 16, fill="#7a1f2b", outline="#ffd23f", width=3,
              tags="dyn")
        c.create_text(500, 396, text="CHANCE DE EMPATE", font=(self.fam, 28, "bold"),
                      fill="#fff2c2", tags="dyn")

    def draw_bubbles(self):
        for name, (kind, text) in self.bubbles.items():
            if name not in self.players[:5]:
                continue
            if name == self.spot:
                x, y, dia = SPOT
                maxh = 90
            else:
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
        x0 = min(max(cx - bw / 2, 8), DW - 8 - bw)
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
            w = self.mw(txt) + 24
            rrect(c, DW / 2 - w / 2, 588, DW / 2 + w / 2, 614, 10, fill="#1b2a38",
                  outline="#0d1620", tags="dyn")
            c.create_text(DW / 2, 601, text=txt, font=self.f_name, fill=GOLD, tags="dyn")

    def draw_banner(self):
        c = self.canvas
        main, sub = self.banner
        if self.peek and self.word:
            main, sub = "A palavra do jogo é: %s" % self.word, "Tema: %s" % self.theme
        t1 = c.create_text(DW / 2, 20, text=main, font=self.f_ban, fill="white", width=660,
                           anchor="n", tags="dyn")
        b1 = c.bbox(t1)
        bottom, left, right = b1[3], b1[0], b1[2]
        if sub:
            t2 = c.create_text(DW / 2, b1[3] + 3, text=sub, font=self.f_sub, fill="#a9c4d9",
                               width=660, anchor="n", tags="dyn")
            b2 = c.bbox(t2)
            bottom, left, right = b2[3], min(left, b2[0]), max(right, b2[2])
        bg = rrect(c, left - 20, 8, right + 20, bottom + 9, 16, fill=BG, outline=GOLD,
                   width=2, tags="dyn")
        c.tag_lower(bg, t1)

    # ------------------------------------------------------------------
    # Tela final
    # ------------------------------------------------------------------
    def show_end(self):
        c = self.canvas
        c.delete("endwin")
        mk = lambda t, bg, cmd: tk.Button(c, text=t, bg=bg, fg="white", relief="flat",
                                          activebackground=bg, font=(self.fam, 12, "bold"),
                                          padx=14, pady=6, command=cmd)
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
        c.create_rectangle(0, 0, DW, DH, fill="black", stipple="gray50", outline="", tags="dyn")
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
