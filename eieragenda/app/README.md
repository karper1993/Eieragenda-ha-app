Eieragenda app v35 - Gunicorn + SQLite WAL voorbereiding

Wijzigingen:
- SQLite busy_timeout 10 seconden
- SQLite WAL mode
- foreign_keys aan
- database wordt bij app-start geïnitialiseerd, niet meer op elk verzoek
- Flask fallback blijft threaded als app.py direct gestart wordt

Plaats deze app-bestanden in /share/eieragenda/app en herstart de add-on.
Voor Gunicorn zelf moet ook de add-on repo v35 worden bijgewerkt.
