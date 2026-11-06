# 🕵️ IMPOSTOR — Jogo multiplayer de dedução social sobre sockets TCP

> Projeto acadêmico de **Redes de Computadores / Sistemas Distribuídos**.
> Arquitetura **Cliente-Servidor**, comunicação por **sockets TCP**, servidor **multithread**,
> com um **protocolo de camada de aplicação próprio** (`IMPOSTOR/1.0`).
> Linguagem: **Python 3** — somente biblioteca padrão.

---

## Sumário

1. [Propósito e Funcionamento da Aplicação](#1-propósito-e-funcionamento-da-aplicação)
2. [Requisitos Mínimos de Funcionamento](#2-requisitos-mínimos-de-funcionamento)
3. [Motivação da Escolha do Protocolo de Transporte (TCP)](#3-motivação-da-escolha-do-protocolo-de-transporte-tcp)
4. [Protocolo da Camada de Aplicação — `IMPOSTOR/1.0`](#4-protocolo-da-camada-de-aplicação--impostor10)
5. [Arquitetura e Concorrência](#5-arquitetura-e-concorrência)
6. [Testes realizados](#6-testes-realizados)
7. [Decisões de projeto e diferenciais](#7-decisões-de-projeto-e-diferenciais)
8. [Limitações conhecidas e trabalhos futuros](#8-limitações-conhecidas-e-trabalhos-futuros)
9. [Estrutura do repositório](#9-estrutura-do-repositório)

---

## 1. Propósito e Funcionamento da Aplicação

### 1.1 O que é

**Impostor** é um jogo de dedução social para **3 a 5 jogadores**, inspirado em jogos como *Among Us* e *Spyfall*.
Todos recebem um **tema** (ex.: `COMIDA`). Todos os jogadores **normais** também recebem a **palavra secreta**
(ex.: `FEIJÃO`), mas **um jogador sorteado é o Impostor** e **não sabe a palavra** — só conhece o tema.
Em cada turno, **um jogador faz uma pergunta para TODOS os outros**, e cada um responde. O Impostor tenta se
disfarçar sem saber a palavra e, no final, todos votam em quem acham que é o Impostor.

### 1.2 Regras implementadas

| # | Regra | Onde é aplicada |
|---|-------|-----------------|
| 1 | O servidor aceita de **3 a 5** jogadores. A 6ª conexão é recusada (`join_rejected`: sala cheia). | `server.py › _handshake` |
| 2 | A partida só começa quando há **≥ 3 jogadores e todos** enviaram `player_ready`. | `server.py › _maybe_start` |
| 3 | O servidor **sorteia** um tema, uma palavra e **1 Impostor**. | `server.py › _run_game` |
| 4 | Jogadores normais recebem **tema + palavra**; o Impostor recebe **só o tema** e o aviso de que é o Impostor. | `role_assign` (enviado individualmente) |
| 5 | **Rodadas por turnos controlados pelo servidor:** em cada turno, **um jogador faz UMA pergunta para TODOS os outros**; o servidor faz **broadcast** da pergunta e, em seguida, chama **cada um dos demais, um por vez**, para responder, retransmitindo cada resposta à sala. Em uma rodada, **cada jogador pergunta uma vez**. | `server.py › _play_round` |
| 6 | Após **3 rodadas** (configurável: `--rounds`), o servidor pergunta: *"Desejam votar no impostor ou jogar mais uma rodada?"*. Se a **maioria** escolher votar, abre a votação. | `server.py › _ask_continue` |
| 7 | **Votação:** cada jogador envia seu voto ao servidor. | `server.py › _voting_phase` |
| 8 | Mais votado **não** é o Impostor → **Impostor vence**. | `_voting_phase` |
| 9 | Mais votado **é** o Impostor → ele perde a votação, **mas ganha uma chance de adivinhar a palavra**. | `server.py › _guess_phase` |
| 10 | Impostor **acerta** a palavra → **EMPATE**. Impostor **erra** → **Impostor perde definitivamente**. | `_guess_phase` |

**Regras complementares (decisões do projeto para cobrir casos de borda):**

* **Empate na votação** (dois ou mais jogadores com o mesmo número de votos no topo, ou nenhum voto) →
  ninguém é eliminado → o **Impostor vence**.
* **"Maioria"** para votar significa **mais da metade dos jogadores presentes**. **Empate ou omissão =
  mais uma rodada.** Para o jogo não ser infinito, ao atingir `--max-rounds` (padrão 10) a votação é obrigatória.
* **Respostas em sequência e públicas:** cada resposta é retransmitida na hora, então quem responde depois já viu as
  respostas anteriores (é aí que o Impostor tenta se camuflar). A ordem de quem pergunta é sorteada a cada rodada.
* **Ninguém vota em si mesmo** (o servidor rejeita).
* O palpite do Impostor ignora **acentos, maiúsculas e espaços extras** (`feijao` = `FEIJÃO`).
* **Tempo limite:** se um jogador não agir no prazo (padrão 60 s para perguntar/responder, 45 s para votar),
  o turno é pulado / o voto é computado como abstenção — o jogo nunca trava.
* Se o **Impostor desconectar** ou restarem **menos de 3 jogadores**, a partida é encerrada (`ABORTED`).
* Ao terminar, a sala **volta ao lobby** e todos podem jogar **de novo** sem reconectar.

### 1.3 Visão geral do fluxo de uma partida

```
 LOBBY ──► RODADAS (N turnos por rodada) ──► "votar ou continuar?" ──► VOTAÇÃO ──┬──► Impostor vence
   ▲            ▲                                   │                             │
   │            └────────── "continuar" ◄───────────┘                             └──► Impostor descoberto ──► PALPITE ──► Empate / Impostor perde
   └───────────────────────────────── nova partida ◄───────────────────────────────────────────────────────────────────────────┘
```

**Como os turnos funcionam.** A cada rodada o servidor **embaralha a ordem de quem pergunta**. Em cada turno:

1. o servidor convida o **perguntador** (`turn_ask`) e ele envia **uma pergunta** (`send_question`);
2. o servidor retransmite a pergunta a **toda a sala** (`broadcast_question`);
3. **todos os outros jogadores** respondem, **um de cada vez**, na ordem circular que vem depois do perguntador
   (`turn_answer` → `send_answer` → `broadcast_answer`, repetido para cada um).

Numa rodada com **N** jogadores há **N perguntas** e **N × (N − 1) respostas**. Com 4 jogadores, por exemplo, uma
rodada tem 4 perguntas e 12 respostas. Para partidas mais rápidas, use `--rounds 1`.

### 1.4 Exemplo de partida (visão de um jogador normal, sala com 3 jogadores)

```
>>> Você é um JOGADOR NORMAL.
    Tema: COMIDA   |   Palavra secreta: FEIJÃO

----- RODADA 1  (ordem: Caio > Ana > Bia) -----

[turno 1/3] Vez de Caio fazer uma pergunta para todos...
  [PERGUNTA] Caio (para todos): Você come isso no almoço?

*** Sua vez de RESPONDER (1/2) à pergunta de Caio: "Você come isso no almoço?"
  -> Digite sua RESPOSTA: Quase todo dia!
  [RESPOSTA] Ana: Quase todo dia!
  Aguardando Bia responder (2/2)...
  [RESPOSTA] Bia: Depende do dia.

*** SUA VEZ! Faça UMA pergunta sobre a palavra para TODOS os outros. (turno 2/3)
    Quem vai responder, em ordem: Bia, Caio
  -> Digite sua PERGUNTA: _
```

### 1.5 Como executar (resumo)

```bash
# Terminal 1 – servidor
python3 server.py --port 5000

# Terminais 2, 3, 4... – um cliente por jogador (mesma máquina ou outras máquinas da rede)
python3 client.py --host 127.0.0.1 --port 5000 --name Ana
python3 client.py --host 127.0.0.1 --port 5000 --name Bia
python3 client.py --host 127.0.0.1 --port 5000 --name Caio
```

Em cada cliente, digite **`pronto`** no lobby. Quando todos (mínimo 3) estiverem prontos, a partida começa.
Siga os prompts `->` na tela. Comandos: `/ajuda`, `/ping` (mede latência), `/sair`.

---

## 2. Requisitos Mínimos de Funcionamento

| Item | Requisito |
|------|-----------|
| **Linguagem** | **Python 3.8 ou superior** (desenvolvido e testado em Python 3.12). |
| **Bibliotecas** | Apenas a **biblioteca padrão**: `socket`, `threading`, `queue`, `json`, `argparse`, `logging`, `re`, `time`, `unicodedata`, `collections`, `random`, `os`, `sys`. **Nenhum `pip install` necessário.** |
| **Sistema operacional** | Linux, macOS ou Windows (terminal com suporte a UTF-8 para acentos). |
| **Rede** | Os clientes precisam alcançar o IP/porta TCP do servidor. Em LAN, libere a porta (padrão `5000`) no firewall do servidor. |
| **Jogadores** | Mínimo 3 e máximo 5 clientes simultâneos. |

### 2.1 Executando o servidor

```bash
python3 server.py [--host 0.0.0.0] [--port 5000] [--rounds 3] [--max-rounds 10]
                  [--turn-timeout 60] [--vote-timeout 45]
```

| Parâmetro | Padrão | Significado |
|-----------|--------|-------------|
| `--host` | `0.0.0.0` | Interface de escuta (`0.0.0.0` = todas). |
| `--port` | `5000` | Porta TCP. |
| `--rounds` | `3` | Rodadas jogadas antes da 1ª pergunta "votar ou continuar?" (**número configurável**). Uma rodada = cada jogador pergunta uma vez a todos os outros. |
| `--max-rounds` | `10` | Ao atingir este limite, a votação é obrigatória. |
| `--turn-timeout` | `60` | Segundos para perguntar, responder ou palpitar. |
| `--vote-timeout` | `45` | Segundos para votar / decidir "votar ou continuar". |

### 2.2 Executando os clientes

```bash
python3 client.py --host <IP_DO_SERVIDOR> --port 5000 --name <SEU_NOME>
```

* **Mesma máquina:** use `--host 127.0.0.1` e abra um terminal por jogador.
* **Rede local:** descubra o IP do servidor (`ip addr` / `ipconfig`) e use-o em `--host`.
* O nome deve ter de 1 a 15 caracteres (letras, números, `_` ou `-`) e ser único na sala.

---

## 3. Motivação da Escolha do Protocolo de Transporte (TCP)

O jogo foi implementado sobre **TCP (`SOCK_STREAM`)**, e não UDP. A decisão é técnica e decorre da natureza do
problema: **um jogo por turnos é um sistema distribuído cuja correção depende de todos os participantes terem a
mesma visão do estado da partida, na mesma ordem.**

### 3.1 Entrega garantida e retransmissão
Cada evento do jogo (a pergunta, a resposta, o voto, o papel secreto) é **semanticamente indispensável**.
Se um único pacote `send_vote` ou `role_assign` fosse perdido, o jogo travaria ou ficaria injusto. O TCP usa
**números de sequência, ACKs e retransmissão por timeout/ACKs duplicados**, entregando cada byte **ou** informando a
falha. Com UDP, teríamos que reimplementar ACKs, timers e retransmissão na aplicação — reinventando o TCP.

### 3.2 Entrega ordenada
O estado do jogo é uma **sequência causal**: `turn_ask → broadcast_question → turn_answer → broadcast_answer`.
Se uma resposta chegasse antes da pergunta, o cliente exibiria algo incoerente. O TCP entrega os bytes **na
ordem em que foram enviados** (reordenação tratada pelos números de sequência), o que dispensa numeração de
mensagens e *buffers* de reordenação na aplicação.

### 3.3 Handshake de três vias e orientação a conexão
O estabelecimento de conexão (**SYN → SYN-ACK → ACK**) cria um **canal ponto a ponto com estado**, que combina
naturalmente com a noção de **"jogador conectado à sala"**:

* o servidor sabe **exatamente quais jogadores estão presentes** (conexão aberta = jogador na sala);
* o **encerramento** (`FIN` ou `RST`) sinaliza imediatamente que um jogador saiu — usado por `_remove_player` para
  atualizar o lobby ou abortar a partida;
* o *keep-alive* (`SO_KEEPALIVE`) detecta pares mortos (queda de rede).

Em UDP, "presença" teria de ser inferida por *heartbeats* próprios.

### 3.4 Controle de fluxo e de congestionamento
O TCP aplica **controle de fluxo por janela deslizante** (`rwnd`): um cliente lento (terminal travado,
máquina sobrecarregada) não é inundado com dados além do que seu *buffer* de recepção suporta. O
**controle de congestionamento** (slow start, congestion avoidance) evita que o jogo agrave redes
saturadas. Como o tráfego do jogo é pequeno (mensagens de dezenas a poucas centenas de bytes), o
*overhead* do TCP é irrelevante; já a robustez é essencial.

### 3.5 Fluxo de bytes (stream) e o *framing* da aplicação
O TCP é um **fluxo de bytes sem fronteiras de mensagem**: duas mensagens enviadas em dois `send()` podem
chegar em um único `recv()`, e uma mensagem pode chegar fragmentada. Por isso o protocolo de aplicação
define **delimitador explícito** (uma mensagem JSON por linha, terminada em `\n`) e a leitura usa
`readline()`, que reagrupa os bytes até encontrar o delimitador (seção 4.1).

### 3.6 Por que o UDP seria inadequado aqui

| Aspecto | TCP | UDP |
|---------|-----|-----|
| Perda de pacotes | Retransmitidos pelo transporte | Perdidos; aplicação deve tratar |
| Ordem de entrega | Garantida | Não garantida |
| Estado de conexão (presença do jogador) | Nativo (handshake/FIN/RST) | Inexistente |
| Controle de fluxo/congestionamento | Nativo | Inexistente |
| Latência | Ligeiramente maior (aceitável em jogo **por turnos**) | Menor (importa em jogos de ação em tempo real) |
| Adequação a **jogos por turnos e votações** | ✅ Alta | ❌ Exige reimplementar o TCP |

> **Conclusão:** UDP é preferível quando *latência > confiabilidade* (FPS, voz, vídeo). Em um jogo de
> dedução por turnos, o **requisito dominante é a consistência do estado compartilhado**; a latência
> adicional de uma retransmissão é imperceptível para quem digita uma pergunta. Logo, TCP.

### 3.7 Opções de socket usadas

| Opção | Motivo |
|-------|--------|
| `SO_REUSEADDR` (servidor) | Reiniciar o servidor imediatamente sem esperar o fim do `TIME_WAIT`. |
| `TCP_NODELAY` (ambos) | Desativa o algoritmo de Nagle: mensagens pequenas e interativas são enviadas sem atraso. |
| `SO_KEEPALIVE` (servidor) | Detecta conexões "mortas" (cliente que desligou a rede sem enviar `FIN`). |

---

## 4. Protocolo da Camada de Aplicação — `IMPOSTOR/1.0`

O protocolo é **textual, orientado a eventos, com estado**, sobre um fluxo TCP. A versão (`IMPOSTOR/1.0`) é
negociada no *handshake* de aplicação (`player_join`), permitindo evoluir o protocolo sem quebrar clientes.

### 4.1 Estrutura e formato das mensagens

**Codificação:** JSON em **UTF-8**. **Delimitação (framing):** **uma mensagem por linha**, terminada por `\n`
(*newline-delimited JSON*). Tamanho máximo de uma mensagem: **4096 caracteres** (acima disso, a conexão é
encerrada para proteger o servidor).

**Envelope** (idêntico nos dois sentidos):

```json
{"type": "<nome_do_evento>", "data": { ...campos específicos... }}
```

| Campo | Tipo | Obrigatório | Descrição |
|-------|------|-------------|-----------|
| `type` | string | sim | Nome do **evento** (seção 4.3). |
| `data` | objeto | não (padrão `{}`) | **Carga útil** do evento. |

**Exemplos reais (cada linha abaixo é uma mensagem no fio):**

```json
{"type":"player_join","data":{"name":"Ana","protocol":"IMPOSTOR/1.0"}}
{"type":"role_assign","data":{"role":"CIVILIAN","theme":"COMIDA","word":"FEIJÃO"}}
{"type":"role_assign","data":{"role":"IMPOSTOR","theme":"COMIDA","word":null}}
{"type":"turn_ask","data":{"round":1,"turn":1,"turns":3,"asker":"Caio","responders":["Ana","Bia"],"timeout":60}}
{"type":"send_question","data":{"text":"Você come isso no almoço?"}}
{"type":"send_vote","data":{"target":"Caio"}}
{"type":"end_game","data":{"result":"DRAW","message":"...","impostor":"Caio","theme":"COMIDA","word":"FEIJÃO","guess":"feijao"}}
```

**Por que JSON delimitado por linha (e não pipes `|`)?**
legível e depurável (`nc`/`telnet`), extensível (campos novos sem quebrar parsers), tolera texto livre dos
jogadores (inclusive o caractere `|`, que quebraria um formato por pipes), e é suportado pela biblioteca
padrão. O JSON escapa `\n` dentro de strings, portanto **uma quebra de linha real só ocorre como delimitador**.

**Princípios do protocolo**

1. **O servidor é a autoridade:** só ele decide de quem é a vez, valida entradas e calcula o resultado.
2. **O cliente apenas envia eventos permitidos no estado atual**; o servidor rejeita os demais com `error`.
3. **Segredos são enviados individualmente** (`role_assign` nunca é *broadcast*): o Impostor jamais recebe a palavra.
4. **Todo evento de jogo público é retransmitido (*broadcast*)** para manter os clientes sincronizados.
5. **Entradas não confiáveis:** nomes, tamanho de texto, alvos de voto e tipos de evento são validados no servidor.

### 4.2 Estados

#### 4.2.1 Estados do servidor (máquina de estados da sala)

| Estado | Descrição | Eventos aceitos dos clientes |
|--------|-----------|------------------------------|
| `WAIT_PLAYERS` | Lobby: aceita conexões (≤ 5) e aguarda `player_ready` de todos (≥ 3). | `player_join`, `player_ready`, `ping`, `player_quit` |
| `IN_GAME` | Fase de rodadas: perguntas e respostas por turnos. | `send_question` (só do `asker`), `send_answer` (só do jogador cuja vez de responder foi anunciada), `ping`, `player_quit` |
| `DECIDING` | "Votar no impostor ou jogar mais uma rodada?" | `send_continue_vote` (todos), `ping`, `player_quit` |
| `VOTING` | Cada jogador vota em quem acredita ser o Impostor. | `send_vote` (todos), `ping`, `player_quit` |
| `GUESSING` | Impostor descoberto tenta adivinhar a palavra (chance de empate). | `send_guess` (só do Impostor), `ping`, `player_quit` |
| `END_GAME` | Resultado final é anunciado; em seguida volta a `WAIT_PLAYERS`. | `ping`, `player_quit` |

```
                    todos "pronto" (≥3)
 ┌────────────────┐ ────────────────────► ┌──────────┐  após ≥ R rodadas  ┌──────────┐
 │  WAIT_PLAYERS  │                       │ IN_GAME  │ ─────────────────► │ DECIDING │
 └────────────────┘ ◄──┐                  └──────────┘ ◄───────────────── └──────────┘
        ▲              │                      ▲   "continuar" (ou empate)       │ maioria absoluta
        │ (reset +     │                      │                                  │ "votar" (ou limite
        │  1 segundo)  │                      └──────────────────────────────────┘  de rodadas)
        │              │                                                          ▼
 ┌──────────────┐      │  Impostor NÃO foi o mais votado          ┌─────────┐
 │   END_GAME   │ ◄────┼───────────────────────────────────────── │ VOTING  │
 └──────────────┘      │                                          └─────────┘
        ▲              │                                               │ Impostor foi o mais votado
        │              │          ┌──────────┐                         ▼
        └──────────────┴───────── │ GUESSING │ ◄───────────────────────┘
          (acertou → DRAW /       └──────────┘
           errou → IMPOSTOR_LOSES)

 Em qualquer estado de jogo: Impostor desconecta ou < 3 jogadores ──► END_GAME (result = ABORTED)
```

O servidor anuncia **cada transição** com o evento `state_change`.

#### 4.2.2 Estados do cliente

O cliente **espelha** o estado do servidor (`state_change`) e mantém uma **ação pendente** (`pending`) que
define o que o teclado do usuário significa naquele momento.

| Estado do cliente | Significado | Entrada do teclado interpretada como… |
|-------------------|-------------|----------------------------------------|
| `CONNECTING` | Socket aberto, `player_join` enviado, aguardando `welcome`. | — |
| `WAIT_PLAYERS` | No lobby. | `pronto` → `player_ready` |
| `IN_GAME` + `pending=ask` | É a vez de **perguntar a todos**. | texto → `send_question` |
| `IN_GAME` + `pending=answer` | É a vez de **responder**. | texto → `send_answer` |
| `IN_GAME` + `pending=None` | Aguardando os outros jogadores. | ignorada (aviso "aguarde") |
| `DECIDING` + `pending=continue` | Decisão coletiva. | `v` / `c` → `send_continue_vote` |
| `VOTING` + `pending=vote` | Votação. | número/nome → `send_vote` |
| `GUESSING` + `pending=guess` | (Só o Impostor descoberto) palpite. | texto → `send_guess` |
| `END_GAME` | Resultado exibido. | — (logo volta a `WAIT_PLAYERS`) |
| `CLOSED` | Conexão encerrada. | — |

A ação pendente é **cancelada** quando chega qualquer evento "ativo" do servidor (o servidor seguiu em frente —
por exemplo, estourou o tempo), e é **restaurada** quando o servidor responde `error/INVALID_INPUT`
(entrada inválida, o jogador pode tentar de novo).

### 4.3 Eventos

#### 4.3.1 Cliente → Servidor

| Evento | `data` | Estado válido | Descrição |
|--------|--------|---------------|-----------|
| `player_join` | `{name, protocol}` | *handshake* (1ª mensagem) | Pede entrada na sala. `protocol` deve ser `"IMPOSTOR/1.0"`. |
| `player_ready` | `{}` | `WAIT_PLAYERS` | Marca o jogador como pronto. |
| `send_question` | `{text}` | `IN_GAME` (turno do `asker`) | Envia a pergunta do turno, dirigida a **todos** os outros jogadores. |
| `send_answer` | `{text}` | `IN_GAME` (vez do `responder`) | Envia a resposta à pergunta atual. Cada jogador responde uma vez por pergunta. |
| `send_continue_vote` | `{choice}` | `DECIDING` | `choice` ∈ {`"vote"`, `"continue"`}. |
| `send_vote` | `{target}` | `VOTING` | Nome do jogador em quem vota (≠ si mesmo). |
| `send_guess` | `{word}` | `GUESSING` (só Impostor) | Palpite da palavra secreta. |
| `ping` | `{t}` | qualquer | Medição de latência (RTT). |
| `player_quit` | `{}` | qualquer | Saída voluntária (o cliente então fecha o socket). |

#### 4.3.2 Servidor → Cliente(s)

| Evento | Destino | `data` | Descrição |
|--------|---------|--------|-----------|
| `welcome` | 1 jogador | `{name, protocol, min_players, max_players}` | Aceite da entrada. |
| `join_rejected` | 1 jogador | `{reason}` | Entrada recusada (sala cheia, nome duplicado, versão incompatível, partida em andamento, nome inválido). A conexão é fechada. |
| `lobby_update` | todos | `{players:[{name,ready}], notice, min_players, max_players}` | Lista da sala; emitido a cada entrada/saída/`player_ready`. |
| `state_change` | todos | `{state}` | Nova transição da máquina de estados. |
| `start_game` | todos | `{players, theme, rounds_before_vote, max_rounds, turn_timeout}` | A partida começou; **tema** é público. |
| `role_assign` | **individual** | `{role, theme, word}` | `role` ∈ {`CIVILIAN`,`IMPOSTOR`}; `word` é `null` para o Impostor. |
| `round_start` | todos | `{round, order}` | Início de rodada e ordem sorteada. |
| `turn_ask` | todos | `{round, turn, turns, asker, responders, timeout}` | **Convite** para `asker` fazer uma pergunta a **todos**; `responders` é a ordem em que os demais responderão. |
| `broadcast_question` | todos | `{round, asker, text}` | Retransmissão da pergunta para toda a sala. |
| `turn_answer` | todos | `{round, asker, responder, question, position, total, timeout}` | **Convite** para `responder` responder (é o `position`-ésimo de `total`). Enviado uma vez para cada respondente. |
| `broadcast_answer` | todos | `{round, asker, responder, question, text}` | Retransmissão da resposta, na hora, para toda a sala. |
| `turn_timeout` | todos | `{player, action, reason}` | Jogador não agiu (tempo/queda); turno pulado. |
| `round_end` | todos | `{round}` | Fim da rodada. |
| `ask_continue` | todos | `{round, options, message, timeout}` | "Desejam votar no impostor ou jogar mais uma rodada?" |
| `continue_result` | todos | `{votes_vote, votes_continue, abstentions, decision}` | Resultado da decisão (`vote` ou `continue`). |
| `start_vote` | todos | `{candidates, timeout}` | Abre a votação. |
| `vote_progress` | todos | `{received, total}` | Progresso (sem revelar votos). |
| `vote_result` | todos | `{ballot, tally, accused, tie, impostor_caught}` | Apuração pública. |
| `request_guess` | todos | `{impostor, theme, timeout}` | O Impostor descoberto deve palpitar. |
| `guess_result` | todos | `{impostor, guess, correct}` | Resultado do palpite. |
| `end_game` | todos | `{result, message, impostor, theme, word, [guess]}` | Fim; revela Impostor e palavra. `result` ∈ {`IMPOSTOR_WINS`, `IMPOSTOR_LOSES`, `DRAW`, `ABORTED`}. |
| `info` | todos | `{message}` | Avisos (ex.: jogador desconectou). |
| `pong` | 1 jogador | `{t}` | Resposta ao `ping`. |
| `error` | 1 jogador | `{code, message}` | Erro de protocolo/validação (tabela abaixo). |

#### 4.3.3 Códigos de erro (`error.code`)

| Código | Causa | Efeito |
|--------|-------|--------|
| `BAD_MESSAGE` | Linha não é JSON válido / falta `type` / `data` não é objeto. | Mensagem ignorada; conexão mantida. |
| `INVALID_STATE` | Evento não permitido no estado atual (ex.: `send_vote` no lobby). | Ignorado. |
| `NOT_YOUR_TURN` | Jogador agiu fora da vez (ou duas vezes). | Ignorado — **é assim que o servidor impõe a ordem dos turnos**. |
| `UNEXPECTED_EVENT` | A vez é do jogador, mas o evento é de outro tipo. | Ignorado; o servidor continua esperando o evento certo. |
| `INVALID_INPUT` | Conteúdo inválido (texto vazio, voto em si mesmo, jogador inexistente, `choice` inválido). | O jogador **pode tentar de novo** dentro do prazo. |

### 4.4 Fluxo de mensagens entre as partes comunicantes

Legenda: `C1..C3` = clientes (C2 será o Impostor neste exemplo), `S` = servidor.
`►` = enviado do remetente ao destino; `►►` = *broadcast* para todos.

#### 4.4.1 Conexão e lobby

```
 C1            C2            C3                         S
 │ ── TCP SYN/SYN-ACK/ACK (handshake de transporte) ───►│
 │ ── player_join {name:"Ana", protocol:"IMPOSTOR/1.0"} ►│  valida versão, nome, lotação, estado
 │ ◄──────────── welcome ────────────────────────────── │
 │ ◄──────────── lobby_update (Ana) ──────────────────── │  ►► todos
 │      (C2 e C3 repetem o mesmo procedimento)           │
 │ ── player_ready ─────────────────────────────────────►│
 │ ◄──────────── lobby_update (Ana PRONTO) ───────────── │  ►►
 │              ...  C2 e C3 enviam player_ready ...      │
 │                                                        │  ≥3 jogadores e todos prontos ⇒ inicia
```

#### 4.4.2 Início da partida e rodada

```
 S: sorteia tema, palavra e Impostor (C2)
 │ ◄── state_change {IN_GAME} ───────────────────────────  ►►
 │ ◄── start_game {players, theme:"COMIDA", ...} ───────── ►►
 │ ◄── role_assign {CIVILIAN, COMIDA, "FEIJÃO"} ───────── C1 (individual)
 │                  role_assign {IMPOSTOR, COMIDA, null} ► C2 (individual)
 │ ◄── role_assign {CIVILIAN, COMIDA, "FEIJÃO"} ───────── C3 (individual)
 │ ◄── round_start {round:1, order:[C3,C1,C2]} ─────────── ►►

 ── Turno 1: C3 pergunta a TODOS (respondem C1 e depois C2) ──
 │ ◄── turn_ask {asker:C3, responders:[C1,C2]} ─────────── ►►
 │                                    C3 ── send_question {text} ──► S
 │      (se C1 ou C2 enviassem algo agora: S ► error NOT_YOUR_TURN)
 │ ◄── broadcast_question {C3, text} ───────────────────── ►►
 │ ◄── turn_answer {responder:C1, position:1, total:2} ─── ►►
 │ C1 ── send_answer {text} ───────────────────────────────► S
 │ ◄── broadcast_answer {C1, text} ─────────────────────── ►►
 │ ◄── turn_answer {responder:C2, position:2, total:2} ─── ►►
 │ C2 ── send_answer {text} ───────────────────────────────► S
 │ ◄── broadcast_answer {C2, text} ─────────────────────── ►►    (C2 já viu a resposta de C1)
 ── Turnos 2..N análogos (em cada rodada, cada jogador pergunta uma vez) ──
 │ ◄── round_end {round:1} ─────────────────────────────── ►►
```

#### 4.4.3 Decisão, votação e resolução

```
 (após --rounds rodadas)
 │ ◄── state_change {DECIDING} ─────────────────────────── ►►
 │ ◄── ask_continue {"Desejam votar no impostor ou jogar mais uma rodada?"} ►►
 │ C1,C2,C3 ── send_continue_vote {choice:"vote"|"continue"} ──► S
 │ ◄── continue_result {votes_vote:2, votes_continue:1, decision:"vote"} ►►
 │        └─ decision="continue" ⇒ state_change {IN_GAME} e nova rodada (volta ao 4.4.2)

 │ ◄── state_change {VOTING} ───────────────────────────── ►►
 │ ◄── start_vote {candidates:[C1,C2,C3]} ──────────────── ►►
 │ C1 ── send_vote {target:C2} ───────────────────────────► S
 │ ◄── vote_progress {1/3} ─────────────────────────────── ►►   (não revela quem votou em quem)
 │ C2 ── send_vote {target:C1} ─►  C3 ── send_vote {target:C2} ─► S
 │ ◄── vote_result {ballot, tally:{C2:2,C1:1}, accused:C2, impostor_caught:true} ►►

 ┌── CASO A: acusado ≠ Impostor (ou empate) ───────────────────────────────────────┐
 │ ◄── state_change {END_GAME}                                                      │
 │ ◄── end_game {result:"IMPOSTOR_WINS", impostor, word}                      ►►    │
 └──────────────────────────────────────────────────────────────────────────────────┘
 ┌── CASO B: acusado = Impostor (como no exemplo) ──────────────────────────────────┐
 │ ◄── state_change {GUESSING}                                                ►►    │
 │ ◄── request_guess {impostor:C2, theme}                                     ►►    │
 │ C2 ── send_guess {word:"feijao"} ───────────────────────────────────────────► S  │
 │ ◄── guess_result {guess:"feijao", correct:true}                            ►►    │
 │ ◄── end_game {result:"DRAW" (acertou) | "IMPOSTOR_LOSES" (errou)}          ►►    │
 └──────────────────────────────────────────────────────────────────────────────────┘
 │ ◄── state_change {WAIT_PLAYERS} + lobby_update {"Nova partida..."} ──── ►►
```

#### 4.4.4 Situações excepcionais

```
 Entrada recusada:   C ── player_join ──► S ── join_rejected {reason} ──► C   (S fecha o socket)
 Tempo esgotado:     S ►► turn_timeout {player, action, reason:"timeout"}  → turno é pulado
 Queda de jogador:   (FIN/RST) S ►► info {"X desconectou-se."}
                       • no lobby          → lobby_update
                       • no jogo, Impostor caiu ou restam < 3 jogadores
                                           → end_game {result:"ABORTED"}
                       • no jogo, caso contrário → o turno do jogador é pulado
 Mensagem inválida:  C ── "lixo" ──► S ── error {BAD_MESSAGE} ──► C           (conexão mantida)
 Latência:           C ── ping {t} ──► S ── pong {t} ──► C   (RTT = agora − t)
```

---

## 5. Arquitetura e Concorrência

```
                        ┌──────────────────────────────── SERVIDOR (server.py) ───────────────────────────────┐
 Cliente 1 ──TCP──►  [thread handle_client 1] ─┐                                                              │
 Cliente 2 ──TCP──►  [thread handle_client 2] ─┼─► eventos de jogo ─► queue.Queue ─► [thread da PARTIDA]      │
 Cliente 3 ──TCP──►  [thread handle_client 3] ─┘   (lobby tratado         (árbitro: máquina de estados,        │
 Cliente N ──TCP──►  [thread handle_client N]       na própria thread)     turnos, apuração, broadcast)  ──► sockets
                        │   thread principal: accept() em loop                                                 │
                        └────────────────────────────────────────────────────────────────────────────────────┘
```

* **Thread principal:** `accept()` em laço; para cada conexão, cria uma **thread por cliente**.
* **Thread por cliente (`handle_client`):** faz o *handshake*, lê mensagens (`readline`), responde `ping`, trata o lobby
  (`player_ready`) e **entrega os eventos de jogo à fila** da partida. É também quem detecta quedas (EOF).
* **Thread da partida (`_run_game`):** único "árbitro". Consome a fila e **só aceita o evento esperado do jogador esperado**
  (`_collect`). Isso elimina condições de corrida na lógica do jogo e torna a ordem dos turnos **imposta pelo servidor**.
* **Sincronização:** `threading.RLock` protege `players`/`state`; cada `Player` possui um `Lock` de envio para que duas
  threads nunca intercalem bytes de duas mensagens no mesmo socket; `queue.Queue` é *thread-safe*.
* **Timeouts:** cada espera usa `queue.get(timeout=…)` com prazo (`time.monotonic`), de modo que um jogador ausente nunca
  trava os demais.
* **Cliente:** 2 threads — **recepção** (imprime atualizações assim que chegam, de forma assíncrona) e **principal**
  (teclado), cooperando por `state`/`pending` protegidos por `Lock`.

---

## 6. Testes realizados

Executados em Python 3.12 (Linux), com servidor e clientes via `localhost`. Parte dos testes usou o `client.py` real
controlado por um script; outra parte usou clientes descartáveis que falam o protocolo diretamente por *sockets*
(scripts de teste, não incluídos na entrega).

| Teste | Resultado |
|-------|-----------|
| 4 instâncias reais do `client.py`, partida completa | ✅ 4 perguntas e 12 respostas na rodada (cada pergunta respondida pelos 3 outros, um por vez) |
| Várias partidas seguidas, votos aleatórios (volta ao lobby e revanche) | ✅ `IMPOSTOR_WINS` e `IMPOSTOR_LOSES` observados |
| Impostor descoberto + palpite **errado** | ✅ `IMPOSTOR_LOSES` |
| Impostor descoberto + palpite **certo** (5 jogadores) | ✅ `DRAW` |
| Rodada extra ("continuar" antes de "votar") | ✅ |
| Jogador que **não responde** (prazo de 2 s) | ✅ `turn_timeout`, o turno é pulado e a partida termina normalmente |
| **Queda de jogador comum** na hora de responder | ✅ `turn_timeout (disconnected)`, partida continua com os demais |
| **Queda do Impostor** | ✅ `end_game` com `ABORTED` |
| 6º jogador recusado | ✅ `join_rejected` ("Sala cheia (5/5)") |
| Nome duplicado, nome inválido, versão de protocolo incompatível, 1ª mensagem inválida | ✅ `join_rejected` |
| JSON inválido após entrar / evento fora do estado | ✅ `error BAD_MESSAGE` / `INVALID_STATE`, conexão mantida |

**Roteiro manual** para reproduzir (3 a 5 terminais): suba o servidor com `python3 server.py --rounds 1`, entre com
um `client.py` por terminal, digite `pronto` em todos e acompanhe o fluxo. Para testar o *timeout*, use
`--turn-timeout 5` e deixe um jogador sem responder; para testar a queda, feche o terminal de um jogador no meio da vez.

Exemplo ilustrativo de sessão manual com `netcat` (o protocolo é textual, legível e independente do cliente):

```bash
$ nc 127.0.0.1 5000
{"type":"player_join","data":{"name":"Teste","protocol":"IMPOSTOR/1.0"}}
{"type": "welcome", "data": {"name": "Teste", "protocol": "IMPOSTOR/1.0", "min_players": 3, "max_players": 5}}
{"type": "lobby_update", "data": {"players": [{"name": "Teste", "ready": false}], "notice": "Teste entrou na sala.", ...}}
```

---

## 7. Decisões de projeto e diferenciais

* **Protocolo próprio, versionado e documentado** (`IMPOSTOR/1.0`), com máquina de estados explícita no servidor **e** no
  cliente, e códigos de erro padronizados.
* **Servidor autoritativo:** o cliente não decide nada; mesmo um cliente modificado/malicioso não consegue agir fora de
  vez (`NOT_YOUR_TURN`), votar em si mesmo ou ver a palavra do Impostor (o segredo nem é enviado).
* **Pergunta para todos, respostas em sequência e públicas:** o servidor sorteia a ordem de quem pergunta a cada rodada e
  conduz cada respondente, um por vez, retransmitindo cada resposta na hora.
* **Votação secreta com progresso público** (`vote_progress`) e **revelação simultânea** (`vote_result`).
* **Robustez:** timeouts em todas as esperas, tratamento de queda de jogador, `ABORTED` seguro, limite de tamanho de mensagem,
  validação de nome/entradas, descarte de mensagens atrasadas (`_drain`).
* **Revanche sem reconectar:** a sala volta ao lobby ao final da partida.
* **Observabilidade:** *log* com horário no servidor; `/ping` no cliente mede o RTT real da conexão TCP.
* **Banco de temas** inclui um tema de **Redes** (`SOCKET`, `FIREWALL`, `PROTOCOLO`, …) alinhado à disciplina.
* **Somente biblioteca padrão**, sem dependências externas.

---

## 8. Limitações conhecidas e trabalhos futuros

* **Sem criptografia:** o tráfego é texto puro. Em produção, usar **TLS** (`ssl.SSLContext.wrap_socket`) sobre o mesmo
  protocolo.
* **Sem autenticação:** o nome do jogador é apenas um apelido.
* **Envio síncrono:** um cliente com *buffer* de recepção cheio por tempo muito longo poderia atrasar um *broadcast*.
  Melhoria: fila de saída + thread escritora por jogador.
* **Uma única sala por servidor.** Melhoria: múltiplas salas/lobbies simultâneos.
* **Interface de terminal:** uma GUI (Tkinter) ou web (WebSocket) poderia reutilizar o mesmo protocolo.
* **Reconexão** de jogadores no meio da partida não é suportada (a queda é tratada, mas não há "retorno").

---

## 9. Estrutura do repositório

```
impostor/
├── server.py      # Servidor: sockets TCP, 1 thread por cliente, árbitro da partida, máquina de estados
├── client.py      # Cliente de terminal: thread de recepção + entrada do usuário
└── readme.md      # Este documento
```
