FROM python:3.12-slim

# Instalace závislostí
RUN pip install --no-cache-dir requests paho-mqtt

# Přidání skriptů
COPY run.sh /run.sh
COPY xo_updater.py /xo_updater.py
RUN chmod +x /run.sh

CMD [ "/run.sh" ]
