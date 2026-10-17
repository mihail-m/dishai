.PHONY: setup dev backend frontend test districts data graph roads ndvi lst canopy bake build start

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
roads:
	$(PY) -m backend.data_prep.graph_dl --roads --out data/roads.graphml
ndvi:
	$(PY) -m backend.data_prep.ndvi                 # Sentinel-2 median NDVI -> data/ndvi.tif
lst:
	$(PY) -m backend.data_prep.lst                  # Landsat LST -> data/lst.tif
canopy:
	$(PY) -m backend.data_prep.greenery_osm         # OSM canopy fallback -> data/canopy_osm.tif
bake:
	$(PY) -m backend.cai.graph                      # data/*.graphml -> data/net.npz (what the app loads)
data: graph roads ndvi lst canopy bake

# single-process demo: build the UI, FastAPI serves it on :8000
build:
	cd frontend && npm run build

start: build
	.venv/bin/uvicorn backend.main:app --port 8000
