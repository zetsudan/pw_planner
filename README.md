# Maintenance Email Generator — Brackets Subject Edition

Subject format:
```
Planned Network Maintenance – [Jira Ref] [PoP / Equipment] – [Start Date - End Date, Start Time - End Time UTC+0]
```

Other features:
- CID/Label TSV parser (multi-file cumulative, header-friendly)
- WL/WLP → CID only; OC/3POC → `CID (Label)`; skip `OC-900001*`, ignore noise CIDs
- UTC math: input offset → **UTC+0** in subject and body
- Live preview
- PoP / Equipment / Line (free)
- Purpose presets + custom purpose
- Downtime auto-calc; if `0` then “No service interruption is anticipated.”

Run with Docker:
```bash
docker build -t maintenance-email:latest .
docker run -d --name maintenance-email -p 8000:8000 --restart unless-stopped maintenance-email:latest
# open http://localhost:8000
```
# pw_planner
