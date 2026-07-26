# Publishing checklist

The repository content is prepared for:

```text
https://github.com/FrancYescO/alexa-room-sync
```

Before the first public release:

1. Create the repository as public with Issues enabled.
2. Use the description: `Synchronize Home Assistant areas and Alexa rooms.`
3. Add topics: `home-assistant`, `hacs`, `alexa`, `matter`,
   `custom-integration`.
4. Push the repository root, not its parent directory.
5. Confirm that tests, HACS validation, and hassfest pass.
6. Create a full GitHub release matching the manifest version, currently
   `v0.8.2`. A tag by itself is not sufficient for HACS release discovery.
7. Add the repository as a HACS custom repository and perform a clean install.

Suggested commands after creating the empty GitHub repository:

```bash
git remote add origin git@github.com:FrancYescO/alexa-room-sync.git
git push --set-upstream origin main --follow-tags
gh release create v0.8.2 --title "v0.8.2" --generate-notes
```

Home Assistant 2026.3 and later loads the included brand images directly from
the integration's `brand/` directory. Before requesting inclusion in the
default HACS catalog, re-check the current HACS brand validation policy.
