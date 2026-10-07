# Tibia Loot Info – instrukcja uruchomienia

## Wersja „klik i gotowe" (zalecana)

1. **Rozpakuj** cały zip do zwykłego folderu (np. `Dokumenty\tibia-loot-info`). Nie uruchamiaj skryptu z wnętrza zipa.
2. Załóż darmowe konto na https://github.com, jeśli jeszcze go nie masz.
3. **Windows:** kliknij dwukrotnie `START.bat`.
   **Mac / Linux:** otwórz terminal w tym folderze i wpisz `./setup.sh`.
4. Skrypt sam: doinstaluje potrzebne programy (Git, GitHub CLI), otworzy przeglądarkę do zalogowania, założy publiczne repozytorium `tibia-loot-info`, wyśle pliki, włączy GitHub Pages i uruchomi pierwsze generowanie danych.
   - Gdy poprosi o logowanie: w przeglądarce zatwierdź i wpisz pokazany w oknie kod.
   - Jeśli po instalacji programów napisze, żeby uruchomić jeszcze raz – zamknij okno i kliknij `START.bat` ponownie. To normalne.
5. Na końcu dostaniesz dwa linki: do postępu (zakładka Actions) i do gotowej strony `https://TWOJ-LOGIN.github.io/tibia-loot-info/`.
6. Pierwszy przebieg trwa długo (szacunkowo kilkadziesiąt minut). Zielony „ptaszek" w Actions = strona gotowa. Później aktualizuje się sama co poniedziałek.

Inna nazwa repozytorium: `START.bat` → w PowerShellu `.\setup.ps1 -Repo moja-nazwa`, na Macu `./setup.sh moja-nazwa`.

## Boosted creature i boss

Na górze strony są dwa kafelki. Są pobierane na żywo z TibiaData przy każdym otwarciu strony, więc zawsze są aktualne (zmieniają się po server save). Kliknięcie w boosted creature dodaje ją do listy mobów. Jeśli API nie odpowie, kafelek pokazuje „niedostępne" (dla creature strona próbuje jeszcze danych lokalnych).

## Ręcznie, bez skryptu (gdyby skrypt nie działał)

1. Na github.com: **New repository** → nazwa `tibia-loot-info` → **Public** → Create.
2. Kliknij **uploading an existing file** i przeciągnij **wszystkie pliki z folderu, razem z ukrytym katalogiem `.github`** (w Eksploratorze Windows włącz Widok → Pokaż → Ukryte elementy). Jeśli przeglądarka nie przyjmie folderu `.github`, użyj GitHub Desktop.
3. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
4. Zakładka **Actions** → „Dane i GitHub Pages" → **Run workflow**.

## Gdy coś nie działa

- **Strona pokazuje 404 zaraz po zakończeniu** – odczekaj kilka minut i odśwież (Ctrl+F5).
- **W Actions czerwony krzyżyk na kroku „Generuj dane"** – kliknij w niego i zajrzyj do logu; 403 z wiki oznacza, że Fandom blokuje serwery GitHuba. Strona i tak działa, a brakujące dane pobiera z API w przeglądarce.
- **Czerwony krzyżyk na „configure-pages"** – Pages nie jest włączone: Settings → Pages → Source: GitHub Actions, potem Run workflow.
- **Prywatne repozytorium** – Pages w planie Free działa tylko w publicznych.
