#!/usr/bin/env bash
# Zaklada repozytorium na GitHubie, wrzuca pliki, wlacza GitHub Pages i uruchamia pierwsza aktualizacje.
# Uzycie: ./setup.sh [nazwa-repo]     (domyslnie: tibia-loot-info)
set -u
cd "$(dirname "$0")"
REPO="${1:-tibia-loot-info}"

say(){ printf '\n== %s\n' "$*"; }
die(){ printf '\nBLAD: %s\n' "$*" >&2; exit 1; }

say "1/6 Sprawdzam narzedzia"
command -v git >/dev/null 2>&1 || die "Brak programu git. Zainstaluj: https://git-scm.com/downloads i uruchom ten skrypt ponownie."
command -v gh  >/dev/null 2>&1 || die "Brak GitHub CLI (gh). Zainstaluj: https://cli.github.com (Mac: brew install gh) i uruchom ten skrypt ponownie."

say "2/6 Logowanie do GitHuba"
if ! gh auth status -h github.com >/dev/null 2>&1; then
  echo "Otworzy sie przegladarka - zaloguj sie i wpisz pokazany kod."
  gh auth login -h github.com -p https -w -s workflow || die "Logowanie nie powiodlo sie."
elif ! gh auth status -h github.com 2>&1 | grep -q "workflow"; then
  echo "Potrzebuje dodatkowego uprawnienia 'workflow' (do wrzucenia pliku harmonogramu)."
  gh auth refresh -h github.com -s workflow || die "Nie udalo sie dodac uprawnienia."
fi
gh auth setup-git >/dev/null 2>&1
OWNER="$(gh api user -q .login)" || die "Nie moge odczytac nazwy konta GitHub."
echo "Konto: $OWNER   Repozytorium: $REPO"

say "3/6 Przygotowuje repozytorium lokalnie"
[ -d .git ] || { git init -b main >/dev/null 2>&1 || { git init >/dev/null && git checkout -b main; }; }
git config user.name  >/dev/null 2>&1 || git config user.name  "$OWNER"
git config user.email >/dev/null 2>&1 || git config user.email "${OWNER}@users.noreply.github.com"
git add -A
git diff --cached --quiet || git commit -m "Tibia Loot Info" >/dev/null
git branch -M main

say "4/6 Tworze repozytorium na GitHubie (publiczne - Pages w planie Free wymaga publicznego)"
if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  echo "Repozytorium $OWNER/$REPO juz istnieje - uzywam go."
else
  gh repo create "$OWNER/$REPO" --public >/dev/null || die "Nie udalo sie utworzyc repozytorium."
fi
git remote get-url origin >/dev/null 2>&1 && git remote set-url origin "https://github.com/$OWNER/$REPO.git" \
  || git remote add origin "https://github.com/$OWNER/$REPO.git"

enable_pages(){
  gh api -X POST "repos/$OWNER/$REPO/pages" -f build_type=workflow >/dev/null 2>&1 \
  || gh api -X PUT "repos/$OWNER/$REPO/pages" -f build_type=workflow >/dev/null 2>&1
}

say "5/6 Wlaczam GitHub Pages i wysylam pliki"
enable_pages && echo "Pages wlaczone (Source: GitHub Actions)." || echo "(Pages wlacze po wyslaniu plikow)"
git push -u origin main || die "Nie udalo sie wyslac plikow (git push)."
enable_pages && echo "Pages: OK" || echo "UWAGA: nie udalo sie wlaczyc Pages automatycznie - zrob to recznie: https://github.com/$OWNER/$REPO/settings/pages -> Source: GitHub Actions"

say "6/6 Uruchamiam pierwsze generowanie danych"
sleep 5
ok=""
for i in 1 2 3 4 5 6; do
  if gh workflow run update-data.yml -R "$OWNER/$REPO" >/dev/null 2>&1; then ok=1; break; fi
  sleep 5
done
[ -n "$ok" ] && echo "Uruchomione." || echo "Nie udalo sie uruchomic z linii polecen - kliknij w zakladce Actions: Run workflow."

cat <<MSG

GOTOWE.
  Postep (pierwszy przebieg to szacunkowo kilkadziesiat minut):
    https://github.com/$OWNER/$REPO/actions
  Twoja strona (pojawi sie po zakonczeniu pierwszego przebiegu):
    https://$OWNER.github.io/$REPO/
Potem strona aktualizuje sie sama co poniedzialek.
MSG
