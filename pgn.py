#!/usr/bin/env python3
"""Parseur PGN et moteur de coups légaux (SAN -> UCI) en Python 3.11 stdlib.

Aucune dépendance externe : extrait en-têtes, coups SAN, évaluations [%eval ...]
et pendules [%clk ...], et convertit la ligne principale en coups UCI vérifiés
(roques, prise en passant, promotions, clouages).
"""

from __future__ import annotations

import re
import sys

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

FILES = "abcdefgh"
RANKS = "12345678"

_EVAL_RE = re.compile(r"\[%eval\s+([#+-]?\d+(?:\.\d+)?)\]")
_CLK_RE = re.compile(r"\[%clk\s+(\d+):(\d+):(\d+(?:\.\d+)?)\]")
_HEADER_RE = re.compile(r'^\[([A-Za-z0-9_]+)\s+"((?:\\.|[^"\\])*)"\]\s*$')
_SAN_RE = re.compile(
    r"^(?:(O-O-O|O-O|0-0-0|0-0)|([KQRBN])?([a-h])?([1-8])?(x)?([a-h][1-8])(?:=([QRBN]))?)[+#?!]*$"
)


def sq_to_idx(sq: str) -> int:
    return (ord(sq[0]) - ord("a")) + (int(sq[1]) - 1) * 8


def idx_to_sq(idx: int) -> str:
    return FILES[idx % 8] + RANKS[idx // 8]


class Board:
    """Échiquier minimal complet pour convertir SAN -> UCI avec vérification des clouages."""

    def __init__(self, fen: str = START_FEN) -> None:
        self.squares: list[str] = [""] * 64
        self.turn: str = "w"
        self.castling: str = "KQkq"
        self.ep: int | None = None
        self.load_fen(fen)

    def load_fen(self, fen: str) -> None:
        parts = fen.strip().split()
        rows = parts[0].split("/")
        if len(rows) != 8:
            raise ValueError(f"FEN invalide : {fen}")
        self.squares = [""] * 64
        for r_idx, row in enumerate(rows):
            rank = 7 - r_idx
            file_i = 0
            for ch in row:
                if ch.isdigit():
                    file_i += int(ch)
                else:
                    self.squares[rank * 8 + file_i] = ch
                    file_i += 1
        self.turn = parts[1] if len(parts) > 1 else "w"
        self.castling = parts[2] if len(parts) > 2 and parts[2] != "-" else ""
        self.ep = sq_to_idx(parts[3]) if len(parts) > 3 and parts[3] != "-" else None

    def copy(self) -> Board:
        b = Board.__new__(Board)
        b.squares = self.squares.copy()
        b.turn = self.turn
        b.castling = self.castling
        b.ep = self.ep
        return b

    def _king_sq(self, color: str) -> int | None:
        target = "K" if color == "w" else "k"
        for i, p in enumerate(self.squares):
            if p == target:
                return i
        return None

    def is_attacked(self, sq: int, by_color: str) -> bool:
        f, r = sq % 8, sq // 8
        # Pions
        pawn = "P" if by_color == "w" else "p"
        pr = r - 1 if by_color == "w" else r + 1
        if 0 <= pr < 8:
            for df in (-1, 1):
                pf = f + df
                if 0 <= pf < 8 and self.squares[pr * 8 + pf] == pawn:
                    return True
        # Cavaliers
        knight = "N" if by_color == "w" else "n"
        for df, dr in ((-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)):
            nf, nr = f + df, r + dr
            if 0 <= nf < 8 and 0 <= nr < 8 and self.squares[nr * 8 + nf] == knight:
                return True
        # Roi
        king = "K" if by_color == "w" else "k"
        for df in (-1, 0, 1):
            for dr in (-1, 0, 1):
                if df == 0 and dr == 0:
                    continue
                kf, kr = f + df, r + dr
                if 0 <= kf < 8 and 0 <= kr < 8 and self.squares[kr * 8 + kf] == king:
                    return True
        # Glissantes orthogonales (Tour / Dame)
        rooks = ("R", "Q") if by_color == "w" else ("r", "q")
        for df, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            cf, cr = f + df, r + dr
            while 0 <= cf < 8 and 0 <= cr < 8:
                p = self.squares[cr * 8 + cf]
                if p:
                    if p in rooks:
                        return True
                    break
                cf += df
                cr += dr
        # Glissantes diagonales (Fou / Dame)
        bishops = ("B", "Q") if by_color == "w" else ("b", "q")
        for df, dr in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            cf, cr = f + df, r + dr
            while 0 <= cf < 8 and 0 <= cr < 8:
                p = self.squares[cr * 8 + cf]
                if p:
                    if p in bishops:
                        return True
                    break
                cf += df
                cr += dr
        return False

    def _can_reach(self, src: int, dst: int, piece_type: str, is_capture: bool) -> bool:
        sf, sr = src % 8, src // 8
        df, dr = dst % 8, dst // 8
        dx, dy = df - sf, dr - sr

        if piece_type == "P":
            step = 1 if self.turn == "w" else -1
            start_rank = 1 if self.turn == "w" else 6
            if is_capture:
                if abs(dx) == 1 and dy == step:
                    if self.squares[dst]:
                        return True
                    if self.ep == dst:
                        return True
                return False
            if dx != 0 or self.squares[dst]:
                return False
            if dy == step:
                return True
            if dy == 2 * step and sr == start_rank and not self.squares[(sr + step) * 8 + sf]:
                return True
            return False

        if piece_type == "N":
            return (abs(dx), abs(dy)) in ((1, 2), (2, 1))
        if piece_type == "K":
            return max(abs(dx), abs(dy)) == 1
        if piece_type == "B":
            if abs(dx) != abs(dy) or dx == 0:
                return False
            return self._path_clear(sf, sr, df, dr)
        if piece_type == "R":
            if dx != 0 and dy != 0:
                return False
            return self._path_clear(sf, sr, df, dr)
        if piece_type == "Q":
            if dx != 0 and dy != 0 and abs(dx) != abs(dy):
                return False
            return self._path_clear(sf, sr, df, dr)
        return False

    def _path_clear(self, sf: int, sr: int, df: int, dr: int) -> bool:
        step_f = 0 if df == sf else (1 if df > sf else -1)
        step_r = 0 if dr == sr else (1 if dr > sr else -1)
        cf, cr = sf + step_f, sr + step_r
        while (cf, cr) != (df, dr):
            if self.squares[cr * 8 + cf]:
                return False
            cf += step_f
            cr += step_r
        return True

    def _apply_raw(self, src: int, dst: int, promo: str | None = None) -> None:
        piece = self.squares[src]
        ptype = piece.upper()
        # Prise en passant
        if ptype == "P" and dst == self.ep and not self.squares[dst]:
            cap_sq = (dst % 8) + (src // 8) * 8
            self.squares[cap_sq] = ""

        self.squares[dst] = (promo.upper() if self.turn == "w" else promo.lower()) if promo else piece
        self.squares[src] = ""

        # Roque
        if ptype == "K" and abs((dst % 8) - (src % 8)) == 2:
            rank = src // 8
            if dst % 8 == 6:  # petit roque
                self.squares[rank * 8 + 5] = self.squares[rank * 8 + 7]
                self.squares[rank * 8 + 7] = ""
            elif dst % 8 == 2:  # grand roque
                self.squares[rank * 8 + 3] = self.squares[rank * 8 + 0]
                self.squares[rank * 8 + 0] = ""

        # Droits de roque
        if ptype == "K":
            rem = "KQ" if self.turn == "w" else "kq"
            self.castling = "".join(c for c in self.castling if c not in rem)
        for corner, flag in ((0, "Q"), (7, "K"), (56, "q"), (63, "k")):
            if src == corner or dst == corner:
                self.castling = self.castling.replace(flag, "")

        # Case en passant
        if ptype == "P" and abs((dst // 8) - (src // 8)) == 2:
            self.ep = (src + dst) // 2
        else:
            self.ep = None

        self.turn = "b" if self.turn == "w" else "w"

    def push_san(self, san: str) -> str:
        """Joue un coup SAN et retourne sa notation UCI (ex. 'e2e4', 'e7e8q')."""
        m = _SAN_RE.match(san.strip())
        if not m:
            raise ValueError(f"Coup SAN non reconnu : {san!r}")
        castle, ptype, src_f, src_r, cap, dst_s, promo = m.groups()

        if castle:
            rank = 0 if self.turn == "w" else 7
            src = rank * 8 + 4
            dst = rank * 8 + (2 if len(castle) == 5 else 6)
            self._apply_raw(src, dst)
            return idx_to_sq(src) + idx_to_sq(dst)

        ptype = ptype or "P"
        dst = sq_to_idx(dst_s)
        piece_char = ptype if self.turn == "w" else ptype.lower()
        is_cap = bool(cap) or bool(self.squares[dst]) or (ptype == "P" and dst == self.ep)
        opp = "b" if self.turn == "w" else "w"

        candidates: list[int] = []
        for sq, p in enumerate(self.squares):
            if p != piece_char:
                continue
            if src_f and FILES[sq % 8] != src_f:
                continue
            if src_r and RANKS[sq // 8] != src_r:
                continue
            if not self._can_reach(sq, dst, ptype, is_cap):
                continue
            # Vérifier que le coup ne laisse pas son propre roi en échec
            test = self.copy()
            test._apply_raw(sq, dst, promo)
            ksq = test._king_sq(self.turn)
            if ksq is not None and test.is_attacked(ksq, opp):
                continue
            candidates.append(sq)

        if len(candidates) != 1:
            raise ValueError(f"Coup SAN ambigu ou illégal ({len(candidates)} candidats) : {san!r}")

        src = candidates[0]
        self._apply_raw(src, dst, promo)
        return idx_to_sq(src) + idx_to_sq(dst) + (promo.lower() if promo else "")


def _parse_comment(comment: str) -> tuple[int | None, int | None, int | None]:
    """Extrait (eval_cp, mate, clk_sec) d'un commentaire PGN."""
    ev_cp: int | None = None
    mate: int | None = None
    clk_s: int | None = None

    m_ev = _EVAL_RE.search(comment)
    if m_ev:
        raw = m_ev.group(1)
        if raw.startswith("#"):
            try:
                mate = int(raw[1:])
            except ValueError:
                pass
        else:
            try:
                ev_cp = int(round(float(raw) * 100))
            except ValueError:
                pass

    m_clk = _CLK_RE.search(comment)
    if m_clk:
        h, mi, sec = int(m_clk.group(1)), int(m_clk.group(2)), float(m_clk.group(3))
        clk_s = int(round(h * 3600 + mi * 60 + sec))

    return ev_cp, mate, clk_s


def _tokenize_movetext(movetext: str) -> list[tuple[str, str]]:
    """Découpe le texte de coups en jetons (SAN, commentaire_associé) sur la ligne principale."""
    tokens: list[tuple[str, str]] = []
    i = 0
    n = len(movetext)
    depth = 0

    while i < n:
        ch = movetext[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if ch == "{":
            end = movetext.find("}", i + 1)
            if end == -1:
                break
            comment = movetext[i + 1 : end]
            if depth == 0 and tokens:
                prev_san, prev_c = tokens[-1]
                tokens[-1] = (prev_san, (prev_c + " " + comment).strip())
            i = end + 1
            continue
        if ch == ";":
            end = movetext.find("\n", i + 1)
            i = n if end == -1 else end + 1
            continue

        # Lire un mot
        j = i
        while j < n and not movetext[j].isspace() and movetext[j] not in "(){}":
            j += 1
        word = movetext[i:j]
        i = j

        if depth > 0:
            continue
        if word in ("1-0", "0-1", "1/2-1/2", "*"):
            continue
        if word.startswith("$") or word.endswith(".") or re.match(r"^\d+\.+$", word):
            continue
        # Parfois "1.e4" ou "1...c5" collé sans espace
        word = re.sub(r"^\d+\.+", "", word)
        if not word:
            continue
        tokens.append((word, ""))

    return tokens


def parse_pgn_games(pgn_text: str) -> list[dict]:
    """Parse un fichier PGN multi-parties en une liste de dictionnaires structurés."""
    games: list[dict] = []
    headers: dict[str, str] = {}
    move_lines: list[str] = []
    in_moves = False

    def flush() -> None:
        nonlocal headers, move_lines, in_moves
        if not headers and not move_lines:
            return
        movetext = " ".join(move_lines).strip()
        fen = headers.get("FEN", START_FEN)
        board: Board | None = None
        try:
            board = Board(fen)
        except ValueError:
            board = None

        plies: list[dict] = []
        uci_ok = board is not None
        for san, comment in _tokenize_movetext(movetext):
            uci = ""
            if uci_ok and board is not None:
                try:
                    uci = board.push_san(san)
                except ValueError:
                    uci_ok = False
            ev_cp, mate, clk_s = _parse_comment(comment)
            ply: dict = {"san": san}
            if uci:
                ply["uci"] = uci
            if ev_cp is not None:
                ply["eval"] = ev_cp
            if mate is not None:
                ply["mate"] = mate
            if clk_s is not None:
                ply["clk"] = clk_s
            plies.append(ply)

        games.append({
            "headers": headers,
            "plies": plies,
            "uci_complet": uci_ok and bool(plies),
        })
        headers = {}
        move_lines = []
        in_moves = False

    for raw_line in pgn_text.splitlines():
        line = raw_line.strip()
        if not line:
            if in_moves:
                flush()
            continue
        m = _HEADER_RE.match(line)
        if m:
            if in_moves:
                flush()
            headers[m.group(1)] = m.group(2).replace('\\"', '"').replace("\\\\", "\\")
        else:
            in_moves = True
            move_lines.append(line)
    flush()
    return games


def _run_tests() -> None:
    sample = """
[Event "Tata Steel Masters 2026"]
[Site "Wijk aan Zee"]
[Date "2026.01.18"]
[Round "1.1"]
[White "Firouzja, Alireza"]
[Black "Vachier-Lagrave, Maxime"]
[Result "1-0"]
[WhiteElo "2763"]
[BlackElo "2735"]
[WhiteFideId "12573981"]
[BlackFideId "623539"]
[ECO "C65"]

1. e4 { [%eval 0.20] [%clk 1:40:00] } 1... e5 { [%eval 0.22] [%clk 1:39:55] }
2. Nf3 { [%eval 0.18] [%clk 1:39:40] } 2... Nc6 { [%eval 0.25] [%clk 1:39:30] }
3. Bb5 { [%eval 0.20] [%clk 1:39:20] } 3... Nf6 { [%eval 0.24] [%clk 1:39:10] }
4. O-O { [%eval 0.15] [%clk 1:39:00] } 4... Nxe4 (4... Bc5 5. c3) 5. d4 { [%eval 0.10] [%clk 1:38:00] }
5... Nd6 { [%eval 0.12] [%clk 1:37:50] } 6. Bxc6 { [%eval 0.05] [%clk 1:37:30] }
6... dxc6 { [%eval 0.08] [%clk 1:37:20] } 7. dxe5 { [%eval 0.05] [%clk 1:37:00] }
7... Nf5 { [%eval 0.06] [%clk 1:36:50] } 8. Qxd8+ { [%eval 0.00] [%clk 1:36:40] }
8... Kxd8 { [%eval 0.00] [%clk 1:36:35] } 1-0

[Event "Test En Passant & Promotion"]
[White "A"]
[Black "B"]
[Result "1-0"]

1. e4 d5 2. e5 f5 3. exf6 e5 4. fxg7 Bxg7 5. d4 exd4 6. c3 dxc3 7. Nxc3 Bxc3+ 8. bxc3 Qe7+ 9. Be2 Bg4 10. Nf3 Bxf3 11. gxf3 Qxe2+ 12. Qxe2+ Ne7 13. Qxe7+ Kxe7 14. Bg5+ Kd6 15. O-O-O { [%eval #3] } 1-0
"""
    games = parse_pgn_games(sample)
    assert len(games) == 2, f"Attendu 2 parties, obtenu {len(games)}"
    g1 = games[0]
    assert g1["headers"]["WhiteFideId"] == "12573981"
    assert g1["uci_complet"] is True
    assert len(g1["plies"]) == 16
    assert g1["plies"][0]["uci"] == "e2e4"
    assert g1["plies"][0]["eval"] == 20
    assert g1["plies"][0]["clk"] == 6000
    assert g1["plies"][6]["uci"] == "e1g1"  # O-O
    assert g1["plies"][15]["uci"] == "e8d8"

    g2 = games[1]
    assert g2["uci_complet"] is True
    assert g2["plies"][4]["uci"] == "e5f6"  # en passant
    assert g2["plies"][-1]["uci"] == "e1c1"  # O-O-O
    assert g2["plies"][-1]["mate"] == 3

    # Test promotion + désambiguïsation par clouage
    b = Board("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    assert b.push_san("a8=Q+") == "a7a8q"

    b2 = Board("4k3/8/8/8/8/1b6/2N1N3/3K4 w - - 0 1")
    # Cavalier c2 est cloué par le fou b3 sur le roi d1 -> seul Ne2 peut jouer Nd4 !
    assert b2.push_san("Nd4") == "e2d4"

    print("[OK] pgn.py : tous les tests passent.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_tests()
    else:
        print("Usage: python3 pgn.py --test")
