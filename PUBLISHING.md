# Publishing checklist

The repository content is ready to be placed at:

```text
https://github.com/FrancYescO/alexa-room-sync
```

Before the first public release:

1. Create the repository as public with Issues enabled.
2. Use the description: `Synchronize Home Assistant areas and Alexa rooms.`
3. Add topics: `home-assistant`, `hacs`, `alexa`, `matter`,
   `custom-integration`.
4. Push the repository root, not its parent directory.
5. Confirm that both HACS validation and hassfest pass.
6. Create a full GitHub release named `v0.4.0`.
7. Add the repository as a HACS custom repository and perform a clean install.

Home Assistant 2026.3 and later can load the brand images directly from the
integration's `brand/` directory. Inclusion in the default HACS catalog may
still require satisfying any additional brand-catalog rule enforced by the
current HACS validator.
