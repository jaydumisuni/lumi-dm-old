# THETECHGUY Builder → GitHub Releases

When a Builder project has a GitHub repository, the Builder can publish its signed output directly to that repository's Releases page.

## Required flow

1. Read the repository as `owner/name` or from its GitHub URL.
2. Read the token only from `GITHUB_TOKEN` or `GH_TOKEN`. Never save the token inside the project, generated application or release metadata.
3. Validate the target repository and show it to the owner before publishing.
4. Create or update the requested tag release.
5. Upload installers, portable packages and generated SHA-256 sidecars.
6. Return the release URL and exact uploaded asset list.

The reusable publisher is `scripts/publish_github_release.py`.

Example:

```powershell
$env:GITHUB_TOKEN = "<repository token>"
python scripts/publish_github_release.py `
  --repo jaydumisuni/lumi-dm `
  --tag v1.2.0 `
  --title "Lumi DM 1.2.0" `
  --notes-file RELEASE_NOTES.md `
  --replace-assets `
  --asset dist/electron/Lumi-DM-Setup-1.2.0.exe
```

The publisher creates `Lumi-DM-Setup-1.2.0.exe.sha256` automatically. This matches Lumi's update security rule: a downloaded installer is not executed unless a SHA-256 digest is available and matches.

## Builder UI standard

Use one **Release → GitHub** panel with:

- Repository URL or `owner/name`
- Tag and release title
- Release notes
- Stable or prerelease
- Draft or publish now
- Asset selection
- Validate Repository, Create Draft Release and Publish Release actions

The machine-readable contract is `assets/builder-github-release-contract.json`.
