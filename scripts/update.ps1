param(
    [ValidateSet('0', '1', '2', '3')]
    [string]$Channel = '0',
    [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$git = Join-Path $projectRoot 'git\cmd\git.exe'
$launcher = Join-Path $projectRoot 'WWA.exe'
$updateLock = $null

function Invoke-Git {
    & $git @args
    if ($LASTEXITCODE -ne 0) {
        throw "git $($args[0]) failed (exit $LASTEXITCODE)."
    }
}

function Test-GitOperation {
    foreach ($name in 'rebase-merge', 'rebase-apply', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'sequencer') {
        if (Test-Path -LiteralPath (Join-Path $gitDir $name)) {
            return $true
        }
    }
    return $false
}

function Restore-UpdateStash([string]$Stash) {
    $clean = @(Invoke-Git status --porcelain=v1 --untracked-files=normal).Count -eq 0
    if ((Test-GitOperation) -or -not $clean) {
        throw "The worktree is busy. Your edits are saved in stash $Stash."
    }

    # Restore staged changes too. A failed apply leaves both the stash and any conflicts in place.
    & $git stash apply --index $Stash
    if ($LASTEXITCODE -ne 0) {
        throw "Could not restore local edits automatically. Stash $Stash was kept. Inspect git status before retrying."
    }
    $selector = Invoke-Git stash list '--format=%H %gd' |
        Where-Object { $_ -like "$Stash *" } |
        Select-Object -First 1
    if ($selector) {
        Invoke-Git stash drop ($selector.Split(' ', 2)[1])
    }
}

try {
    if (-not (Test-Path -LiteralPath $git -PathType Leaf)) {
        $git = (Get-Command git.exe -CommandType Application | Select-Object -First 1).Source
    }
    if (-not $NoRestart -and -not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
        throw "WWA launcher not found at $launcher."
    }

    Set-Location -LiteralPath $projectRoot
    $worktree = [IO.Path]::GetFullPath((Invoke-Git rev-parse --show-toplevel))
    if ($worktree -ne [IO.Path]::GetFullPath($projectRoot)) {
        throw 'The WWA folder must be the root of a Git checkout.'
    }
    $gitDir = Invoke-Git rev-parse --absolute-git-dir
    $lockPath = Join-Path $gitDir 'wwa-updater.lock'
    $updateLock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')

    if (Test-GitOperation) {
        throw 'A Git operation is in progress. Finish it before updating WWA.'
    }

    $branch = Invoke-Git symbolic-ref --quiet --short HEAD
    $remote = @{
        '0' = 'origin'
        '1' = 'https://github.com/wakening/WutheringWavesAssistant.git'
        '2' = 'https://cnb.cool/github.wakening/WutheringWavesAssistant.git'
        '3' = 'https://atomgit.com/unwaking/WutheringWavesAssistant.git'
    }[$Channel]
    if ($Channel -ne '0') {
        $originUrl = & $git remote get-url origin 2>$null
        if ($LASTEXITCODE -eq 0 -and $remote -eq $originUrl) {
            $remote = 'origin'
        }
    }

    Write-Host "Fetching upstream main for $branch..."
    Invoke-Git fetch --no-tags $remote refs/heads/main
    $upstream = Invoke-Git rev-parse --verify 'FETCH_HEAD^{commit}'
    # Require shared history before replaying commits onto the selected source.
    $null = Invoke-Git merge-base HEAD $upstream
    & $git merge-base --is-ancestor $upstream HEAD
    if ($LASTEXITCODE -eq 0) {
        Write-Host "$branch already contains upstream main."
    } elseif ($LASTEXITCODE -eq 1) {
        $savedStash = $null
        try {
            if (@(Invoke-Git status --porcelain=v1 --untracked-files=normal).Count -gt 0) {
                $oldStash = & $git rev-parse --verify --quiet refs/stash
                Invoke-Git stash push --include-untracked --message "WWA update $branch $([guid]::NewGuid())"
                $newStash = & $git rev-parse --verify --quiet refs/stash
                if ($newStash -and $oldStash -ne $newStash) {
                    $savedStash = $newStash
                }
                if (@(Invoke-Git status --porcelain=v1 --untracked-files=normal).Count -gt 0) {
                    throw 'Git could not save all changes. Check the worktree and any submodule changes.'
                }
            }

            if ($branch -eq 'main') {
                Invoke-Git merge --ff-only $upstream
            } else {
                & $git -c rebase.updateRefs=false rebase --rebase-merges --no-autostash $upstream
                if ($LASTEXITCODE -ne 0) {
                    if (Test-GitOperation) {
                        Invoke-Git rebase --abort
                    }
                    throw "Could not rebase $branch onto upstream main. Review the Git output above."
                }
            }
        } finally {
            if ($savedStash) {
                Restore-UpdateStash $savedStash
            }
        }
    } else {
        throw 'Could not compare the current branch with upstream main.'
    }

    Write-Host "WWA update completed on $branch."
    if (-not $NoRestart) {
        Start-Process -FilePath $launcher -WorkingDirectory $projectRoot
    }
} catch {
    [Console]::Error.WriteLine("[ERROR] $($_.Exception.Message)")
    exit 1
} finally {
    if ($updateLock) {
        $updateLock.Dispose()
    }
}
