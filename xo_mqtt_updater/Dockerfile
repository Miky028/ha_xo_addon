FROM python:3.12-slim

# nainstalovat jq a curl (curl může být užitečný pro testy)
RUN apt-get update && apt-get install -y jq curl && rm -rf /var/lib/apt/lists/*

# Instalace závislostí
RUN pip install --no-cache-dir requests paho-mqtt ha-mqtt-discoverable

# Přidání skriptů
COPY run.sh /run.sh
COPY xo_updater.py /xo_updater.py
RUN chmod +x /run.sh

CMD [ "/run.sh" ]
