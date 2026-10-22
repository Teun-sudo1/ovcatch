PY ?= python3
VENV ?= .venv
BIN = $(VENV)/bin

.PHONY: setup test demo serve-demo data download prepare gtfs valhalla valhalla-build valhalla-serve valhalla-logs serve up web-export site serve-site deploy-site

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

valhalla:         ## Valhalla starten: Docker als die er is, anders native (pyvalhalla)
	@if command -v docker >/dev/null 2>&1; then \
		mkdir -p data/valhalla && cp -n data/raw/*.osm.pbf data/valhalla/ 2>/dev/null || true; \
		docker compose up -d valhalla; \
	else \
		[ -f data/valhalla/tiles.tar ] || scripts/valhalla_native.sh build; \
		echo "Valhalla native op :8002 (log: data/valhalla/service.log)"; \
		nohup scripts/valhalla_native.sh serve > data/valhalla/service.log 2>&1 & \
	fi

valhalla-build:   ## native: routing-tiles bouwen uit data/raw/*.osm.pbf
	scripts/valhalla_native.sh build

valhalla-serve:   ## native: Valhalla-server op :8002 (voorgrond)
	scripts/valhalla_native.sh serve

valhalla-logs:
	docker compose logs -f valhalla

serve:            ## web-app lokaal (Valhalla via localhost:8002)
	$(BIN)/python -m ovcatch serve

up:               ## alles in Docker
	docker compose up -d --build

web-export:       ## alle bushaltes/-lijnen vooraf berekenen voor de webviewer (Valhalla moet draaien)
	$(BIN)/python -m ovcatch web-export --out data/web

site:             ## viewer + geëxporteerde data samenvoegen in data/site
	rm -rf data/site && mkdir -p data/site && cp viewer/* data/site/ && cp -R data/web data/site/data

serve-site: site  ## webviewer lokaal bekijken op http://localhost:8001
	cd data/site && $(abspath $(BIN))/python -m http.server 8001

deploy-site: site ## webviewer publiceren op GitHub Pages (gh-pages-branch)
	scripts/deploy_site.sh
