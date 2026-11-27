#!/usr/bin/env python3
"""Analyse déterministe des parties d'échecs (Python 3.11 stdlib).

Formules publiques Lichess :
- Chances de gain (Win%) : 50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1)
- Précision d'un coup : 103.1668 * exp(-0.04354 * dW) - 3.1669
- Repli déterministe par Stockfish (UCI) si le broadcast Lichess ne fournit pas [%eval].
Aucune valeur n'est jamais inventée : sans évaluation, l'analyse reste absente.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
import time

_WIN_COEFF = -0.00368208
_CP_CAP = 1000


def win_percent(ev_cp: int | None, mate: int | None = None) -> float | None:
    """Probabilité de gain des Blancs en [0, 100] (formule publique Lichess)."""
    if mate is not None:
        if mate > 0:
            return 100.0
        if mate < 0:
            return 0.0
        return 50.0
    if ev_cp is None:
        return None
    cp = max(-_CP_CAP, min(_CP_CAP, ev_cp))
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(_WIN_COEFF * cp)) - 1.0)


def move_accuracy(wp_before: float, wp_after: float) -> float:
    """Précision d'un coup en [0, 100] selon la perte de chances de gain du joueur au trait."""
    diff = max(0.0, wp_before - wp_after)
    val = 103.1668 * math.exp(-0.04354 * diff) - 3.1669
    return max(0.0, min(100.0, val))


def format_eval(ev_cp: int | None, mate: int | None = None) -> str:
    if mate is not None:
        return f"#{mate}" if mate > 0 else f"#-{abs(mate)}"
    if ev_cp is None:
        return ""
    sign = "+" if ev_cp > 0 else ""
    return f"{sign}{ev_cp / 100:.2f}".replace(".", ",")


def _parse_increment(time_control: str) -> int:
    """Extrait l'incrément (en secondes) d'un TimeControl PGN simple (ex. '5400+30' -> 30)."""
    if not time_control:
        return 0
    m = re.search(r"\+(\d+)\s*$", time_control.strip())
    return int(m.group(1)) if m else 0


def analyser_partie(plies: list[dict], time_control: str = "") -> dict | None:
    """Calcule courbe d'évaluation, précision, erreurs, tournants et gestion du temps.

    Retourne None si aucune donnée exploitable (ni eval ni clk).
    """
    if not plies:
        return None

    eval_count = sum(1 for p in plies if "eval" in p or "mate" in p)
    has_eval = eval_count >= max(4, len(plies) // 2)
    clk_count = sum(1 for p in plies if "clk" in p)
    has_clk = clk_count >= max(4, len(plies) // 2)

    if not has_eval and not has_clk:
        return None

    res: dict = {}

    if has_eval:
        # Position initiale : ~+0.20 (51.8% Blancs)
        prev_wp = win_percent(20, None)
        assert prev_wp is not None
        prev_cp = 20

        courbe: list[float | None] = []
        acc_w: list[float] = []
        acc_b: list[float] = []
        cpl_w: list[int] = []
        cpl_b: list[int] = []
        counts = {
            "blancs": {"imprecisions": 0, "erreurs": 0, "gaffes": 0},
            "noirs": {"imprecisions": 0, "erreurs": 0, "gaffes": 0},
        }
        tournants: list[dict] = []

        for idx, ply in enumerate(plies):
            is_white = (idx % 2 == 0)
            ev_cp = ply.get("eval")
            mate = ply.get("mate")
            wp = win_percent(ev_cp, mate)
            courbe.append(round(wp, 1) if wp is not None else None)

            if wp is not None:
                # Point de vue du joueur qui vient de jouer
                p_before = prev_wp if is_white else (100.0 - prev_wp)
                p_after = wp if is_white else (100.0 - wp)
                loss_w = max(0.0, p_before - p_after)
                ma = move_accuracy(p_before, p_after)

                cur_cp = (
                    (_CP_CAP if mate > 0 else -_CP_CAP)
                    if mate is not None
                    else max(-_CP_CAP, min(_CP_CAP, ev_cp if ev_cp is not None else prev_cp))
                )
                cpl = max(0, (prev_cp - cur_cp) if is_white else (cur_cp - prev_cp))

                camp = "blancs" if is_white else "noirs"
                if is_white:
                    acc_w.append(ma)
                    cpl_w.append(cpl)
                else:
                    acc_b.append(ma)
                    cpl_b.append(cpl)

                classe = ""
                if loss_w >= 30.0:
                    counts[camp]["gaffes"] += 1
                    classe = "gaffe"
                elif loss_w >= 20.0:
                    counts[camp]["erreurs"] += 1
                    classe = "erreur"
                elif loss_w >= 10.0:
                    counts[camp]["imprecisions"] += 1
                    classe = "imprecision"

                if classe:
                    ply["classe"] = classe

                if loss_w >= 12.0:
                    tournants.append({
                        "demi_coup": idx + 1,
                        "coup": (idx // 2) + 1,
                        "camp": camp,
                        "san": ply.get("san", ""),
                        "delta_wp": round(loss_w, 1),
                        "avant_wp": round(prev_wp, 1),
                        "apres_wp": round(wp, 1),
                        "eval": format_eval(ev_cp, mate),
                        "type": classe or "bascule",
                    })

                prev_wp = wp
                prev_cp = cur_cp

        tournants.sort(key=lambda x: x["delta_wp"], reverse=True)

        def _bloc(accs: list[float], cpls: list[int], cnt: dict[str, int]) -> dict:
            return {
                "precision": round(sum(accs) / len(accs), 1) if accs else None,
                "acpl": int(round(sum(cpls) / len(cpls))) if cpls else None,
                "imprecisions": cnt["imprecisions"],
                "erreurs": cnt["erreurs"],
                "gaffes": cnt["gaffes"],
            }

        res["courbe"] = courbe
        res["blancs"] = _bloc(acc_w, cpl_w, counts["blancs"])
        res["noirs"] = _bloc(acc_b, cpl_b, counts["noirs"])
        res["tournants"] = sorted(tournants[:4], key=lambda x: x["demi_coup"])

    if has_clk:
        inc = _parse_increment(time_control)
        prev_clk = {"blancs": None, "noirs": None}
        max_think = {"blancs": None, "noirs": None}
        zeitnot = {"blancs": False, "noirs": False}
        temps_par_coup: list[int | None] = []

        for idx, ply in enumerate(plies):
            camp = "blancs" if idx % 2 == 0 else "noirs"
            clk = ply.get("clk")
            spent: int | None = None
            if clk is not None:
                if clk <= 60:
                    zeitnot[camp] = True
                p_prev = prev_clk[camp]
                if p_prev is not None:
                    diff = p_prev - clk + inc
                    if diff >= 0:
                        spent = diff
                        cur_max = max_think[camp]
                        if cur_max is None or spent > cur_max["sec"]:
                            max_think[camp] = {
                                "sec": spent,
                                "coup": (idx // 2) + 1,
                                "san": ply.get("san", ""),
                            }
                prev_clk[camp] = clk
            temps_par_coup.append(spent)

        res["temps"] = {
            "par_coup": temps_par_coup,
            "plus_long": max_think,
            "zeitnot": zeitnot,
            "increment": inc,
        }

    return res


class StockfishRunner:
    """Interface UCI légère (Python stdlib) pour évaluer les parties sans [%eval]."""

    def __init__(self, depth: int = 12, threads: int = 2, hash_mb: int = 64) -> None:
        self.path = shutil.which("stockfish")
        self.depth = depth
        self.threads = threads
        self.hash_mb = hash_mb
        self.proc: subprocess.Popen[str] | None = None
        self.version = ""

    @property
    def available(self) -> bool:
        return self.path is not None

    def __enter__(self) -> StockfishRunner:
        if not self.path:
            return self
        self.proc = subprocess.Popen(
            [self.path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._send("uci")
        assert self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if line.startswith("id name "):
                self.version = line[8:].strip()
            if line == "uciok":
                break
        self._send(f"setoption name Threads value {self.threads}")
        self._send(f"setoption name Hash value {self.hash_mb}")
        self._send("isready")
        for line in self.proc.stdout:
            if line.strip() == "readyok":
                break
        return self

    def __exit__(self, *args) -> None:
        if self.proc:
            try:
                self._send("quit")
                self.proc.wait(timeout=3)
            except Exception:
                self.proc.kill()
            self.proc = None

    def _send(self, cmd: str) -> None:
        if self.proc and self.proc.stdin:
            self.proc.stdin.write(cmd + "\n")
            self.proc.stdin.flush()

    def evaluer_partie(self, plies: list[dict], max_seconds: float = 10.0) -> bool:
        """Ajoute eval/mate (point de vue Blancs) sur les plies ayant un coup UCI."""
        if not self.proc or not self.proc.stdout or not plies:
            return False
        if not all("uci" in p for p in plies):
            return False

        t0 = time.monotonic()
        self._send("ucinewgame")
        self._send("isready")
        for line in self.proc.stdout:
            if line.strip() == "readyok":
                break

        uci_moves: list[str] = []
        for idx, ply in enumerate(plies):
            if time.monotonic() - t0 > max_seconds:
                return False
            uci_moves.append(ply["uci"])
            # Après le coup d'indice idx :
            # idx pair (Blancs viennent de jouer) -> c'est aux Noirs de jouer -> score UCI inversé
            # idx impair (Noirs viennent de jouer) -> c'est aux Blancs de jouer -> score UCI direct
            black_to_move = (idx % 2 == 0)
            self._send("position startpos moves " + " ".join(uci_moves))
            self._send(f"go depth {self.depth}")
            last_kind = None
            last_val = None
            for line in self.proc.stdout:
                line = line.strip()
                if " score " in line:
                    m = re.search(r"score (cp|mate) (-?\d+)", line)
                    if m:
                        last_kind = m.group(1)
                        last_val = int(m.group(2))
                if line.startswith("bestmove"):
                    break
            if last_kind is not None and last_val is not None:
                val_white = -last_val if black_to_move else last_val
                if last_kind == "cp":
                    ply["eval"] = val_white
                else:
                    ply["mate"] = val_white
        return True


def _run_tests() -> None:
    # 1. Formules Lichess
    assert abs((win_percent(0) or 0) - 50.0) < 1e-6
    assert (win_percent(300) or 0) > 74.0
    assert (win_percent(-300) or 0) < 26.0
    assert win_percent(None, 3) == 100.0
    assert win_percent(None, -2) == 0.0
    assert abs(move_accuracy(50.0, 50.0) - 100.0) < 0.01
    assert move_accuracy(60.0, 30.0) < 30.0

    # 2. Analyse d'une partie avec gaffe noire au coup 3
    plies = [
        {"san": "e4", "eval": 20, "clk": 5400},
        {"san": "e5", "eval": 20, "clk": 5390},
        {"san": "Nf3", "eval": 25, "clk": 5370},
        {"san": "Nc6", "eval": 20, "clk": 5360},
        {"san": "Bc4", "eval": 25, "clk": 5300},
        {"san": "f6", "eval": 450, "clk": 5200},  # grosse gaffe !
    ]
    a = analyser_partie(plies, "5400+30")
    assert a is not None
    assert a["blancs"]["precision"] > 98.0
    assert a["noirs"]["gaffes"] == 1
    assert len(a["tournants"]) == 1
    assert a["tournants"][0]["san"] == "f6"
    assert a["temps"]["plus_long"]["noirs"]["san"] == "f6"

    # 3. Sans eval ni clk -> None (zéro hallucination)
    assert analyser_partie([{"san": "e4"}, {"san": "e5"}]) is None

    print("[OK] analyse.py : tous les tests passent.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_tests()
    else:
        print("Usage: python3 analyse.py --test")
