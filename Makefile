PY ?= python3
VENV ?= .venv
BIN = $(VENV)/bin

.PHONY: setup test demo serve-demo data download prepare gtfs valhalla valhalla-logs serve up

setup:            ## virtuele omgeving + dependencies
	$(PY) -m venv $(VENV) && $(BIN)/pip install -U pip && $(BIN)/pip install -r requirements.txt

test:             ## alle tests (gebruikt synthetische data)
	$(BIN)/python -m pytest -q

demo:             ## synthetische demo-data bouwen
	OVCATCH_CONFIG=config.demo.yaml $(BIN)/python -m ovcatch demo

serve-demo: demo  ## web-app met demo-data op http://localhost:8000
	OVCATCH_CONFIG=config.demo.yaml $(BIN)/python -m ovcatch serve

download:         ## ruwe bronnen downloaden (BAG ~7 GB!)
	$(BIN)/python -m ovcatch download

prepare:          ## BAG/CBS/OSM -> data/processed
	$(BIN)/python -m ovcatch prepare

gtfs:             ## GTFS indexeren
	$(BIN)/python -m ovcatch gtfs-index

data: download prepare gtfs  ## alles: downloaden + verwerken + GTFS

valhalla:         ## OSM-extract klaarzetten en Valhalla starten (bouwt tiles bij eerste start)
	mkdir -p data/valhalla && cp -n data/raw/*.osm.pbf data/valhalla/ 2>/dev/null || true
	docker compose up -d valhalla

valhalla-logs:
	docker compose logs -f valhalla

serve:            ## web-app lokaal (Valhalla via localhost:8002)
	$(BIN)/python -m ovcatch serve

up:               ## alles in Docker
	docker compose up -d --build
