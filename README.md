# PopsPortal release-meldingen

Checkt elke 15 minuten de PopsPortal-shop (alleen lezen) via GitHub Actions en post nieuwe producten in Discord: titel, status (In stock / Pre-order / Back-order), prijs, PortalPoints-tip en productfoto.

Benodigde secrets (Settings → Secrets and variables → Actions): `WC_URL`, `WC_CONSUMER_KEY`, `WC_CONSUMER_SECRET`, `DISCORD_WEBHOOK_URL`.
