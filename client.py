#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
 client.py - Cliente gráfico (Tkinter + Pillow) do jogo multiplayer "IMPOSTOR"
==============================================================================

Papel na arquitetura
--------------------
Cliente do modelo cliente-servidor. Conecta-se ao server.py por um socket TCP
e fala o protocolo de aplicação IMPOSTOR/1.0 (JSON em UTF-8, uma mensagem por
linha, terminada em '\\n'):

    {"type": "<evento>", "data": {...}}

O cliente NÃO decide regras do jogo: apenas envia as intenções do jogador
(send_question, send_answer, send_continue_vote, send_vote, send_guess...) e
exibe os eventos que o servidor anuncia (métodos on_<type> da classe App).

Threads
-------
* Thread da interface (Tkinter): laço pump(), a cada 40 ms, consome a fila
  `inbox`, atualiza o estado local e redesenha a sala.
* Thread leitora (App.reader): bloqueia em readline() no socket e deposita
  cada mensagem em `inbox`. Os widgets só são tocados pela thread da interface.
* Threads curtas (_query_taken): consulta periódica das skins em uso (login).

Pastas ao lado deste arquivo
----------------------------
    Images/        ícones das skins (o NOME DO ARQUIVO é o nome da skin)
    background/    imagem de fundo da sala (qualquer .png/.jpg; usa a primeira)

Requisitos: Python 3.8+, Tkinter e Pillow (pip install pillow).
Uso:  python3 client.py [--host 127.0.0.1] [--port 5000] [--name Ana]
Atalhos: F11 tela cheia | Esc sai da tela cheia | Enter = "Estou pronto" no lobby
Reações: a faixa "Reagir" envia send_emoji; todos recebem broadcast_emoji e veem o emoji
por 3 s sobre o ícone do jogador (ou sobre o balão, se ele tiver um). Um novo emoji substitui o
anterior. Os emojis são desenhados com Pillow a partir de uma fonte de emoji colorida do sistema.
"""
import argparse
import json
import os
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
DW, DH, BAR_H = 1000, 620, 96          # tamanho de "projeto" da sala e altura mínima da barra
NARROW_W = 760                         # abaixo desta largura a barra de ações usa o layout compacto
EMOJI_BAR_H = 40                       # altura (estimada) da faixa de reações, para o 1º cálculo de escala
EMOJIS = ("🤔", "😂", "😖", "🥸", "😱", "🤥", "🤫")   # DEVE ser igual ao EMOJIS do server.py
EMOJI_SECS = 3.0                       # tempo que cada emoji fica na tela
EMO = 46                               # tamanho do emoji na sala (coordenadas de projeto)
EMO_R = 30                             # raio do círculo branco atrás do emoji
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
          "• Reações: use a faixa \"Reagir\" para mostrar um emoji sobre o seu ícone por 3 segundos.\n"
          "• F11 = tela cheia."),
]


# --------------------------------------------------------------------------
# Imagens externas (pastas Images/ e background/)
# --------------------------------------------------------------------------
def _placeholder(label):
    """Gera uma imagem 256x256 com a inicial do nome, usada quando falta a skin."""
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


# (arquivo da fonte de emoji colorida, tamanho exigido/ideal da fonte) por sistema operacional
EMOJI_FONTS = [
    (os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", "seguiemj.ttf"), 128),
    ("/System/Library/Fonts/Apple Color Emoji.ttc", 160),
    ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", 109),
    ("/usr/share/fonts/noto/NotoColorEmoji.ttf", 109),
    ("/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf", 109),
    ("/usr/share/fonts/google-noto-color-emoji/NotoColorEmoji.ttf", 109),
    (os.path.expanduser("~/.local/share/fonts/NotoColorEmoji.ttf"), 109),
]
RESAMPLE = getattr(getattr(Image, "Resampling", Image), "LANCZOS", None)


def _render_emoji(font, size, ch):
    """Desenha o emoji 'ch' com a fonte colorida e devolve um RGBA quadrado (ou None se não saiu nada)."""
    try:
        img = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((size // 2, size // 4), ch, font=font, embedded_color=True)
        box = img.getbbox()
        if not box:
            return None
        img = img.crop(box)
        side = max(img.size)
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        sq.paste(img, ((side - img.size[0]) // 2, (side - img.size[1]) // 2))
        return sq
    except Exception:
        return None


def load_emoji_sources():
    """Gera as imagens dos emojis (dict emoji -> imagem RGBA ou None) usando uma fonte colorida do sistema.

    Se nenhuma fonte for encontrada, os valores ficam None e o cliente desenha o emoji como texto.
    """
    out = {ch: None for ch in EMOJIS}
    for path, size in EMOJI_FONTS:
        if not os.path.isfile(path):
            continue
        try:
            font = ImageFont.truetype(path, size)
        except Exception:
            continue
        for ch in EMOJIS:
            out[ch] = _render_emoji(font, size, ch)
        if any(out.values()):
            break
    return out


def load_background():
    """Usa a primeira imagem da pasta background/ (ou uma cor lisa se não houver)."""
    for folder in ("background", "Background"):
        d = BASE / folder
        if d.is_dir():
            files = sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXTS)
            if files:
                return Image.open(files[0]).convert("RGB")
    print("Aviso: coloque uma imagem na pasta 'background' (ao lado do client.py).")
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
    """Recorta a imagem em círculo (com suavização) e devolve um RGBA d x d."""
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
    """Escala o tamanho de uma fonte (família, tamanho, ...) pelo fator k, em pixels negativos."""
    fam, size, *rest = font
    return (fam, -max(7, int(round(size * k * 4 / 3)))) + tuple(rest)


class SCanvas(tk.Canvas):
    """Canvas que desenha em coordenadas de projeto (DW x DH) e se escala sozinho pelo fator k."""
    def __init__(self, master, k, **kw):
        """Cria o canvas com tamanho DW*k x DH*k; k é o fator de escala atual."""
        self.k = k
        super().__init__(master, width=int(DW * k), height=int(DH * k), **kw)

    def _sc(self, args):
        """Multiplica por k todas as coordenadas recebidas (números ou listas/tuplas)."""
        out = []
        for a in args:
            if isinstance(a, (list, tuple)):
                out.extend(v * self.k for v in a)
            else:
                out.append(a * self.k)
        return out

    def _kw(self, kw):
        """Escala as opções width e font de um create_*."""
        kw = dict(kw)
        if "width" in kw:
            kw["width"] = max(1, kw["width"] * self.k)
        if "font" in kw:
            kw["font"] = sfont(kw["font"], self.k)
        return kw

    def bbox(self, *args):
        """Devolve a caixa delimitadora já convertida de volta para coordenadas de projeto."""
        b = super().bbox(*args)
        return tuple(v / self.k for v in b) if b else b


def _mk(name):
    """Fabrica a versão escalada de Canvas.create_<name>: multiplica coordenadas, largura e fonte por k."""
    base = getattr(tk.Canvas, "create_" + name)

    def f(self, *a, **kw):
        return base(self, *self._sc(a), **self._kw(kw))
    return f


for _n in ("polygon", "oval", "rectangle", "line", "text", "image", "window"):
    setattr(SCanvas, "create_" + _n, _mk(_n))


def rrect(c, x0, y0, x1, y1, r, **kw):
    """Desenha um retângulo com cantos arredondados (polígono suavizado) no canvas c."""
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


# --------------------------------------------------------------------------
# Aplicação
# --------------------------------------------------------------------------
class App(tk.Tk):
    """Janela principal: tela de login, sala do jogo (canvas), barra de ações e tratamento do protocolo."""
    PASSIVE = {"info", "error", "pong", "lobby_update", "vote_progress",
               "continue_vote_cast", "broadcast_emoji"}

    def __init__(self, args):
        """Monta a janela, carrega imagens, cria as telas e agenda pump() e poll_taken()."""
        super().__init__()
        self.title("IMPOSTOR")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.k = max(0.5, min(1.0, (sw - 30) / DW, (sh - 140 - BAR_H - EMOJI_BAR_H) / DH))
        gw, gh = int(DW * self.k), int(DH * self.k) + BAR_H + EMOJI_BAR_H
        self.geometry("%dx%d+%d+%d" % (gw, gh, max(0, (sw - gw) // 2),
                                       max(0, (sh - gh - 80) // 2)))
        self.minsize(440, 400)
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
        self.emoji_src = load_emoji_sources()
        self.photos, self.emo_photos = {}, {}
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
        """Zera todo o estado local da partida/conexão (jogadores, papel, balões, prazos...)."""
        self.me = ""
        self.players, self.skins, self.ready, self.gone = [], {}, set(), set()
        self.bubbles, self.active, self.spot = {}, None, None
        self.emojis, self.bubble_tops = {}, {}      # emojis ativos {nome: (emoji, expira)}; topo dos balões
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
        """Devolve (com cache) a foto circular da skin no diâmetro dia, já escalada por k."""
        size = max(8, int(dia * self.k))
        key = (skin, size)
        if key not in self.photos:
            img = self.icon_map.get(skin) or _placeholder(skin)
            self.photos[key] = ImageTk.PhotoImage(circle_avatar(img, size))
        return self.photos[key]

    def mw(self, text):
        """Mede a largura de um texto na fonte do nome, em coordenadas de projeto."""
        return self.fn_name.measure(text) / self.k

    # ------------------------------------------------------------------
    # Tela inicial
    # ------------------------------------------------------------------
    def build_login(self, args):
        """Constrói a tela inicial (responsiva): nome, servidor, porta, grade de skins rolável e botão de entrar."""
        f = self.login = tk.Frame(self, bg=BG)
        # O botão de entrar e o aviso são empacotados primeiro, embaixo: assim NUNCA são cortados.
        tk.Button(f, text="ENTRAR NA SALA", font=(self.fam, 14, "bold"), bg="#46c35f",
                  fg="white", activebackground="#3aa850", relief="flat", padx=24, pady=8,
                  command=self.connect).pack(side="bottom", pady=8)
        self.lbl_login = tk.Label(f, text="", fg="#ff8a80", bg=BG, font=(self.fam, 11),
                                  wraplength=700)
        self.lbl_login.pack(side="bottom", pady=(4, 0))
        self.lbl_hint = tk.Label(f, text="Jogando em outra máquina? Em 'Servidor' coloque o IP de "
                                         "quem rodou o server.py.", fg="#7f98ad", bg=BG,
                                 font=(self.fam, 9), wraplength=700)
        self.lbl_hint.pack(side="bottom")

        tk.Label(f, text="IMPOSTOR", font=(self.fam, 32, "bold"), fg=GOLD,
                 bg=BG).pack(side="top", pady=(14, 0))
        tk.Label(f, text="Escolha seu nome e sua skin", font=(self.fam, 12),
                 fg="#a9c4d9", bg=BG).pack(side="top")
        row = self.login_row = tk.Frame(f, bg=BG)
        row.pack(side="top", pady=10)
        self.v_name = tk.StringVar(value=args.name or "")
        self.v_host = tk.StringVar(value=args.host)
        self.v_port = tk.StringVar(value=str(args.port))
        self.login_pairs = []
        for label, var, w in (("Nome", self.v_name, 16), ("Servidor", self.v_host, 16),
                              ("Porta", self.v_port, 6)):
            pair = tk.Frame(row, bg=BG)
            tk.Label(pair, text=label, fg="white", bg=BG, font=(self.fam, 11)
                     ).pack(side="left", padx=(12, 4))
            e = tk.Entry(pair, textvariable=var, width=w, font=(self.fam, 12))
            e.pack(side="left")
            if label == "Nome":
                self.e_name = e
            self.login_pairs.append(pair)
        self.login_wide = None

        # Grade de skins dentro de um canvas rolável: com janela pequena ela rola em vez de cortar.
        holder = tk.Frame(f, bg=BG)
        holder.pack(side="top", fill="both", expand=True, padx=10, pady=4)
        self.skin_cv = tk.Canvas(holder, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(holder, orient="vertical", command=self.skin_cv.yview)
        self.skin_cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.skin_cv.pack(side="left", fill="both", expand=True)
        grid = self.skin_grid = tk.Frame(self.skin_cv, bg=BG)
        self.skin_win = self.skin_cv.create_window((0, 0), window=grid, anchor="n")
        self.cells, self.skin_cols = [], 0
        self.thumbs = [ImageTk.PhotoImage(circle_avatar(im, 76)) for _n, im in self.icons]
        self.thumbs_x = [ImageTk.PhotoImage(taken_avatar(im, 76)) for _n, im in self.icons]
        for i, (nm, _im) in enumerate(self.icons):
            cell = tk.Frame(grid, bg="#1d2d3c", highlightthickness=3,
                            highlightbackground="#1d2d3c", padx=6, pady=3)
            lb1 = tk.Label(cell, image=self.thumbs[i], bg="#1d2d3c")
            lb2 = tk.Label(cell, text=nm, fg="white", bg="#1d2d3c", font=(self.fam, 9),
                           wraplength=90)
            lb1.pack()
            lb2.pack()
            for w in (cell, lb1, lb2):
                w.bind("<Button-1>", lambda _e, k=i: self.select_skin(k))
            self.cells.append((cell, lb1, lb2))
        self.layout_skins(5)
        self.select_skin(0)
        grid.bind("<Configure>", lambda _e: self.skin_cv.configure(
            scrollregion=self.skin_cv.bbox("all")))
        self.skin_cv.bind("<Configure>", self.on_skin_resize)
        self.skin_cv.bind("<Enter>", lambda _e: self.bind_wheel(True))
        self.skin_cv.bind("<Leave>", lambda _e: self.bind_wheel(False))
        f.bind("<Configure>", self.on_login_resize)
        self.e_name.bind("<Return>", lambda _e: self.connect())

    def layout_skins(self, cols):
        """Distribui as skins na grade com 'cols' colunas (só refaz se o número de colunas mudou)."""
        cols = max(1, min(5, cols))
        if cols == self.skin_cols:
            return
        self.skin_cols = cols
        for i, (cell, _l1, _l2) in enumerate(self.cells):
            cell.grid(row=i // cols, column=i % cols, padx=6, pady=6)

    def on_skin_resize(self, ev):
        """Recalcula as colunas da grade de skins e recentraliza a grade ao mudar a largura."""
        self.skin_cv.coords(self.skin_win, ev.width // 2, 0)
        self.layout_skins(max(1, ev.width // 112))

    def on_login_resize(self, ev):
        """Login responsivo: empilha Nome/Servidor/Porta em janelas estreitas e ajusta as quebras de linha."""
        if ev.widget is not self.login:
            return
        wide = ev.width >= 700
        if wide != self.login_wide:
            self.login_wide = wide
            for pair in self.login_pairs:
                pair.pack_forget()
                if wide:
                    pair.pack(side="left")
                else:
                    pair.pack(side="top", pady=2)
        wrap = max(200, ev.width - 40)
        self.lbl_login.configure(wraplength=wrap)
        self.lbl_hint.configure(wraplength=wrap)

    def bind_wheel(self, on):
        """Liga/desliga a rolagem com o mouse na grade de skins (Windows, macOS e Linux)."""
        if on:
            self.bind_all("<MouseWheel>", self.on_wheel)
            self.bind_all("<Button-4>", self.on_wheel)
            self.bind_all("<Button-5>", self.on_wheel)
        else:
            for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.unbind_all(ev)

    def on_wheel(self, ev):
        """Rola a grade de skins com a roda do mouse, só quando o conteúdo é maior que a área visível."""
        bb = self.skin_cv.bbox("all")
        if not bb or bb[3] - bb[1] <= self.skin_cv.winfo_height():
            return
        if getattr(ev, "num", 0) == 4:
            step = -1
        elif getattr(ev, "num", 0) == 5:
            step = 1
        else:
            step = -1 if ev.delta > 0 else 1
        self.skin_cv.yview_scroll(step, "units")

    def is_taken(self, i):
        """True se a skin de índice i já está em uso por outro jogador (segundo o servidor)."""
        return self.icons[i][0].casefold() in self.taken_cf

    def select_skin(self, i):
        """Seleciona a skin de índice i, ignorando as que estão em uso."""
        if self.is_taken(i):
            return                          # skin em uso: não dá para escolher
        self.sel_skin = self.icons[i][0]
        self.refresh_cells()

    def refresh_cells(self):
        """Atualiza a grade de skins: marca a escolhida e escurece (com X) as indisponíveis."""
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
        """A cada 2,5 s, na tela de login, consulta em segundo plano as skins em uso."""
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
        """Thread curta: abre uma conexão, envia skins_query, lê skins_taken e entrega o resultado à fila."""
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
        """Aplica a lista de skins em uso; se a escolhida foi tomada, troca por outra livre."""
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
        """Exibe a tela de login com uma mensagem opcional (erro ou aviso)."""
        self.game.pack_forget()
        self.login.pack(fill="both", expand=True)
        if not msg and self.no_icons:
            msg = "Nenhuma imagem encontrada na pasta 'Images' (ao lado do client.py)."
        self.lbl_login.configure(text=msg, fg="#ff8a80")
        self.e_name.focus_set()

    def connect(self):
        """Valida o nome, abre o socket TCP, inicia a thread leitora e envia player_join."""
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
        """Constrói a tela do jogo: canvas da sala, barra de ações e botão 'Ver a palavra'."""
        g = self.game = tk.Frame(self, bg=BG)
        # a barra é empacotada primeiro: assim ela NUNCA é cortada. Sua altura acompanha o
        # conteúdo (layout compacto em janelas estreitas) e a sala é reescalada em seguida.
        self.bar = tk.Frame(g, bg="#0f1923")
        self.bar.pack(side="bottom", fill="x")
        self.bar.bind("<Configure>", lambda _e: self.schedule_scale())
        # Faixa de reações (emojis), logo acima da barra de ações.
        self.emo_bar = tk.Frame(g, bg="#0b131b")
        self.emo_bar.pack(side="bottom", fill="x")
        inner = tk.Frame(self.emo_bar, bg="#0b131b")
        inner.pack(pady=4)
        tk.Label(inner, text="Reagir:", fg="#7f98ad", bg="#0b131b",
                 font=(self.fam, 9)).pack(side="left", padx=(0, 6))
        self.emo_btns, self.emo_imgs, self._emo_enabled = [], [], True
        for ch in EMOJIS:
            src = self.emoji_src.get(ch)
            kw = {}
            if src is not None:
                ph = ImageTk.PhotoImage(src.resize((26, 26), RESAMPLE) if RESAMPLE else src)
                self.emo_imgs.append(ph)
                kw["image"] = ph
            else:
                kw.update(text=ch, font=(self.fam, 13))
            b = tk.Button(inner, bg="#1b2a38", activebackground="#33506b", relief="flat", bd=0,
                          padx=5, pady=2, cursor="hand2",
                          command=lambda e=ch: self.send_emoji(e), **kw)
            b.pack(side="left", padx=2)
            self.emo_btns.append(b)
        self.emo_bar.bind("<Configure>", lambda _e: self.schedule_scale())
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
        self.peek_win = self.canvas.create_window(DW - 12, 12, window=btn, anchor="ne",
                                                  state="hidden")
        g.bind("<Configure>", self.on_resize)

    def build_bg(self):
        """Redimensiona a imagem de fundo para a escala atual e a aplica ao canvas."""
        img = cover(self.bg_src, int(DW * self.k), int(DH * self.k))
        self.bg_photo = ImageTk.PhotoImage(img)
        self.canvas.itemconfigure("bg", image=self.bg_photo)

    def on_resize(self, ev):
        """Reage a mudanças de tamanho da janela (com 80 ms de atraso para evitar excesso)."""
        if ev.widget is not self.game:
            return
        self.schedule_scale()

    def schedule_scale(self):
        """Agenda apply_scale() (com 80 ms de atraso, juntando vários eventos seguidos)."""
        if getattr(self, "_rs", None):
            self.after_cancel(self._rs)
        self._rs = self.after(80, self.apply_scale)

    def apply_scale(self):
        """Recalcula o fator de escala k e reajusta canvas, fontes, fundo e fotos."""
        self._rs = None
        bar_h = self.bar.winfo_height() if self.bar.winfo_ismapped() else BAR_H
        emo_h = self.emo_bar.winfo_height() if self.emo_bar.winfo_ismapped() else EMOJI_BAR_H
        w = self.game.winfo_width()
        h = self.game.winfo_height() - max(bar_h, 40) - max(emo_h, 20)
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
        self.emo_photos.clear()
        self.build_bg()
        tk.Canvas.coords(self.canvas, self.peek_win, (DW - 12) * k, 12 * k)
        if self.end:
            self.show_end()
        self.dirty = True

    def toggle_full(self, _e=None):
        """Alterna o modo tela cheia (F11)."""
        self.attributes("-fullscreen", not self.attributes("-fullscreen"))

    def key_enter(self, _e=None):
        """Enter no lobby = 'Estou pronto'."""
        if (self.sock and self.pending is None and self.state == "WAIT_PLAYERS"
                and self.end is None and self.me and self.me not in self.ready
                and self.game.winfo_ismapped()
                and not isinstance(self.focus_get(), tk.Entry)):
            self.send("player_ready")

    def set_peek(self, v):
        """Liga/desliga a exibição temporária da palavra secreta (botão pressionado)."""
        self.peek = v
        self.dirty = True

    # ------------------------------------------------------------------
    # Rede
    # ------------------------------------------------------------------
    @staticmethod
    def reader(rfile, inbox):
        """Thread leitora: lê uma linha JSON por vez do socket e a põe na fila; ao fim põe __closed__."""
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
        """Envia uma mensagem {type, data} ao servidor; em falha de rede volta ao login."""
        try:
            payload = json.dumps({"type": type_, "data": data}, ensure_ascii=False) + "\n"
            self.sock.sendall(payload.encode("utf-8"))
        except (OSError, AttributeError):
            self.to_login("Conexão perdida.")

    def close_socket(self):
        """Fecha o socket (se existir) e o descarta."""
        try:
            self.sock.close()
        except (OSError, AttributeError):
            pass
        self.sock = None

    def to_login(self, msg):
        """Encerra a conexão, zera o estado e volta à tela de login com a mensagem msg."""
        if self.sock is None and self.login.winfo_ismapped():
            return
        self.close_socket()
        self.reset_state()
        self.show_login(msg)

    def leave_to_login(self):
        """Botão SAIR da tela final: avisa o servidor (player_quit), desconecta e volta ao início (login)."""
        if self.sock:
            self.send("player_quit")
            if self.sock is None:          # a falha de envio já levou ao login
                return
        self.canvas.delete("endwin")
        self.to_login("")

    def on_close(self):
        """Fecha a janela (X): avisa o servidor com player_quit e encerra o programa."""
        if self.sock:
            self.send("player_quit")
        self.close_socket()
        self.destroy()

    # ------------------------------------------------------------------
    # Laço principal
    # ------------------------------------------------------------------
    def pump(self):
        """Laço principal da interface: executa _pump_once() e se reagenda a cada 40 ms."""
        try:
            self._pump_once()
        except Exception:
            import traceback
            traceback.print_exc()
        self.after(40, self.pump)

    def _pump_once(self):
        """Consome mensagens da fila, pisca o destaque, envia 'pronto' pendente, redesenha e atualiza o cronômetro."""
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
        if self.emojis:                         # remove os emojis que já passaram de 3 s
            old = [n for n, (_e, until) in self.emojis.items() if now >= until]
            for n in old:
                del self.emojis[n]
            if old:
                self.dirty = True
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
        """Pausa o consumo de mensagens por ms milissegundos (dá tempo de ler a tela)."""
        self.pause_until = time.monotonic() + ms / 1000.0

    def say(self, main, sub=""):
        """Define o texto do banner superior (principal e secundário)."""
        self.banner = (main, sub)

    def handle(self, t, d):
        """Despacha uma mensagem do servidor para o método on_<type> correspondente."""
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
        """Marca que o jogador precisa agir (ask, answer, continue, vote ou guess) e inicia o prazo."""
        self.pending = kind
        self.last_pending = (kind, timeout)
        self.pend_token += 1
        self.deadline = time.monotonic() + timeout if timeout else None

    # ------------------------------------------------------------------
    # Eventos do servidor
    # ------------------------------------------------------------------
    def on_welcome(self, d):
        """welcome: entrada aceita; guarda limites da sala e abre a tela do jogo (lobby)."""
        self.welcomed = True
        self.state = "WAIT_PLAYERS"
        self.min_players, self.max_players = d["min_players"], d["max_players"]
        got = d.get("skin", self.sel_skin)
        self.skins[self.me] = got
        self.say("Você entrou na sala!", "Skin: %s" % got if got else "")
        self.login.pack_forget()
        self.game.pack(fill="both", expand=True)

    def on_join_rejected(self, d):
        """join_rejected: entrada recusada; fecha o socket e mostra o motivo no login."""
        self.close_socket()
        self.show_login("Entrada recusada: %s" % d["reason"])

    def on_lobby_update(self, d):
        """lobby_update: atualiza jogadores, skins e quem está pronto."""
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
        """state_change: guarda o estado do servidor; no lobby limpa balões e destaques."""
        self.state = d["state"]
        if self.state == "WAIT_PLAYERS":
            self.bubbles, self.active, self.spot, self.peek = {}, None, None, False

    def on_start_game(self, d):
        """start_game: nova partida; guarda jogadores, tema e limites e zera balões e histórico."""
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
        """role_assign: recebe o papel (CIVILIAN ou IMPOSTOR) e, se civil, a palavra secreta."""
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
        """turn_start: início de turno; mostra a ordem das perguntas."""
        self.turn = d["turn"]
        self.say("Turno %d" % d["turn"], "Ordem das perguntas: " + " > ".join(d["order"]))
        self.pause(1500)

    def on_ask_prompt(self, d):
        """ask_prompt: vez de alguém perguntar; se for a minha, habilita o campo de pergunta."""
        self.bubbles, self.active = {}, d["asker"]
        if d["asker"] == self.me:
            self.say("Sua vez de perguntar!", "Faça UMA pergunta sobre a palavra para todos.")
            self.set_pending("ask", d.get("timeout"))
        else:
            self.say("%s irá perguntar" % d["asker"],
                     "Pergunta %d/%d do turno %d" % (d["step"], d["steps"], d["turn"]))

    def on_broadcast_question(self, d):
        """broadcast_question: mostra a pergunta no balão e a registra no histórico."""
        self.bubbles[d["asker"]] = ("q", d["text"])
        self.history.append({"turn": self.turn, "asker": d["asker"], "q": d["text"],
                             "answers": []})
        self.refresh_history()
        self.say("%s perguntou" % d["asker"], "Agora todos respondem, um de cada vez.")
        self.pause(900)

    def on_answer_prompt(self, d):
        """answer_prompt: vez de alguém responder; se for a minha, habilita o campo de resposta."""
        self.active = d["responder"]
        self.resp_pos, self.resp_total = d["position"], d["total"]
        if d["responder"] == self.me:
            self.say("Sua vez de responder!", 'Pergunta de %s: "%s"' % (d["asker"], d["question"]))
            self.set_pending("answer", d.get("timeout"))
        else:
            self.say("%s irá responder" % d["responder"],
                     "Resposta %d/%d" % (d["position"], d["total"]))

    def on_broadcast_answer(self, d):
        """broadcast_answer: mostra a resposta no balão e a registra no histórico."""
        self.bubbles[d["responder"]] = ("a", d["text"])
        if self.history:
            self.history[-1]["answers"].append((d["responder"], d["text"]))
            self.refresh_history()
        last = self.resp_pos and self.resp_pos == self.resp_total
        self.say("%s respondeu" % d["responder"],
                 "Última resposta da pergunta: leia com calma." if last else "")
        self.pause(6000 if last else 1100)      # a última resposta fica bem mais tempo

    def on_action_timeout(self, d):
        """action_timeout: um jogador não agiu a tempo; o passo foi pulado."""
        self.say("%s não agiu a tempo" % d["player"], "O passo (%s) foi pulado." % d["action"])
        self.pause(1500)

    def on_turn_end(self, d):
        """turn_end: todos já perguntaram; o turno terminou."""
        self.active = None
        self.say("Fim do turno %d" % d["turn"], "Todos já fizeram sua pergunta.")
        self.pause(1500)

    def on_ask_continue(self, d):
        """ask_continue: abre a decisão Continuar x Votação e habilita os botões."""
        self.bubbles, self.active = {}, None
        self.say("Todos devem votar!", "Continuar (outro turno) ou Votação do impostor?")
        self.set_pending("continue", d.get("timeout"))

    def _show_continue_votes(self, d):
        """Mostra, nos balões, quem votou em Votação e quem votou em Continuar."""
        for n in d["voters_vote"]:
            self.bubbles[n] = ("vote", "VOTAÇÃO")
        for n in d["voters_continue"]:
            self.bubbles[n] = ("cont", "CONTINUAR")

    def on_continue_vote_cast(self, d):
        """continue_vote_cast: voto parcial (público); mostra contagem e quem falta votar."""
        self._show_continue_votes(d)
        sub = "VOTAÇÃO %d  ·  CONTINUAR %d" % (len(d["voters_vote"]), len(d["voters_continue"]))
        if d["waiting"]:
            sub += "  ·  faltam: " + ", ".join(d["waiting"])
        self.say("Todos devem votar!", sub)

    def on_continue_result(self, d):
        """continue_result: resultado da decisão (vote ou continue)."""
        self._show_continue_votes(d)
        self.say("Vamos para a votação do impostor!" if d["decision"] == "vote"
                 else "Vamos jogar outro turno!",
                 "VOTAÇÃO %d  ·  CONTINUAR %d" % (d["votes_vote"], d["votes_continue"]))
        self.pause(3200)

    def on_start_vote(self, d):
        """start_vote: abre a votação do impostor; os candidatos excluem o próprio jogador."""
        self.bubbles, self.active = {}, None
        self.candidates = [c for c in d["candidates"] if c != self.me]
        self.say("Todos devem votar: quem é o Impostor?",
                 "Clique em um jogador ou use os botões abaixo.")
        self.set_pending("vote", d.get("timeout"))

    def on_vote_progress(self, d):
        """vote_progress: mostra quantos votos foram recebidos, sem revelar em quem."""
        self.say("Todos devem votar: quem é o Impostor?",
                 "Votos recebidos: %d/%d" % (d["received"], d["total"]))

    def on_vote_result(self, d):
        """vote_result: apuração; guarda e exibe quem votou em quem e a contagem."""
        self.ballot, self.tally = d["ballot"], d["tally"]
        self.bubbles = {v: ("ballot", t) for v, t in d["ballot"].items()}
        self.say("Votação encerrada!",
                 "Empate: ninguém foi o mais votado." if d["tie"]
                 else "Mais votado: %s" % d["accused"])
        self.pause(4500)

    def on_request_guess(self, d):
        """request_guess: impostor descoberto; se for eu, habilito o campo de palpite."""
        self.bubbles, self.active, self.spot = {}, None, d["impostor"]
        if d["impostor"] == self.me:
            self.say("CHANCE DE EMPATE: adivinhe a palavra!", "Tema: %s" % d["theme"])
            self.set_pending("guess", d.get("timeout"))
        else:
            self.say("%s foi descoberto!" % d["impostor"],
                     "CHANCE DE EMPATE: ele tenta adivinhar a palavra...")

    def on_guess_result(self, d):
        """guess_result: mostra o palpite do impostor e se acertou."""
        self.bubbles[d["impostor"]] = ("guess", d["guess"] or "(sem palpite)")
        self.say("ACERTOU!" if d["correct"] else "ERROU!", "Palpite de %s" % d["impostor"])
        self.pause(2800)

    def on_end_game(self, d):
        """end_game: fim da partida; guarda o resultado e mostra a tela final."""
        self.end = d
        self.active = None
        self.show_end()

    def on_info(self, d):
        """info: aviso do servidor; registra quem se desconectou para exibir o avatar apagado."""
        m = d["message"]
        if m.endswith(" desconectou-se."):
            self.gone.add(m[:-len(" desconectou-se.")])
        self.say(m)
        self.pause(1200)

    def on_broadcast_emoji(self, d):
        """broadcast_emoji: um jogador reagiu; o emoji fica 3 s e substitui o anterior desse jogador."""
        name, emoji = d.get("player"), d.get("emoji")
        if name and emoji in EMOJIS:
            self.emojis[name] = (emoji, time.monotonic() + EMOJI_SECS)

    def send_emoji(self, emoji):
        """Envia send_emoji ao servidor (botões da faixa 'Reagir'); ignorado fora da sala ou na tela final."""
        if self.sock and self.welcomed and self.end is None:
            self.send("send_emoji", emoji=emoji)

    def set_emoji_buttons(self, enabled):
        """Habilita/desabilita os botões de emoji (desabilitados na tela final)."""
        if enabled != self._emo_enabled:
            self._emo_enabled = enabled
            for b in self.emo_btns:
                b.configure(state="normal" if enabled else "disabled")

    def on_error(self, d):
        """error: aviso do servidor; em INVALID_INPUT reabre a entrada para nova tentativa."""
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
        """Envia pergunta, resposta ou palpite digitado, conforme a ação pendente."""
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
        """Envia send_continue_vote com a escolha 'vote' ou 'continue'."""
        if self.pending != "continue":
            return
        self.pending = None
        self.pend_token += 1
        self.send("send_continue_vote", choice=choice)

    def cast_vote(self, target):
        """Envia send_vote no jogador escolhido (o voto só aparece para quem votou)."""
        if self.pending != "vote":
            return
        self.pending = None
        self.pend_token += 1
        self.bubbles[self.me] = ("ballot", target)     # só você vê o seu voto
        self.send("send_vote", target=target)
        self.say("Voto enviado!", "Aguardando os demais...")
        self.dirty = True

    def on_click(self, ev):
        """Clique no canvas: durante a votação, vota no avatar clicado."""
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
        """Cria uma janela com texto rolável e as tags de estilo usadas por regras e histórico."""
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
        """Abre (ou traz à frente) a janela de regras do jogo."""
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
        """Abre (ou traz à frente) a janela de histórico de perguntas e respostas."""
        if self.hist_win and self.hist_win.winfo_exists():
            self.hist_win.lift()
            self.refresh_history()
            return
        self.hist_win, self.hist_txt = self._text_window("Histórico da partida", "560x560")
        self.refresh_history()

    def refresh_history(self):
        """Redesenha o histórico da partida na janela, se ela estiver aberta."""
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
        """Reconstrói a barra de ações apenas quando o estado que a define mudou."""
        key = (self.pending, self.pend_token, self.state, self.me in self.ready,
               self.end is None, self.game.winfo_width() < NARROW_W)
        if key != self.bar_key:
            self.bar_key = key
            self.render_bar()

    def render_bar(self):
        """Monta a barra de ações (responsiva): pronto, campo de texto, Continuar/Votação, candidatos ou espera.

        Janela larga (>= NARROW_W): uma linha, com Regras/Histórico e o cronômetro à direita.
        Janela estreita: duas camadas (cronômetro + Regras/Histórico em cima; pergunta e controles
        embaixo), com textos que quebram linha e botões em grade, para que nada fique cortado.
        """
        for w in self.bar.winfo_children():
            w.destroy()
        mode = self.pending
        if mode is None and self.state == "WAIT_PLAYERS" and self.end is None:
            mode = "ready"
        CB = "#0f1923"
        narrow = self.game.winfo_width() < NARROW_W
        small_btn = lambda parent, t, cmd, **kw: tk.Button(
            parent, text=t, command=cmd, bg="#26394d", fg="white", relief="flat",
            activebackground="#33506b", font=(self.fam, 10 if not narrow else 9, "bold"), **kw)

        if narrow:
            top = tk.Frame(self.bar, bg=CB)
            top.pack(side="top", fill="x", padx=8, pady=(4, 0))
            self.timer_lbl = tk.Label(top, text="", fg=GOLD, bg=CB, font=(self.fam, 11, "bold"))
            self.timer_lbl.pack(side="left")
            small_btn(top, "Histórico", self.open_history, padx=8, pady=0).pack(side="right",
                                                                                padx=(4, 0))
            small_btn(top, "Regras", self.open_rules, padx=8, pady=0).pack(side="right")
            box = tk.Frame(self.bar, bg=CB)
            box.pack(side="top", fill="x", padx=8, pady=(2, 6))
        else:
            tk.Frame(self.bar, bg=CB, width=1, height=BAR_H).pack(side="left")   # altura mínima
            side = tk.Frame(self.bar, bg=CB)
            side.pack(side="right", padx=(4, 10))
            small_btn(side, "Regras", self.open_rules, width=11, pady=2).pack(pady=(14, 3))
            small_btn(side, "Histórico", self.open_history, width=11, pady=2).pack()
            self.timer_lbl = tk.Label(self.bar, text="", fg=GOLD, bg=CB,
                                      font=(self.fam, 11, "bold"))
            self.timer_lbl.pack(side="right", padx=6)
            box = tk.Frame(self.bar, bg=CB)
            box.pack(side="left", expand=True, fill="both", padx=12)

        def lead(text, wide_pady=0):
            """Texto de orientação: à esquerda (largo) ou em cima, com quebra de linha (estreito)."""
            lb = tk.Label(box, text=text, fg="white", bg=CB, font=(self.fam, 11, "bold"))
            if narrow:
                lb.configure(justify="left", anchor="w", wraplength=max(120, self.game.winfo_width() - 30))
                lb.pack(side="top", fill="x")
                box.bind("<Configure>", lambda e, l=lb: l.configure(wraplength=max(120, e.width - 4)))
            else:
                lb.pack(side="left", padx=(0, 10), pady=wide_pady)
            return lb

        ctl = tk.Frame(box, bg=CB) if narrow else box

        def btn(t, bg, cmd):
            return tk.Button(ctl, text=t, bg=bg, fg="white", relief="flat", activebackground=bg,
                             font=(self.fam, 10 if narrow else 11, "bold"),
                             padx=10 if narrow else 14, pady=4 if narrow else 7, command=cmd)

        def place(widgets, colw=170):
            """Largo: botões lado a lado. Estreito: grade que reorganiza as colunas ao redimensionar."""
            if not narrow:
                for b in widgets:
                    b.pack(side="left", padx=5, pady=24)
                return
            state = {"cols": 0}

            def relayout(_e=None):
                cols = max(1, min(len(widgets), (ctl.winfo_width() or self.game.winfo_width()) // colw))
                if cols == state["cols"]:
                    return
                state["cols"] = cols
                for c_ in range(len(widgets)):
                    ctl.grid_columnconfigure(c_, weight=1 if c_ < cols else 0)
                for i, b in enumerate(widgets):
                    b.grid(row=i // cols, column=i % cols, padx=3, pady=3, sticky="ew")
            ctl.bind("<Configure>", relayout)
            relayout()

        if narrow:
            def start_ctl():
                ctl.pack(side="top", fill="x", pady=(4, 0))
        else:
            def start_ctl():
                pass

        if mode == "ready":
            if self.me in self.ready:
                lead("Você está pronto. Aguardando os outros jogadores...", 30)
            else:
                lead("Quando todos estiverem na sala, clique em:")
                start_ctl()
                place([btn("ESTOU PRONTO", "#46c35f", lambda: self.send("player_ready"))])
        elif mode in PROMPTS:
            var = tk.StringVar()
            var.trace_add("write", lambda *_: var.set(var.get()[:200])
                          if len(var.get()) > 200 else None)
            lead(PROMPTS[mode] + ":")
            start_ctl()
            send_b = btn("ENVIAR", "#2d6cdf", lambda: self.submit_text(var))
            send_b.pack(side="right", padx=(8, 0))
            e = tk.Entry(ctl, textvariable=var, font=(self.fam, 12), width=10)
            e.pack(side="left", fill="x", expand=True, ipady=5, pady=0 if narrow else 24)
            e.bind("<Return>", lambda _e: self.submit_text(var))
            e.focus_set()
        elif mode == "continue":
            lead("Como seguir?")
            start_ctl()
            place([btn("VOTAÇÃO (votar no impostor)", "#d1382e",
                       lambda: self.choose_continue("vote")),
                   btn("CONTINUAR (outro turno)", "#2e9d48",
                       lambda: self.choose_continue("continue"))], colw=210)
        elif mode == "vote":
            lead("Quem é o Impostor?")
            start_ctl()
            place([btn(name, "#6b4fd1", lambda n=name: self.cast_vote(n))
                   for name in self.candidates], colw=120)
        else:
            lead("Aguarde: ainda não é a sua vez.", 30)

    # ------------------------------------------------------------------
    # Desenho (tudo em coordenadas de projeto 1000x620)
    # ------------------------------------------------------------------
    def redraw(self):
        """Redesenha a sala: avatares, destaque do impostor, balões, tema e banner."""
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
        else:
            self.draw_emojis()
        self.set_emoji_buttons(self.end is None)
        in_game = self.state in ("IN_GAME", "DECIDING", "VOTING", "GUESSING")
        c.itemconfigure(self.peek_win,
                        state="normal" if (self.role == "CIVILIAN" and in_game
                                           and not self.end) else "hidden")

    def draw_one(self, name, x, y, dia, ring_extra=None):
        """Desenha um avatar com anel, nome e marcas (pronto, você, saiu)."""
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
        """Desenha os jogadores nos assentos, exceto o impostor em destaque."""
        for i, name in enumerate(self.players[:5]):
            if name == self.spot:
                continue                    # o impostor descoberto vai para o centro
            x, y, dia = SEATS[i]
            self.draw_one(name, x, y, dia)

    def draw_spot(self):
        """Desenha o impostor descoberto no centro, com a faixa 'CHANCE DE EMPATE'."""
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
        """Desenha o balão (pergunta, resposta, voto ou palpite) de cada jogador."""
        self.bubble_tops = {}
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
            self.bubble_tops[name] = self.bubble(x, y - dia // 2 - 12, hdr, text, fill, edge,
                                                 hcol, 250, maxh)

    def bubble(self, cx, bottom, hdr, text, fill, edge, hcol, maxw, maxh):
        """Desenha um balão de fala, reduzindo o texto com reticências se não couber; devolve o y do topo."""
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
        return y0

    def emo_photo(self, emoji):
        """Imagem do emoji na escala atual (com cache); None se não há fonte de emoji colorida."""
        src = self.emoji_src.get(emoji)
        if src is None:
            return None
        size = max(10, int(EMO * self.k))
        key = (emoji, size)
        if key not in self.emo_photos:
            self.emo_photos[key] = ImageTk.PhotoImage(src.resize((size, size), RESAMPLE)
                                                      if RESAMPLE else src)
        return self.emo_photos[key]

    def draw_emojis(self):
        """Desenha cada emoji ativo sobre o ícone do jogador, ou sobre o balão se ele tiver um."""
        c = self.canvas
        for name, (emoji, _until) in self.emojis.items():
            if name not in self.players[:5]:
                continue
            x, y, dia = SPOT if name == self.spot else SEATS[self.players.index(name)]
            top = self.bubble_tops.get(name)
            base = (top - 4) if top is not None else (y - dia // 2 - 12)   # parte de baixo do emoji
            cy = max(EMO_R + 4, base - EMO_R)
            c.create_oval(x - EMO_R, cy - EMO_R, x + EMO_R, cy + EMO_R, fill="white",
                          outline="#cfd8e3", width=2, tags="dyn")
            ph = self.emo_photo(emoji)
            if ph is not None:
                c.create_image(x, cy, image=ph, tags="dyn")
            else:
                c.create_text(x, cy, text=emoji, font=(self.fam, 24), tags="dyn")

    def draw_chips(self):
        """Desenha a etiqueta com o tema e o número do turno."""
        c = self.canvas
        if self.theme and self.state != "WAIT_PLAYERS":
            txt = "TEMA: %s%s" % (self.theme, ("   ·   Turno %d" % self.turn) if self.turn else "")
            w = self.mw(txt) + 24
            rrect(c, DW / 2 - w / 2, 588, DW / 2 + w / 2, 614, 10, fill="#1b2a38",
                  outline="#0d1620", tags="dyn")
            c.create_text(DW / 2, 601, text=txt, font=self.f_name, fill=GOLD, tags="dyn")

    def draw_banner(self):
        """Desenha o banner superior (ou a palavra secreta enquanto o botão é segurado)."""
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
        """Cria os botões JOGAR DE NOVO e SAIR (volta ao login) da tela final, centralizados no canvas."""
        c = self.canvas
        c.delete("endwin")
        # Os dois botões ficam num único quadro centralizado: em janelas pequenas não se sobrepõem.
        small = self.k < 0.7
        fr = tk.Frame(c, bg=BG)
        mk = lambda t, bg, cmd: tk.Button(fr, text=t, bg=bg, fg="white", relief="flat",
                                          activebackground=bg,
                                          font=(self.fam, 10 if small else 12, "bold"),
                                          padx=10 if small else 14, pady=3 if small else 6,
                                          command=cmd)
        mk("JOGAR DE NOVO", "#46c35f", lambda: self.close_end(True)).pack(side="left", padx=6)
        mk("SAIR", "#d1382e", self.leave_to_login).pack(side="left", padx=6)
        c.create_window(500, 538, window=fr, tags="endwin")

    def close_end(self, again):
        """Fecha a tela final; se again=True, marca o jogador como pronto para nova partida."""
        self.canvas.delete("endwin")
        self.end = None
        self.want_ready = again
        self.dirty = True

    def draw_end(self):
        """Desenha a tela final: resultado, impostor, tema, palavra, votos e palpite."""
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
    """Lê os argumentos de linha de comando (--host, --port, --name) e inicia a interface."""
    ap = argparse.ArgumentParser(description="Frontend gráfico do jogo IMPOSTOR")
    ap.add_argument("--host", default="127.0.0.1", help="IP/host do servidor")
    ap.add_argument("--port", type=int, default=5000, help="porta do servidor")
    ap.add_argument("--name", help="seu apelido (1-15 letras/números/_/-)")
    App(ap.parse_args()).mainloop()


if __name__ == "__main__":
    main()
