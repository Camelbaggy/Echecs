#!/usr/bin/env python3
"""Test UI mobile Playwright (390x844) du Dashboard Échecs sur jeu d'essai local."""

from __future__ import annotations

import http.server
import os
import shutil
import socketserver
import sys
import tempfile
import threading

from playwright.sync_api import sync_playwright

import run_pipeline


def main() -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with tempfile.TemporaryDirectory() as tmp:
        run_pipeline.generer_demo(tmp)
        shutil.copy(os.path.join(repo_root, "index.html"), os.path.join(tmp, "index.html"))

        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=tmp, **kwargs)

            def log_message(self, *args):
                pass

        httpd = socketserver.TCPServer(("127.0.0.1", 0), QuietHandler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))
            page.on("console", lambda m: errors.append(f"CONSOLE {m.type}: {m.text}") if m.type == "error" else None)

            def _route(route):
                if route.request.url.startswith(f"http://127.0.0.1:{port}"):
                    route.continue_()
                elif "flagcdn.com" in route.request.url:
                    route.fulfill(status=200, content_type="image/png", body=b"")
                else:
                    route.fulfill(status=200, content_type="application/json", body="{}")

            page.route("**/*", _route)

            def check(label: str) -> None:
                ov = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
                assert not ov, f"Débordement horizontal sur {label}"
                print(f"  [OK] {label}")

            page.goto(f"http://127.0.0.1:{port}/index.html", wait_until="networkidle")
            page.wait_for_selector("#view-accueil .fra-card")
            check("Accueil")

            # Fiche de partie
            page.click("#view-accueil .fra-card")
            page.wait_for_selector("#game-sheet.open .board-grid")
            assert page.locator("#game-sheet .sq").count() == 64
            check("Fiche de partie · Échiquier")

            page.click("#mv-prev")
            page.click("#mv-flip")
            page.click("#mv-first")
            page.click("#mv-last")

            page.click('#game-tabs [data-gtab="analyse"]')
            page.wait_for_selector("#eval-chart")
            check("Fiche de partie · Analyse")

            page.click('#game-tabs [data-gtab="coups"]')
            page.wait_for_selector(".mv-btn")
            page.click(".mv-btn >> nth=0")
            page.wait_for_selector("#game-sheet .board-grid")
            check("Fiche de partie · Coups -> Échiquier")

            page.click('#game-sheet [data-close="game-sheet"]')

            # Vue Tournoi + Sélecteur de ronde
            page.click('[data-open-tour="sinq26"]')
            page.wait_for_selector("#view-tournoi .game-row")
            check("Vue Tournoi")
            page.click("#ronde-btn")
            page.wait_for_selector("#ronde-modal.open")
            page.click('#ronde-modal [data-close="ronde-modal"]')

            # Fiche Joueur
            page.click('#view-tournoi [data-open-player="12573981"]')
            page.wait_for_selector("#player-sheet.open")
            assert "Firouzja" in (page.text_content("#player-sheet-title") or "")
            check("Fiche Joueur")
            page.click('#player-sheet [data-close="player-sheet"]')

            # Classement FIDE mensuel
            page.click("#menu-btn")
            page.wait_for_selector("#menu-modal.open")
            page.click('[data-nav="fide"]')
            page.wait_for_selector("#view-fide table")
            check("Classement FIDE · Monde")
            page.click('[data-fcat="france"]')
            page.click('[data-fcad="blitz"]')
            check("Classement FIDE · France Blitz")

            browser.close()

        httpd.shutdown()
        assert not errors, f"Erreurs détectées : {errors}"
        print("[OK] tools/test_ui.py : 0 erreur console, 0 erreur page, 0 débordement horizontal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
