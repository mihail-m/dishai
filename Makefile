.PHONY: reference calibrate dem construction construction-activity construction-districts history train setup dev backend frontend test districts data graph bikegraph roads ndvi lst canopy trees bake build start

PY=.venv/bin/python

setup:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

backend:
	.venv/bin/uvicorn backend.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

# both servers; Ctrl-C stops both
dev:
	@trap 'kill 0' INT TERM; \
	.venv/bin/uvicorn backend.main:app --reload --port 8000 & \
	(cd frontend && npm run dev) & \
	wait

test:
	$(PY) -m pytest backend/tests -q

# ---- one-off data bake (outputs are committed under data/ and public/data/) ---------------------------
districts:
	$(PY) scripts/fetch_districts.py

graph:
	$(PY) -m backend.data_prep.graph_dl            # walking graph -> data/graph.graphml (large, git-ignored)
bikegraph:
	$(PY) -m backend.data_prep.graph_dl --bike --out data/graph_bike.graphml   # cycling network (git-ignored)
roads:
	$(PY) -m backend.data_prep.graph_dl --roads --out data/roads.graphml
ndvi:
	$(PY) -m backend.data_prep.ndvi                 # Sentinel-2 median NDVI -> data/ndvi.tif
lst:
	$(PY) -m backend.data_prep.lst                  # Landsat LST -> data/lst.tif
canopy:
	$(PY) -m backend.data_prep.greenery_osm         # OSM canopy fallback -> data/canopy_osm.tif
trees:
	$(PY) -m backend.data_prep.trees_sofiaplan   # Sofia tree inventory -> data/tree_crown_m2.tif (~45 min, polite sequential crawl, resumable)
bake:
	$(PY) -m backend.cai.graph                      # data/*.graphml -> data/net.npz (what the app loads)
construction-activity:
	$(PY) -m backend.data_prep.construction_activity   # Sentinel-2 bare-soil/NDVI per site -> data/construction_activity.csv (~5 min)
construction-districts:
	$(PY) -m backend.data_prep.construction_districts  # share of each district under construction -> public/data
construction:
	$(PY) -m backend.data_prep.construction   # OSM construction sites -> data/construction.tif
dem:
	$(PY) -m backend.data_prep.dem                  # Copernicus 30 m terrain -> data/dem.tif
history:
	$(PY) -m backend.data_prep.history              # Sensor.Community + Open-Meteo history -> data/history/ (~1 h, resumable)
reference:
	$(PY) -m backend.data_prep.reference_eea     # EEA hourly PM2.5/PM10 for Sofia -> data/history/reference.csv
calibrate:
	$(PY) -m backend.ml.calibrate                    # fit + evaluate the sensor calibration -> data/calibration.joblib
train:
	$(PY) -m backend.ml.train                       # fit + evaluate the PM2.5 model -> data/pm_model.joblib
data: graph bikegraph roads ndvi lst canopy trees bake

# single-process demo: build the UI, FastAPI serves it on :8000
build:
	cd frontend && npm run build

start: build
	.venv/bin/uvicorn backend.main:app --port 8000
