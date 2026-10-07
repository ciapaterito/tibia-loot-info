# Zaklada repozytorium na GitHubie, wrzuca pliki, wlacza GitHub Pages i uruchamia pierwsza aktualizacje.
param([string]$Repo = "tibia-loot-info")
Set-Location -LiteralPath $PSScriptRoot

function Say($m){ Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Die($m){ Write-Host ""; Write-Host "BLAD: $m" -ForegroundColor Red; exit 1 }
function Have($c){ return [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function RefreshPath { $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User') }
function Run { param([scriptblock]$b) $old=$ErrorActionPreference; $ErrorActionPreference='Continue'; & $b *> $null; $c=$LASTEXITCODE; $ErrorActionPreference=$old; return ($c -eq 0) }

Say "1/6 Sprawdzam narzedzia"
foreach ($t in @(@{c='git';id='Git.Git'}, @{c='gh';id='GitHub.cli'})) {
  if (-not (Have $t.c)) {
    if (Have 'winget') {
      Write-Host "Instaluje $($t.c) (moze pojawic sie okno z pytaniem o zgode - zatwierdz)..."
      winget install --id $t.id -e --silent --accept-package-agreements --accept-source-agreements
      RefreshPath
    }
    if (-not (Have $t.c)) { Die "Nie ma programu '$($t.c)'. Zamknij to okno, otworz START.bat jeszcze raz (po instalacji potrzebne jest swieze okno). Jesli dalej nie dziala, zainstaluj reczne: git-scm.com oraz cli.github.com" }
  }
}

Say "2/6 Logowanie do GitHuba"
$logged = Run { gh auth status -h github.com }
if (-not $logged) {
  Write-Host "Otworzy sie przegladarka - zaloguj sie i wpisz pokazany kod."
  gh auth login -h github.com -p https -w -s workflow
  if ($LASTEXITCODE -ne 0) { Die "Logowanie nie powiodlo sie." }
} else {
  $st = (gh auth status -h github.com 2>&1 | Out-String)
  if ($st -notmatch 'workflow') {
    Write-Host "Potrzebuje dodatkowego uprawnienia 'workflow' (do wrzucenia pliku harmonogramu)."
    gh auth refresh -h github.com -s workflow
    if ($LASTEXITCODE -ne 0) { Die "Nie udalo sie dodac uprawnienia." }
  }
}
Run { gh auth setup-git } | Out-Null
$Owner = (gh api user -q .login).Trim()
if (-not $Owner) { Die "Nie moge odczytac nazwy konta GitHub." }
Write-Host "Konto: $Owner   Repozytorium: $Repo"

Say "3/6 Przygotowuje repozytorium lokalnie"
if (-not (Test-Path .git)) {
  if (-not (Run { git init -b main })) { git init | Out-Null; git checkout -b main | Out-Null }
}
if (-not (Run { git config user.name }))  { git config user.name  $Owner }
if (-not (Run { git config user.email })) { git config user.email "$Owner@users.noreply.github.com" }
git add -A
if (-not (Run { git diff --cached --quiet })) { git commit -m "Tibia Loot Info" | Out-Null }
git branch -M main

Say "4/6 Tworze repozytorium na GitHubie (publiczne - Pages w planie Free wymaga publicznego)"
if (Run { gh repo view "$Owner/$Repo" }) { Write-Host "Repozytorium $Owner/$Repo juz istnieje - uzywam go." }
else {
  if (-not (Run { gh repo create "$Owner/$Repo" --public })) { Die "Nie udalo sie utworzyc repozytorium." }
}
$url = "https://github.com/$Owner/$Repo.git"
if (Run { git remote get-url origin }) { git remote set-url origin $url } else { git remote add origin $url }

function EnablePages {
  if (Run { gh api -X POST "repos/$Owner/$Repo/pages" -f build_type=workflow }) { return $true }
  return (Run { gh api -X PUT "repos/$Owner/$Repo/pages" -f build_type=workflow })
}

Say "5/6 Wlaczam GitHub Pages i wysylam pliki"
if (EnablePages) { Write-Host "Pages wlaczone (Source: GitHub Actions)." } else { Write-Host "(Pages wlacze po wyslaniu plikow)" }
git push -u origin main
if ($LASTEXITCODE -ne 0) { Die "Nie udalo sie wyslac plikow (git push)." }
if (EnablePages) { Write-Host "Pages: OK" } else { Write-Host "UWAGA: nie udalo sie wlaczyc Pages automatycznie - zrob to recznie: https://github.com/$Owner/$Repo/settings/pages -> Source: GitHub Actions" -ForegroundColor Yellow }

Say "6/6 Uruchamiam pierwsze generowanie danych"
Start-Sleep -Seconds 5
$ok = $false
for ($i=0; $i -lt 6 -and -not $ok; $i++) {
  if (Run { gh workflow run update-data.yml -R "$Owner/$Repo" }) { $ok = $true } else { Start-Sleep -Seconds 5 }
}
if ($ok) { Write-Host "Uruchomione." } else { Write-Host "Nie udalo sie uruchomic z linii polecen - kliknij w zakladce Actions: Run workflow." -ForegroundColor Yellow }

Write-Host ""
Write-Host "GOTOWE." -ForegroundColor Green
Write-Host "  Postep (pierwszy przebieg to szacunkowo kilkadziesiat minut):"
Write-Host "    https://github.com/$Owner/$Repo/actions"
Write-Host "  Twoja strona (pojawi sie po zakonczeniu pierwszego przebiegu):"
Write-Host "    https://$Owner.github.io/$Repo/"
Write-Host "Potem strona aktualizuje sie sama co poniedzialek."
